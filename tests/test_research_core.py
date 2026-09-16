import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from spxlab.calendar import VERSION, session_schedule, contract_session_matches
from spxlab.contracts import Clock, ContractError, digest, validate_plan
from spxlab.dataset import dataset_check, build_residual_distribution
from spxlab.distribution import butterfly, expected_payoff, validate_distribution
from spxlab.forecast import ForecastBook, normalize_forecast
from spxlab.policy import decide, new_book, apply_book_event
from spxlab.research import ResearchEngine, ResearchJournal, replay_research
from spxlab.shadow import shadow_step
from spxlab.valuation import value_candidates

ASOF='2026-09-16T14:05:00+00:00'
TARGET='2026-09-16T20:00:00+00:00'


def plan(mode='SYNTHETIC_REPLAY_V1'):
    return {'schema_version':2,'run_id':'synthetic-round2','mode':mode,'session':'2026-09-16',
            'calendar_version':VERSION,'strategies':[{'id':'DV1','selector':'DV1','timing':'FIRST_TRIGGER','gate':'NONE'}],
            'execution':{'version':'FIXED_LIMIT_SHADOW_V1','quantity':1,'width':25,'budget_points':'6.25',
                         'latency_seconds':1,'ttl_seconds':10},
            'valuation':{'min_edge_points':'0','allowed_uncertainty':['MODEL_ENVELOPE','IDENTIFICATION_BOUND'],
                         'max_conditioning_age_seconds':30},
            'decision_grid_seconds':30,'deadline_tolerance_ms':250,'max_option_subscriptions':40,
            'fees':{'kind':'IBKR_PUBLIC_CUSTOMER_SCENARIO','version':'FEE_FIXTURE',
                    'regulatory_reserve_per_contract_usd':'.05'},'fixed_costs':{'status':'UNKNOWN'}}


def samples(value='7604'):
    return {'schema_version':2,'model_version':'SYNTHETIC_ONE_POINT','measure':'ASSUMED',
            'representation':'WEIGHTED_SAMPLES','target_at':TARGET,
            'information_cutoff':ASOF,'conditioning_as_of':ASOF,'computed_at':ASOF,'available_at':ASOF,
            'training_cutoff':'2026-09-15T22:00:00+00:00','calibration_status':'SYNTHETIC',
            'provenance':{'classification':'SYNTHETIC_FIXTURE'},'samples':[{'value':value,'weight':'1'}]}


def model():
    s=samples()
    return {**s,'representation':'SCENARIO_SET','scenarios':[samples(),samples()]}


def batch(seq=10,mono=10_000_000_000):
    rows=[];universe=[]
    for k,d in ((7595,'1'),(7600,'2'),(7605,'6'),(7610,'5')):
        c={'candidate_id':f'C{k}_W25','center':k,'width':25,
           'roles':['SPOT'] if k==7600 else ['FORECAST'] if k==7605 else ['FORECAST_NEIGHBOR']}
        universe.append(c)
        refs=[]
        for i,side in enumerate(('ask','bid','ask')):
            refs.append({'con_id':str(k-25+i*25), 'side_confirmation_refs':{side:{'seq':seq,'mono':mono,'generation':1}}})
        rows.append({**c,'snapshot_seq':seq,'generation':1,'clock_epoch':'epoch1','as_of':ASOF,
                     'con_ids':[str(k-25),str(k),str(k+25)],'reasons':[], 'debit_points':d,
                     'fees_usd':'0','fee_version':'FEE_FIXTURE','quote_refs':refs})
    return {'schema_version':2,'universe':universe,'rows':rows,'as_of':ASOF,'target_at':TARGET,
            'snapshot_seq':seq,'classification':'SYNTHETIC_FIXTURE'}


def spec():
    p=plan()
    return {**p['valuation'],'budget_points':p['execution']['budget_points']}


def forecast(fid='f1'):
    return {'schema_version':2,'forecast_id':fid,'source_product_id':'TEST','model_version':'TEST',
            'target_session':'2026-09-16','target_at':TARGET,'series':'SPXW_PM','statistic_type':'median',
            'median':7604,'gamma':'UNKNOWN','first_seen_at':'2026-09-16T13:55:00+00:00',
            'validated_at':'2026-09-16T13:56:00+00:00','raw_assets':[{'sha256':'a'*64,
            'received_at':'2026-09-16T13:55:01+00:00','source_url':'https://example.test/synthetic'}],
            'status':'VALIDATED','reviewed_by':'Synthetic reviewer','supersedes':None}


