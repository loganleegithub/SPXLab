"""Independent synthetic reproductions of the e9996d9 preopen review."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spxlab.contracts import Clock, ContractError, stamp, validate_plan
from spxlab.field import FieldTracker, event_risk, terminal_scores, write_page
from spxlab.field_model import estimate, build_model
from spxlab.field_pin import submit_pin
from spxlab.forecast import normalize_forecast
from spxlab.frozen import freeze_run, replay_frozen
from spxlab.observer import Observer
from spxlab.research import ResearchEngine
from test_field import field_plan, value, tape, ROOT, AT, distribution, quote_batch


class ReviewRegressionTests(unittest.TestCase):
    def test_packet_crossing_minute_commits_one_bar_and_preserves_ready(self):
        tracker = FieldTracker(validate_plan(field_plan()))
        start = stamp('2026-09-16T13:30:59+00:00')
        for i in range(34):
            tracker.tape.add({'seq':i+1,'value':7600+i*.2,'utc':(start+timedelta(minutes=i)).isoformat()})
        tracker.tape.advance('2026-09-16T14:04:00+00:00')
        self.assertEqual(estimate(tracker.tape,'2026-09-16T14:04:00+00:00')['n_increments'],33)
        tracker.tape.add({'seq':40,'value':7607.,'utc':'2026-09-16T14:04:10+00:00'})
        last = {'seq':41,'value':7607.2,'utc':'2026-09-16T14:04:59.999+00:00'}
        for seq,kind,at in [(41,'FIELD',last['utc']),
                            (42,'FIELD','2026-09-16T14:05:00.001+00:00'),
                            (43,'MARKET_BARRIER','2026-09-16T14:05:00.002+00:00')]:
            with patch('spxlab.field.spot_quote',return_value=(last,None)):
                tracker.market_event({'event_type':kind,'payload':{}},None,Clock(seq,seq,at,'fixture'),False)
        keys=[(b['segment'],b['start_at']) for b in tracker.tape.bars]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertEqual(tracker.tape.bars[-1]['close'],7607.2)
        self.assertEqual(estimate(tracker.tape,'2026-09-16T14:05:00.002+00:00')['n_increments'],34)

    def wait_score(self,current):
        tracker=FieldTracker(validate_plan(field_plan()))
        tracker.predictions=[{'prediction_id':'prior','short_horizons':{},
            'candidates':[{'candidate_id':'C7600_W25'}],
            'timing':{'verify_at':'2026-09-16T18:22:00+00:00','H_points':1.,'C_points':1.5}}]
        with patch('spxlab.field.spot_quote',return_value=(None,'NO_SPOT')):
            return tracker.mature(None,Clock(200,200,'2026-09-16T18:22:00+00:00','fixture'),current)[0][1]

    def test_missing_distribution_is_unknown_not_observed_zero(self):
        current=value();current['as_of']='2026-09-16T18:22:00+00:00'
        current['distribution_hash']=None
        current['rows'][0].update(eligible=False,payoff_point=None,actionable_edge=None,reasons=['DISTRIBUTION_MISSING'])
        score=self.wait_score(current)
        self.assertIsNone(score['observed_H_same_candidates'])
        self.assertIsNone(score['error_points'])
        self.assertIsNone(score['wait_better_than_then_H'])
        self.assertNotEqual(score['status'],'OBSERVED')
        self.assertIn('C7600_W25:MODEL_OR_PAYOFF_UNAVAILABLE',score['reasons'])

    def test_completed_bars_immutable_timer_safe_and_real_gaps_reset(self):
        tracker=FieldTracker(validate_plan(field_plan()))
        tracker.tape=tape([7600+i*.2 for i in range(21)])
        original=deepcopy(tracker.tape.bars)
        tracker.tape.add({'seq':100,'utc':'2026-09-16T18:19:59.5+00:00','value':9999})
        self.assertEqual(tracker.tape.bars,original)
        tracker.tape.add({'seq':101,'utc':'2026-09-16T18:20:59+00:00','value':7605})
        tracker.market_event({'event_type':'PACKET_STARTED','payload':{}},None,Clock(102,102,'2026-09-16T18:20:59.9+00:00','f'),False)
        tracker.market_event({'event_type':'TIMER','payload':{}},None,Clock(103,103,'2026-09-16T18:21:00+00:00','f'),False)
        self.assertEqual(len(tracker.tape.bars),21)
        with patch('spxlab.field.spot_quote',return_value=(None,'MISSING')):
            tracker.market_event({'event_type':'MARKET_BARRIER','payload':{}},None,Clock(104,104,'2026-09-16T18:21:00.01+00:00','f'),False)
        self.assertEqual(len(tracker.tape.bars),22)
        tracker.tape.add({'seq':105,'utc':'2026-09-16T18:23:59+00:00','value':7606})
        self.assertEqual(estimate(tracker.tape,'2026-09-16T18:24:00+00:00')['status'],'WARMUP')

    def test_wait_known_negative_and_over_budget_remain_zero(self):
        current=value(distribution(100));current['as_of']='2026-09-16T18:22:00+00:00'
        score=self.wait_score(current)
        self.assertEqual(score['observed_H_same_candidates'],0.)
        self.assertEqual(score['status'],'OBSERVED');self.assertEqual(score['error_points'],-1.5)
        b=quote_batch();b['rows'][0]['debit_points']='7'
        current=value(b=b);current['as_of']='2026-09-16T18:22:00+00:00'
        self.assertEqual(self.wait_score(current)['observed_H_same_candidates'],0.)
        current['distribution_hash']=None
        current['rows'][0].update(payoff_point=None,actionable_edge=None)
        current['rows'][0]['reasons'].append('DISTRIBUTION_MISSING')
        self.assertEqual(self.wait_score(current)['observed_H_same_candidates'],0.)

    def test_wait_incomplete_payoff_quote_candidate_and_time_are_unknown(self):
        baseline=value();baseline['as_of']='2026-09-16T18:22:00+00:00'
        mutations=[lambda v:v['rows'][0].update(payoff_point=None),
                   lambda v:v['rows'][0].update(reasons=['MODEL_STALE']),
                   lambda v:v['rows'][0]['quote'].update(reasons=['STALE_SIDE_ask']),
                   lambda v:v.update(rows=[]),
                   lambda v:v.update(as_of='2026-09-16T18:21:30+00:00')]
        for mutation in mutations:
            current=deepcopy(baseline);mutation(current)
            score=self.wait_score(current)
            self.assertIsNone(score['observed_H_same_candidates']);self.assertTrue(score['reasons'])

    def test_manual_pin_intake_revision_retraction_and_legacy_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'fixture';p=field_plan();p['run_id']='PIN_ENGINEERING_FIXTURE_NOT_MARKET_EVIDENCE'
            p=freeze_run(p,directory,ROOT)
            raw=Path(tmp)/'text.txt';raw.write_text('合成测试：本日收盘Pin=7604。区间7575–7633为旁证。',encoding='utf-8')
            wall='2026-09-16T13:40:00+00:00';mono=100
            with patch('spxlab.field_pin.utc_now',side_effect=lambda:wall),patch('spxlab.observer.utc_now',side_effect=lambda:wall),patch('spxlab.observer.time.monotonic_ns',side_effect=lambda:mono):
                observer=Observer(directory,p);observer.in_timer=True
                self.assertIsNone(observer.journal.engine.source(wall))
                result=submit_pin(directory,raw,'7604')
                self.assertEqual(result['status'],'QUEUED_NOT_YET_APPLIED')
                wall='2026-09-16T13:40:01+00:00';mono+=1;observer.intake()
                source=observer.journal.engine.source(wall)
                self.assertEqual(source['anchor'],'7604');self.assertEqual(source['statistic_type'],'unspecified_point')
                self.assertNotIn('median',source);self.assertEqual(source['available_at'],wall)
                with self.assertRaises(ContractError):normalize_forecast(source)
                model,diagnostic=build_model(tape([7600+i*.2 for i in range(21)]),{'value':7604,'utc':AT,'seq':30},AT,
                    p['schedule']['close_utc'],source,computed_at=AT,algorithm_hash='fixture')
                self.assertEqual(diagnostic['anchor_mode'],'PIN');self.assertEqual(model['parameters']['anchor'],7604.)
                first=deepcopy(source)
                wall='2026-09-16T13:41:00+00:00';mono+=1;submit_pin(directory,raw,'7610',statistic_label='mode')
                wall='2026-09-16T13:41:01+00:00';mono+=1;observer.intake()
                revised=observer.journal.engine.source(wall)
                self.assertEqual(revised['supersedes'],first['forecast_id']);self.assertEqual(revised['anchor'],'7610')
                self.assertEqual(observer.journal.engine.source('2026-09-16T13:40:02+00:00'),first)
                wall='2026-09-16T13:42:00+00:00';mono+=1;submit_pin(directory,raw,retract=True)
                wall='2026-09-16T13:42:01+00:00';mono+=1;observer.intake()
                self.assertEqual(observer.journal.engine.source(wall)['status'],'RETRACTED')
                observer.publish_field_model(mono,wall,wall)
                self.assertEqual(observer.journal.engine.field.diagnostic['anchor_mode'],'MARKET_ONLY')
                with self.assertRaises(ContractError):submit_pin(directory,raw,'7604',session='2026-09-15')
                wall='2026-09-17T13:40:00+00:00'
                with self.assertRaises(ContractError):submit_pin(directory,raw,'7604')
                observer.journal.save(directory);observer.store.close()
            self.assertTrue(replay_frozen(directory,Path(tmp)/'proof')['trace_match'])

    def test_pin_revision_never_resets_used_intent(self):
        from spxlab.policy import new_book, decide, apply_book_event
        p=validate_plan(field_plan());s=p['strategies'][1];book=new_book(s)
        v=value();v['timing_issues']=[]
        for kind,payload in decide(s,book,v,None,p,Clock(1,1,AT,'f'),AT):apply_book_event(book,kind,payload)
        engine=ResearchEngine(p);engine.books[s['id']]=book
        record={'schema_version':2,'forecast_id':'pin','source_product_id':p['source_product_id'],'model_version':'HUMAN_EXPERT_PIN_V1',
            'source_role':'HUMAN_EXPERT_PIN','target_session':p['session'],'target_at':p['schedule']['close_utc'],'series':'SPXW_PM',
            'statistic_type':'unspecified_point','anchor':'7604','first_seen_at':AT,'validated_at':AT,
            'status':'VALIDATED','reviewed_by':'fixture','gamma':'UNKNOWN','supersedes':None,
            'raw_assets':[{'sha256':'0'*64,'received_at':AT,'source_url':'fixture://text'}]}
        engine.on_event({'event_type':'FORECAST','payload':{'record':record},'seq':2,'monotonic_ns':2,'recorded_at':AT,'generation':0})
        self.assertEqual(engine.books[s['id']]['intent_count'],1)
        self.assertEqual(engine.books[s['id']]['intent'],book['intent'])

    def test_event_crossing_and_post_event_are_annotations_only(self):
        p=validate_plan(field_plan());events=p['known_events']
        before=event_risk(events,'2026-09-16T14:05:00+00:00',p['schedule']['close_utc'])
        after=event_risk(events,'2026-09-16T18:15:00+00:00',p['schedule']['close_utc'])
        self.assertTrue(all(e['crosses_target'] for e in before))
        self.assertEqual([e['crosses_target'] for e in after],[False,True])
        self.assertTrue(all(e['model_treatment']=='NOT_SEPARATELY_PRICED' for e in before+after))


if __name__ == '__main__': unittest.main()
