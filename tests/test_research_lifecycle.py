import copy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from spxlab.contracts import Clock,ContractError,digest,file_hash,stamp,validate_plan
from spxlab.events import atomic_json,read_events
from spxlab.frozen import freeze_run,replay_frozen,settle_frozen,verify_snapshot
from spxlab.research import ResearchJournal,replay_research
from spxlab.evaluation import compare_rows,location_attribution,sample_crps
from spxlab.policy import select_candidate
from spxlab.quotes import ResearchMarketState
from spxlab.valuation import value_candidates
from test_research_core import plan,batch,model,samples,spec,ASOF,TARGET,forecast

ROOT=Path(__file__).resolve().parents[1]


def completed_run(directory):
    p=plan()
    p['strategies']=[{'id':'DV1','selector':'DV1','timing':'FIRST_TRIGGER','gate':'NONE'},
                     {'id':'CHEAP','selector':'CHEAP_U','timing':'FIRST_TRIGGER','gate':'NONE'},
                     {'id':'SOURCE_GATE','selector':'FORECAST','timing':'FIXED','gate':'G0'}]
    p=freeze_run(p,directory,ROOT)
    j=ResearchJournal(directory/'events.sqlite',p)
    j.append('RESEARCH_PLAN',{'plan_hash':digest(p)},clock_epoch='epoch1',mono=1,utc=ASOF)
    j.append('SYNTHETIC_FRAME',{'batch':batch(),'distribution':model(),'scheduled_at':ASOF},
             clock_epoch='epoch1',mono=10_000_000_000,utc=ASOF,generation=1)
    n=j.store.db.execute('SELECT MAX(seq) FROM events').fetchone()[0]+1
    b=batch(n,11_100_000_000)
    j.append('SYNTHETIC_FRAME',{'batch':b},clock_epoch='epoch1',mono=11_100_000_000,utc='2026-09-16T14:05:01.1+00:00',generation=1)
    j.append('TIMER',{},clock_epoch='epoch1',mono=20_000_000_000_000,utc=TARGET,generation=1)
    result=j.save(directory);j.store.close()
    return result