class ValueTests(unittest.TestCase):
    def test_quote_structure_and_evaluation_time_are_bound(self):
        for changes in ({'center':7700,'width':50}, {'as_of':'2026-09-16T13:05:00+00:00'}):
            b=batch();b['rows'][1].update(changes)
            with self.assertRaises(ContractError):value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')
        b=batch();b['rows'][1].update(center='7600.0',width='25.00',as_of='2026-09-16T10:05:00-04:00')
        self.assertEqual(value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')['selected'],'C7600_W25')

    def test_A01_value_choice_not_cheapest_or_nearest(self):
        v=value_candidates(model(),batch(),spec(),'SYNTHETIC_REPLAY_V1')
        self.assertEqual(v['selected'],'C7600_W25')
        self.assertEqual(v['cash_value_points'],'0')
        self.assertEqual(v['coverage']['planned'],4)

    def test_A02_cash_threshold_and_budget(self):
        m=model();m['scenarios']=[samples('8000')]
        self.assertEqual(value_candidates(m,batch(),spec(),'SYNTHETIC_REPLAY_V1')['state'],'ABSTAIN')
        b=batch()
        for q in b['rows']:q['debit_points']='7'
        self.assertEqual(value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')['state'],'ABSTAIN')
        self.assertEqual(value_candidates(model(),batch(),{**spec(),'min_edge_points':'19'},'SYNTHETIC_REPLAY_V1')['state'],'ABSTAIN')

    def test_A03_interval_is_unidentified(self):
        d={**samples(),'representation':'PARTITION_MASS_BOUNDS',
           'boundary_convention':'LEFT_CLOSED_RIGHT_OPEN_CLOSURE_BOUNDS',
           'bins':[{'lo':None,'hi':'7575','mass':'.25'},{'lo':'7575','hi':'7633','mass':'.5'},
                   {'lo':'7633','hi':None,'mass':'.25'}]}
        self.assertEqual(expected_payoff(d,7605,25)['lower'],0)
        self.assertEqual(expected_payoff(d,7605,25)['upper'],Decimal('12.5'))
        self.assertEqual(value_candidates(d,batch(),spec(),'SYNTHETIC_REPLAY_V1')['state'],'UNKNOWN')

    def test_A04_atoms_weights_tail_and_payoff(self):
        for value in range(7550,7660):
            self.assertEqual(butterfly(value,7600,25),max(value-7575,0)-2*max(value-7600,0)+max(value-7625,0))
        for weights in (['-.1','1.1'],['.4','.4'],['NaN','1']):
            d=samples();d['samples']=[{'value':'7600','weight':x} for x in weights]
            with self.assertRaises(ContractError):validate_distribution(d)
        d={**samples(),'representation':'PARTITION_MASS_BOUNDS',
           'boundary_convention':'LEFT_CLOSED_RIGHT_OPEN_CLOSURE_BOUNDS','bins':[{'lo':0,'hi':10,'mass':'1'}]}
        with self.assertRaises(ContractError):validate_distribution(d)

    def test_A08_missing_candidate_never_shrinks_universe(self):
        b=batch();b['rows'].pop(0)
        v=value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')
        self.assertEqual(v['state'],'UNKNOWN');self.assertIsNone(v['selected'])
        self.assertEqual(len(v['rows']),4)
        b=batch();b['universe']=[];b['rows']=[]
        self.assertEqual(value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')['state'],'UNKNOWN')

    def test_mode_and_information_clock_separation(self):
        with self.assertRaises(ContractError):value_candidates(model(),batch(),spec(),'VALUE_RESEARCH_SHADOW_V1')
        b=batch();b['classification']='MARKET_OBSERVATION'
        v=value_candidates(model(),b,spec(),'OBSERVE_ONLY_V1')
        self.assertIn('NOT_REAL_WORLD_ESTIMATE',v['rows'][0]['reasons'])
        b=batch();b['as_of']='2026-09-16T15:05:00+00:00'
        for q in b['rows']:q['as_of']=b['as_of']
        v=value_candidates(model(),b,spec(),'SYNTHETIC_REPLAY_V1')
        self.assertIn('STALE_INFORMATION_VALUATION',v['rows'][0]['reasons'])


class SourceAndDataTests(unittest.TestCase):
    def test_A06_late_asset_and_retraction(self):
        f=forecast();f['raw_assets'][0]['received_at']='2026-09-16T14:10:00+00:00'
        self.assertEqual(normalize_forecast(f)['available_at'],'2026-09-16T14:10:00+00:00')
        f['available_at']=ASOF
        with self.assertRaises(ContractError):normalize_forecast(f)
        book=ForecastBook();book.add(forecast())
        f2=forecast('f2');f2.update(status='RETRACTED',supersedes='f1',validated_at='2026-09-16T14:01:00+00:00')
        book.add(f2)
        self.assertEqual(book.latest('TEST',TARGET,ASOF)['status'],'RETRACTED')
        self.assertEqual(book.latest('TEST',TARGET,'2026-09-16T14:00:00+00:00')['forecast_id'],'f1')

    def test_A06_ambiguous_source_cannot_become_known_gate_abstention(self):
        from spxlab.policy import select_candidate
        f=normalize_forecast(forecast());f.update(status='AMBIGUOUS',gamma='SHORT')
        v=value_candidates(model(),batch(),spec(),'SYNTHETIC_REPLAY_V1')
        selected,state,reasons=select_candidate({'gate':'G0','selector':'FORECAST'},v,f,'SYNTHETIC_REPLAY_V1')
        self.assertIsNone(selected);self.assertEqual(state,'UNKNOWN');self.assertIn('SOURCE_AMBIGUOUS',reasons)

    def test_A07_mature_labels_day_weighting_and_holdout(self):
        rows=[]
        for day,n in (('2026-09-14',3),('2026-09-15',1)):
            for i in range(n):
                rows.append({'row_id':day+str(i),'session':day,'classification':'SYNTHETIC_FIXTURE',
                    'anchor_kind':'FORECAST','time_bucket':'10:05','anchor':'7600','scale':'1','outcome':'7605',
                    'decision_at':day+'T14:05:00+00:00','feature_available_at':day+'T14:04:00+00:00',
                    'target_at':day+'T20:00:00+00:00','outcome_available_at':day+'T21:00:00+00:00','source_hash':'b'*64})
        m={'schema_version':2,'training_cutoff':'2026-09-15T22:00:00+00:00','evaluation_sessions':['2026-09-16'],
           'anchor_kind':'FORECAST','time_bucket':'10:05','min_independent_days':2,
           'allowed_classifications':['SYNTHETIC_FIXTURE'],'rows':rows}
        self.assertEqual(dataset_check(m)['independent_days'],2)
        d=build_residual_distribution(m,{'anchor':'7604','scale':'1','as_of':ASOF,'available_at':ASOF,
                    'target_at':TARGET,'time_bucket':'10:05','anchor_kind':'FORECAST'})
        self.assertEqual(sum(Decimal(x['weight']) for x in d['samples'][:3]),Decimal('.5'))
        self.assertEqual(expected_payoff(d,7609,25)['point'],25)
        m['evaluation_sessions'].append('2026-09-15')
        self.assertEqual(dataset_check(m)['status'],'DATA_NOT_READY')
        m['rows'][0]['outcome_available_at']='2026-09-14T19:00:00+00:00'
        self.assertIn('LABEL_BEFORE_MATURITY',dataset_check(m)['excluded_rows'][0]['reasons'])


class ExecutionTests(unittest.TestCase):
    def setup_book(self):
        p=validate_plan(plan());s=p['strategies'][0];b=new_book(s);clock=Clock(10,10_000_000_000,ASOF,'epoch1')
        events=decide(s,b,value_candidates(model(),batch(),spec(),p['mode']),None,p,clock,ASOF)
        for kind,payload in events:apply_book_event(b,kind,payload)
        return p,b

    def test_A09_fixed_limit_not_budget(self):
        _,b=self.setup_book();q=batch(11,11_100_000_000)['rows'][1];q['debit_points']='2.5'
        self.assertIsNone(shadow_step(b,q,Clock(11,11_100_000_000,'2026-09-16T14:05:01.1+00:00','epoch1')))
        q['debit_points']='1.9'
        self.assertEqual(shadow_step(b,q,Clock(11,11_100_000_000,'2026-09-16T14:05:01.1+00:00','epoch1'))['status'],'ASSUMED_FILLED')

    def test_A10_cache_ttl_and_epoch(self):
        _,b=self.setup_book()
        self.assertIsNone(shadow_step(b,batch()['rows'][1],Clock(11,11_000_000_000,'2026-09-16T14:05:01+00:00','epoch1')))
        self.assertEqual(shadow_step(b,None,Clock(12,20_000_000_000,'2026-09-16T14:05:10+00:00','epoch1'))['status'],'FILL_UNKNOWN')
        _,b=self.setup_book()
        b['last_shadow_check_ns']=19_000_000_000
        self.assertEqual(shadow_step(b,None,Clock(12,20_000_000_000,'2026-09-16T14:05:10+00:00','epoch1'))['status'],'EXPIRED_NO_FILL')
        self.assertEqual(shadow_step(b,None,Clock(12,20_000_000_000,'2026-09-16T14:05:10+00:00','epoch2'))['reason'],'CLOCK_EPOCH_CHANGED')

    def test_A11_continuous_unfilled_vs_missing_observation(self):
        _,b=self.setup_book()
        for second in range(11,20):
            q=batch(11+second,second*1_000_000_000)['rows'][1];q['debit_points']='3'
            self.assertIsNone(shadow_step(b,q,Clock(11+second,second*1_000_000_000,ASOF,'epoch1')))
        self.assertEqual(shadow_step(b,None,Clock(40,20_000_000_000,ASOF,'epoch1'))['status'],'EXPIRED_NO_FILL')
        _,b=self.setup_book()
        self.assertEqual(shadow_step(b,None,Clock(40,20_000_000_000,ASOF,'epoch1'))['status'],'FILL_UNKNOWN')

    def test_A12_committed_single_intent_and_observe_no_intent(self):
        p,b=self.setup_book()
        self.assertEqual(decide(p['strategies'][0],b,value_candidates(model(),batch(),spec(),p['mode']),None,p,
                               Clock(11,11_000_000_000,ASOF,'epoch1'),ASOF),[])
        with self.assertRaises(ContractError):apply_book_event(b,'INTENT',b['intent'])
        p=validate_plan(plan('OBSERVE_ONLY_V1'));s=p['strategies'][0]
        events=decide(s,new_book(s),value_candidates(model(),batch(),spec(),'SYNTHETIC_REPLAY_V1'),None,p,
                      Clock(10,10_000_000_000,ASOF,'epoch1'),ASOF)
        self.assertNotIn('INTENT',[kind for kind,_ in events])

    def test_A13_calendar_and_deadline(self):
        self.assertIn('15:05',session_schedule('2026-03-06')['fixed_utc'])
        self.assertIn('14:05',session_schedule('2026-03-09')['fixed_utc'])
        self.assertIn('18:00',session_schedule('2026-11-27')['close_utc'])
        for day in ('2026-11-26','2026-09-19','2027-01-04'):
            with self.assertRaises(ContractError):session_schedule(day)
        c={'liquid_hours':'20260916:0830-20260916:1500','time_zone':'US/Central'}
        self.assertTrue(contract_session_matches(c,session_schedule('2026-09-16')))
        p=validate_plan(plan());s=p['strategies'][0]
        events=decide(s,new_book(s),value_candidates(model(),batch(),spec(),p['mode']),None,p,
                      Clock(11,11_000_000_000,'2026-09-16T14:05:01+00:00','epoch1'),ASOF)
        self.assertIn('MISSED_DECISION_DEADLINE',events[0][1]['reasons'])

    def test_transaction_replay_and_disk_failure_no_exposed_intent(self):
        from spxlab.events import atomic_json,read_events
        with tempfile.TemporaryDirectory() as d:
            p=validate_plan(plan());atomic_json(Path(d)/'plan.json',p)
            j=ResearchJournal(Path(d)/'events.sqlite',p)
            j.append('RESEARCH_PLAN', {'plan_hash':digest(p)}, clock_epoch='epoch1',mono=1,utc=ASOF)
            j.append('SYNTHETIC_FRAME',{'batch':batch(),'distribution':model(),'scheduled_at':ASOF},
                     clock_epoch='epoch1',mono=10_000_000_000,utc=ASOF,generation=1)
            j.save(d);j.store.close()
            proof,e=replay_research(d)
            self.assertTrue(proof['result_match']);self.assertEqual(e.books['DV1']['intent_count'],1)
        with tempfile.TemporaryDirectory() as d:
            j=ResearchJournal(Path(d)/'events.sqlite',p)
            original=j.store.append
            def fail(kind,*args,**kwargs):
                if kind=='INTENT':raise OSError('disk failure fixture')
                return original(kind,*args,**kwargs)
            j.store.append=fail
            with self.assertRaises(OSError):j.append('SYNTHETIC_FRAME',{'batch':batch(),'distribution':model(),'scheduled_at':ASOF},
                     clock_epoch='epoch1',mono=10_000_000_000,utc=ASOF,generation=1)
            self.assertEqual(list(read_events(Path(d)/'events.sqlite')),[])
            self.assertTrue(j.failed);j.store.close()


if __name__=='__main__':unittest.main()
