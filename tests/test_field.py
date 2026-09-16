"""Engineering fixtures only. None of these prices or outcomes are market evidence."""
from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spxlab.calendar import session_schedule
from spxlab.contracts import Clock,ContractError,digest,stamp,validate_plan
from spxlab.distribution import expected_payoff
from spxlab.events import atomic_json,read_events
from spxlab.field import FieldTracker,terminal_scores
from spxlab.field_model import MinuteTape,build_model,condition,estimate,make_process,marginal,normal_call,payoff,transition
from spxlab.frozen import freeze_run,replay_frozen
from spxlab.observer import Observer
from spxlab.policy import apply_book_event,decide,new_book
from spxlab.research import ResearchJournal,replay_research
from spxlab.timing import lookahead,nodes
from spxlab.valuation import value_candidates
from test_research_core import plan as old_plan,model as old_model,batch as old_batch,spec as old_spec

ROOT=Path(__file__).resolve().parents[1]
AT='2026-09-16T18:20:00+00:00'


def field_plan():
    return json.loads((ROOT/'examples/field/2026-09-16.json').read_text())


def tape(prices):
    t=MinuteTape(session_schedule('2026-09-16'));start=stamp('2026-09-16T17:59:59+00:00')
    for i,s in enumerate(prices):t.add({'seq':i+1,'value':s,'utc':(start+timedelta(minutes=i)).isoformat()})
    t.advance(AT)
    return t


def distribution(q=1.,pin=None):
    params={'anchor':pin,'kappa_used':.02 if pin else 0.,'q':q,'parameter_cutoff':AT}
    process=make_process(params,7600,100)
    return {'schema_version':2,'model_version':'FIELD_LOCAL_MIXTURE_V1','measure':'P_ESTIMATE',
        'representation':'NORMAL_MIXTURE','components':marginal(process),'process':process,'parameters':params,
        'target_at':'2026-09-16T20:00:00+00:00','information_cutoff':AT,'conditioning_as_of':AT,
        'computed_at':AT,'available_at':AT,'training_cutoff':AT,'calibration_status':'EXPLORATORY',
        'provenance':{'classification':'MARKET_OBSERVATION'},
        'diagnostic':{'status':'READY','state':'MIXED','pin':pin}}


def quote_batch(sd=45.,center=7600,seq=100,mono=100_000_000_000):
    refs=[]
    for k,side in zip((center-25,center,center+25),('ask','bid','ask')):
        mid=normal_call(7600,sd,k)
        refs.append({'con_id':str(k),'fields':{'bid':{'value':mid-.025},'ask':{'value':mid+.025}},
            'side_confirmation_refs':{side:{'seq':seq,'mono':mono,'generation':1}}})
    debit=refs[0]['fields']['ask']['value']-2*refs[1]['fields']['bid']['value']+refs[2]['fields']['ask']['value']
    c={'candidate_id':f'C{center}_W25','center':center,'width':25,'roles':['SPOT']}
    return {'schema_version':2,'universe':[c],'rows':[{**c,'snapshot_seq':seq,'generation':1,
        'clock_epoch':'fixture','as_of':AT,'con_ids':[r['con_id'] for r in refs],'reasons':[],
        'debit_points':str(debit),'fees_usd':'6','fee_version':'FIXTURE','quote_refs':refs}],
        'as_of':AT,'target_at':'2026-09-16T20:00:00+00:00','snapshot_seq':seq,'classification':'MARKET_OBSERVATION'}


def value(d=None,b=None):
    p=field_plan()
    return value_candidates(d or distribution(),b or quote_batch(),
        {**p['valuation'],'budget_points':'6.25'},'FIELD_PAPER_V1')