class LifecycleTests(unittest.TestCase):
    def test_A14_A15_frozen_replay_settlement_revision_and_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'run';result=completed_run(run)
            self.assertEqual(result['books']['DV1']['status'],'ASSUMED_FILLED')
            self.assertEqual(result['books']['SOURCE_GATE']['status'],'CLOSED_UNKNOWN')
            before=file_hash(run/'events.sqlite'),file_hash(run/'decision.json')
            proof=replay_frozen(run,root/'proof')
            self.assertTrue(proof['trace_match'])
            self.assertIn('/implementation/',proof['loaded_engine'])
            asset=root/'synthetic.txt';asset.write_text('SYNTHETIC FIXTURE: 7604')
            evidence={'target_session':'2026-09-16','series':'SPXW_PM','value':'7604','status':'SYNTHETIC_FIXTURE',
                      'source_url':'https://example.test/fixture','asset_path':str(asset),'asset_sha256':file_hash(asset),
                      'retrieved_at':'2026-09-16T20:01:00+00:00','reviewed_by':'Fixture','supersedes':None}
            a=settle_frozen(run,evidence);b=settle_frozen(run,evidence)
            self.assertEqual(a['revision_id'],b['revision_id']);self.assertTrue(b['idempotent'])
            self.assertEqual(a['result']['books']['DV1']['net_pnl_estimated_fees_usd'],'1900')
            self.assertIsNone(a['result']['books']['SOURCE_GATE']['net_pnl_estimated_fees_usd'])
            corrected={**evidence,'value':'7605','supersedes':a['revision_id']}
            c=settle_frozen(run,corrected);old=settle_frozen(run,evidence)
            self.assertEqual(old['latest_revision_id'],c['revision_id'])
            self.assertEqual(len(list(read_events(run/'settlements-v2.sqlite'))),2)
            self.assertEqual(before,(file_hash(run/'events.sqlite'),file_hash(run/'decision.json')))
            with self.assertRaises(ValueError):settle_frozen(run,{**corrected,'retrieved_at':ASOF})
            path=run/'implementation/src/spxlab/engine.py';path.write_text(path.read_text()+'\n# changed\n')
            with self.assertRaises(ValueError):verify_snapshot(run)

    def test_A12_restart_recovers_committed_intent_without_retry(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);p=validate_plan(plan());atomic_json(run/'plan.json',p)
            j=ResearchJournal(run/'events.sqlite',p)
            j.append('RESEARCH_PLAN',{'plan_hash':digest(p)},clock_epoch='epoch1',mono=1,utc=ASOF)
            j.append('SYNTHETIC_FRAME',{'batch':batch(),'distribution':model(),'scheduled_at':ASOF},
                     clock_epoch='epoch1',mono=10_000_000_000,utc=ASOF,generation=1)
            j.store.close() # crash before writing decision report
            _,engine=replay_research(run,check_result=False)
            j=ResearchJournal(run/'events.sqlite',p,engine=engine)
            j.append('RESTART',{},clock_epoch='epoch2',mono=5,utc='2026-09-16T14:06:00+00:00')
            j.save(run);j.store.close()
            _,engine=replay_research(run)
            self.assertEqual(engine.books['DV1']['intent_count'],1)
            self.assertEqual(engine.books['DV1']['status'],'FILL_UNKNOWN')

    def test_A13_regressing_clock_rejected_and_no_uncommitted_save(self):
        with tempfile.TemporaryDirectory() as d:
            j=ResearchJournal(Path(d)/'events.sqlite',plan())
            j.append('RESEARCH_PLAN',{'plan_hash':digest(j.engine.plan)},clock_epoch='epoch1',mono=100,utc=ASOF)
            with self.assertRaises(ContractError):j.append('TIMER',{},clock_epoch='epoch1',mono=99,utc=ASOF)
            with self.assertRaises(ContractError):j.save(d)
            self.assertEqual(len(list(read_events(Path(d)/'events.sqlite'))),1)
            j.store.close()

    def test_A08_stale_request_after_cancellation_and_partial_spot_baseline(self):
        s=ResearchMarketState()
        def event(kind,p,seq=1):
            return {'event_type':kind,'payload':p,'seq':seq,'generation':1,'monotonic_ns':100,'recorded_at':ASOF}
        s.apply(event('CONNECTED',{}));s.apply(event('SUBSCRIBED',{'con_id':10,'req_id':20}))
        s.apply(event('FIELD',{'con_id':10,'req_id':19,'field':'bid','value':4}))
        self.assertNotIn('10',s.fields)
        s.apply(event('UNSUBSCRIBED',{'con_id':10}))
        s.apply(event('FIELD',{'con_id':10,'req_id':20,'field':'bid','value':4}))
        self.assertNotIn('10',s.fields)
        b=batch();b['universe_issues']=['FORECAST_CENTER_MISSING']
        v=value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')
        self.assertEqual(v['state'],'UNKNOWN')
        s={'selector':'SPOT','gate':'NONE'}
        self.assertEqual(select_candidate(s,v,None,'SYNTHETIC_REPLAY_V1')[0]['center'],7600)
        s['selector']='DV1'
        self.assertIsNone(select_candidate(s,v,None,'SYNTHETIC_REPLAY_V1')[0])

    def test_A17_A18_daily_missingness_matching_and_attribution(self):
        study={'schema_version':2,'study_id':'fixture','classification':'SYNTHETIC_FIXTURE','planned_sessions':['2026-09-15','2026-09-16'],
               'strategies':['DV','BASE'],'training_sessions':[],'purpose':'DESCRIPTIVE',
               'primary_comparison':{'candidate':'DV','baseline':'BASE','require_matched_valuation':True},
               'inference':{'min_independent_days':20,'block_length':2,'bootstrap_repeats':1000,'seed':1,
                            'economic_delta_usd':None,'precision_usd':None}}
        rows=[{'session':'2026-09-15','strategy_id':'DV','net_pnl_usd':'-10','valuation_hash':'a'},
              {'session':'2026-09-15','strategy_id':'BASE','net_pnl_usd':'5','valuation_hash':'b'}]
        r=compare_rows(study,rows)
        self.assertEqual(r['unmatched_pair_days'],['2026-09-15'])
        self.assertEqual(r['missing_pair_days'],['2026-09-16'])
        self.assertIsNone(r['strategy_summaries']['DV']['maximum_drawdown_usd'])
        self.assertEqual(r['strategy_summaries']['DV']['observed_subset_sum_usd'],'-10')
        self.assertIsNone(r['interval_95_usd'])
        with self.assertRaises(ContractError):compare_rows(study,rows+rows[:1])
        d=samples();d['samples']=[{'value':'7580','weight':'.25'},{'value':'7604','weight':'.5'},{'value':'7630','weight':'.25'}]
        r=location_attribution(d,7600,7600,7605,25,'2','6')
        parts=[r[k] for k in ('location_structure_interaction_points','relocated_shape_points','price_cost_points')]
        self.assertEqual(sum(map(Decimal,parts)),Decimal(r['total_edge_difference_points']))
        xs=[(Decimal(x['value']),Decimal(x['weight'])) for x in d['samples']]
        direct=sum(w*abs(x-7600) for x,w in xs)-sum(w*v*abs(x-y) for x,w in xs for y,v in xs)/2
        self.assertEqual(Decimal(sample_crps(d,7600)),direct)


if __name__=='__main__':unittest.main()