class FieldModelTests(unittest.TestCase):
    def test_signed_regression_market_only_and_noise(self):
        for path,expected in [([7600+20*.97**i for i in range(21)],'CONVERGING_CANDIDATE'),
                              ([7600+5*1.08**i for i in range(21)],'EXPANDING_CANDIDATE')]:
            t=tape(path);r,d=build_model(t,{'value':path[-1],'utc':AT,'seq':22},AT,
                '2026-09-16T20:00:00+00:00',{'median':7600,'content_hash':'pin'},computed_at=AT,algorithm_hash='a')
            self.assertEqual(d['state'],expected)
            self.assertEqual(d['n_increments'],20)
            self.assertEqual(d['kappa_shrunk']>0,expected=='CONVERGING_CANDIDATE')
            self.assertAlmostEqual(d['kappa_shrunk'],20/80*d['kappa_raw'])
        r,d=build_model(t,{'value':path[-1],'utc':AT,'seq':22},AT,'2026-09-16T20:00:00+00:00',None,computed_at=AT,algorithm_hash='a')
        self.assertEqual(d['anchor_mode'],'MARKET_ONLY');self.assertEqual(d['state'],'MIXED')
        self.assertTrue(all(c['kappa']==0 for c in r['process']['components']))
        self.assertEqual(sum(c['weight'] for c in r['components']),1)

    def test_gap_and_stale_parameters_never_create_zero_return_bars(self):
        t=tape([7600+i*.2 for i in range(21)])
        self.assertEqual(estimate(t,AT)['status'],'READY')
        t.break_path(AT,'DISCONNECTED')
        self.assertEqual(estimate(t,AT)['status'],'WARMUP')
        self.assertEqual(len(t.bars),21)
        t=tape([7600+i*.2 for i in range(21)])
        r,d=build_model(t,{'value':7604,'utc':AT,'seq':22},'2026-09-16T18:22:00+00:00',
            '2026-09-16T20:00:00+00:00',None,computed_at='2026-09-16T18:22:00+00:00',algorithm_hash='a')
        self.assertIsNone(r);self.assertEqual(d['status'],'STALE_PARAMETERS')

    def test_new_spot_old_pin_actual_reconditioning_and_pin_revision(self):
        t=tape([7600+20*.97**i for i in range(21)])
        def build(s,pin):return build_model(t,{'value':s,'utc':AT,'seq':30},AT,
            '2026-09-16T20:00:00+00:00',{'median':pin,'content_hash':str(pin)},computed_at=AT,algorithm_hash='a')[0]
        a,b,c=build(7605,7600),build(7615,7600),build(7615,7610)
        self.assertNotEqual(a['components'],b['components']);self.assertNotEqual(b['parameters'],c['parameters'])
        self.assertEqual(a['training_cutoff'],b['training_cutoff'])

    def test_tower_consistency_payoff_and_posterior_branch_update(self):
        p=make_process({'anchor':7610,'kappa_used':.02,'q':4},7600,100)
        direct=payoff(marginal(p),7605,25);integral=0.
        for c in p['components']:
            mean,var=transition(c,7600,2)
            for z,w in nodes(255):
                updated=condition(p,mean+math.sqrt(var)*z,2)
                integral+=c['weight']*w*payoff(marginal(updated),7605,25)
        self.assertAlmostEqual(direct,integral,places=5)
        self.assertNotEqual([c['weight'] for c in condition(p,7630,2)['components']],
                            [c['weight'] for c in p['components']])


class FieldDecisionTests(unittest.TestCase):
    def test_exploratory_admission_subset_and_legacy_isolation(self):
        b=quote_batch();b['universe'].append({'candidate_id':'missing','center':7605,'width':25})
        v=value(b=b);self.assertEqual(v['selected'],'C7600_W25')
        self.assertFalse(v['coverage']['complete']);self.assertEqual(v['selection_scope'],'BEST_OBSERVED_VALID_SUBSET')
        self.assertIsNone(v['rows'][1]['cost_points'])
        for mode in ('OBSERVE_ONLY_V1','VALUE_RESEARCH_SHADOW_V1'):
            v=value_candidates(distribution(),quote_batch(),old_spec(),mode)
            self.assertIn('MODEL_NOT_READY',v['rows'][0]['reasons']);self.assertIsNone(v['selected'])
        b['classification']='SYNTHETIC_FIXTURE'
        with self.assertRaises(ContractError):value(b=b)
        p=old_plan();p['valuation']['allowed_uncertainty']=['POINT_ONLY']
        with self.assertRaises(ContractError):validate_plan(p)

    def test_continuation_changes_action_and_first_positive_remains_baseline(self):
        p=validate_plan(field_plan())
        for q,action in [(1.,'ENTER'),(9.,'WAIT')]:
            d=distribution(q);v=value(d);v['timing']=lookahead(d,v,p)
            self.assertEqual(v['timing']['status'],'OK');self.assertEqual(v['timing']['action'],action)
            v['timing_issues']=[]
            counts=[]
            for s in p['strategies']:
                events=decide(s,new_book(s),v,None,p,Clock(100,100_000_000_000,AT,'fixture'),AT)
                counts.append(sum(k=='INTENT' for k,_ in events))
            self.assertEqual(counts,[int(action=='ENTER'),1])

    def test_q_is_quote_derived_endpoint_and_invalid_curve_fallback(self):
        p=validate_plan(field_plan());d=distribution();v=value(d)
        t=lookahead(d,v,p)
        self.assertNotAlmostEqual(t['fits'][0]['q_variance_per_minute'],d['parameters']['q'])
        v['rows'][0]['quote']['quote_refs'][0]['fields']['bid']['value']=-1
        self.assertEqual(lookahead(d,v,p)['status'],'TIMING_FALLBACK')
        v=value(d);v['as_of']='2026-09-16T19:29:30+00:00'
        t=lookahead(d,v,p);self.assertEqual(t['C_points'],0);self.assertEqual(t['action'],'ENTER')
        missing=value();missing['rows'][0].update(eligible=False,actionable_edge=None,reasons=['COST_UNKNOWN'])
        self.assertIsNone(lookahead(d,missing,p)['H_points'])
        negative=value(distribution(100));t=lookahead(distribution(100),negative,p)
        self.assertLess(t['H_points'],0);self.assertEqual(t['action'],'WAIT')

    def test_budget_is_separate_from_positive_value_and_once_limit(self):
        b=quote_batch();b['rows'][0]['debit_points']='7'
        v=value(b=b);self.assertEqual(v['rows'][0]['economic_state'],'POSITIVE_MODEL_EDGE')
        self.assertEqual(v['rows'][0]['risk_state'],'RISK_BUDGET_REJECT');self.assertIsNone(v['selected'])
        p=validate_plan(field_plan());s=p['strategies'][1];book=new_book(s);v=value();v['timing_issues']=[]
        events=decide(s,book,v,None,p,Clock(100,100_000_000_000,AT,'fixture'),AT)
        for kind,payload in events:apply_book_event(book,kind,payload)
        self.assertLess(float(book['intent']['limit_points']),6.25)
        self.assertNotIn('INTENT',[k for k,_ in decide(s,book,v,None,p,Clock(101,101_000_000_000,AT,'fixture'),AT)])


class FieldRuntimeTests(unittest.TestCase):
    def test_live_producer_full_raw_path_intent_fill_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'fixture';p=field_plan();p['run_id']='ENGINEERING_FIXTURE_NOT_MARKET_EVIDENCE'
            p=freeze_run(p,directory,ROOT)
            wall='2026-09-16T13:29:00+00:00';origin=stamp(wall)
            def mono():return int((stamp(wall)-origin).total_seconds()*1e9)+1
            with patch('spxlab.observer.utc_now',side_effect=lambda:wall),patch('spxlab.observer.time.monotonic_ns',side_effect=mono):
                o=Observer(directory,p);o.in_timer=True;o.generation=1
                o.emit('CONNECTED',{})
                o.emit('CONTRACT',{'con_id':416904,'symbol':'SPX','sec_type':'IND'})
                o.emit('SUBSCRIBED',{'con_id':416904,'req_id':1});o.emit('MARKET_TYPE',{'con_id':416904,'market_type':1})
                for i in range(35):
                    wall=(stamp('2026-09-16T13:30:59+00:00')+timedelta(minutes=i)).isoformat()
                    o.emit('FIELD',{'con_id':416904,'req_id':1,'field':'last','value':7600+(i%2)*.3})
                    o.emit('MARKET_BARRIER',{})
                wall='2026-09-16T14:04:59.5+00:00'
                for k in (7575,7600,7625):
                    o.emit('CONTRACT',{'con_id':k,'sec_type':'OPT','symbol':'SPX','trading_class':'SPXW',
                        'right':'C','expiry':'20260916','currency':'USD','multiplier':'100','strike':k,
                        'time_zone':'US/Eastern','liquid_hours':'20260916:0930-20260916:1600'})
                    o.emit('SUBSCRIBED',{'con_id':k,'req_id':k});o.emit('MARKET_TYPE',{'con_id':k,'market_type':1})
                def quotes():
                    for k in (7575,7600,7625):
                        mid=normal_call(7600,45,k)
                        for name,val in [('bid',mid-.025),('bid_size',10),('ask',mid+.025),('ask_size',10)]:
                            o.emit('FIELD',{'con_id':k,'req_id':k,'field':name,'value':val})
                quotes();o.emit('MARKET_BARRIER',{})
                o.in_timer=False;wall='2026-09-16T14:05:00+00:00';o.emit('TIMER',{})
                engine=o.journal.engine
                self.assertEqual(engine.field.diagnostic['status'],'READY')
                self.assertEqual(len(engine.distributions),1)
                self.assertEqual(engine.books['FIRST_POSITIVE']['intent_count'],1)
                self.assertEqual(engine.latest_valuation['selection_scope'],'BEST_OBSERVED_VALID_SUBSET')
                # A price/size-confirmed real-event-shaped quote AFTER intent is
                # necessary even in this engineering fixture.
                wall='2026-09-16T14:05:01.1+00:00';quotes();o.emit('MARKET_BARRIER',{})
                self.assertEqual(engine.books['FIRST_POSITIVE']['status'],'ASSUMED_FILLED')
                first=deepcopy(engine.books['FIRST_POSITIVE']['intent'])
                wall='2026-09-16T14:05:02+00:00';o.emit('DISCONNECTED',{})
                self.assertEqual(engine.books['FIRST_POSITIVE']['intent'],first)
                o.journal.save(directory);o.store.close()
            events=list(read_events(directory/'events.sqlite'))
            delivery=next(e for e in events if e['event_type']=='DISTRIBUTION')
            self.assertEqual(delivery['payload']['record']['available_at'],delivery['recorded_at'])
            self.assertTrue(replay_frozen(directory,Path(tmp)/'proof')['trace_match'])

    def test_producer_real_receipt_not_backdated_and_frozen_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'fixture';p=field_plan();p['run_id']='ENGINEERING_FIXTURE_NOT_MARKET_EVIDENCE'
            p=freeze_run(p,directory,ROOT)
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T18:19:59+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=1_000_000_000):
                observer=Observer(directory,p)
            # Set up an actual-looking unit fixture through committed raw events;
            # production has no fixture ingestion CLI for FIELD.
            observer.grid=[AT];observer.grid_index=0
            observer.in_timer=True;observer.generation=1
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T18:19:59.1+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=1_100_000_000):
                observer.emit('CONNECTED',{})
                observer.emit('CONTRACT',{'con_id':416904,'symbol':'SPX','sec_type':'IND'})
                observer.emit('SUBSCRIBED',{'con_id':416904,'req_id':1})
                observer.emit('MARKET_TYPE',{'con_id':416904,'market_type':1})
                observer.emit('FIELD',{'con_id':416904,'req_id':1,'field':'last','value':7600})
            # Warmup shortage still journals real status and UNKNOWN, no inbox.
            observer.in_timer=False
            with patch('spxlab.observer.utc_now',return_value=AT),patch('spxlab.observer.time.monotonic_ns',return_value=2_000_000_000):
                observer.emit('TIMER',{})
            self.assertEqual(observer.journal.engine.latest_valuation['model_diagnostic']['state'],'WARMUP')
            self.assertEqual(observer.journal.engine.books['LOOKAHEAD']['intent_count'],0)
            observer.journal.save(directory);observer.store.close()
            self.assertTrue(replay_frozen(directory,Path(tmp)/'proof')['trace_match'])

    def test_forward_score_preserves_hits_misses_and_missing(self):
        tracker=FieldTracker(validate_plan(field_plan()));d=distribution();v=value(d)
        v.update(timing=lookahead(d,v,validate_plan(field_plan())),timing_issues=[])
        prediction=tracker.prediction(d,v,Clock(1,1,AT,'x'));tracker.apply('FIELD_PREDICTION',prediction)
        for observed,status in [(7600.,'HIT'),(7650.,'MISS'),(None,'UNKNOWN')]:
            with patch('spxlab.field.spot_quote',return_value=({'value':observed,'utc':'2026-09-16T18:21:01+00:00'} if observed else None,None)):
                events=tracker.mature(None,Clock(2,2,'2026-09-16T18:21:04+00:00','x'),value=v)
            self.assertEqual(next(p['status'] for k,p in events if k=='FIELD_SCORE'),status)

if __name__=='__main__':unittest.main()
