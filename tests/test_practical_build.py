"""Regressions for reviewed data, live receipt ordering, and locked-leg retention."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spxlab.contracts import ContractError, file_hash, validate_plan
from spxlab.events import atomic_json, read_events
from spxlab.frozen import freeze_run
from spxlab.observer import Observer, run_value_shadow
from spxlab.practical import prepare_reviewed_dataset, retrospective_value_report
from spxlab.valuation import value_candidates, render_valuation
from test_research_core import plan, samples, batch, spec, ASOF

ROOT=Path(__file__).resolve().parents[1]


class PracticalBuildTests(unittest.TestCase):
    def test_receipt_clock_and_late_model_cannot_enter_due_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'run';p=freeze_run(plan('OBSERVE_ONLY_V1'),directory,ROOT)
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T14:04:59+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=10_000_000_000):
                observer=Observer(directory,p)
            record=samples();artifact=Path(tmp)/'model.txt';artifact.write_text('SYNTHETIC test artifact')
            record['provenance'].update(model_artifact_path=str(artifact),model_artifact_hash=file_hash(artifact))
            atomic_json(directory/'model-inbox/model.json',record)
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T14:05:00.001+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=11_001_000_000):
                observer.intake();observer.intake()
            events=list(read_events(directory/'events.sqlite'))
            models=[e for e in events if e['event_type']=='DISTRIBUTION']
            self.assertEqual(len(models),1)
            self.assertEqual(models[0]['payload']['record']['computed_at'],ASOF)
            self.assertEqual(models[0]['payload']['record']['available_at'],models[0]['recorded_at'])
            self.assertEqual(models[0]['payload']['producer_available_at'],ASOF)
            self.assertLess(next(e['seq'] for e in events if e['event_type']=='EVALUATE'),models[0]['seq'])
            self.assertIsNone(observer.journal.engine.latest_valuation['distribution_hash'])
            observer.journal.save(directory);observer.store.close()
            # A restart must recover the input and not ingest the same artifact twice.
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T14:05:02+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=12_000_000_000):
                observer=Observer(directory,p);observer.intake()
            self.assertEqual(len(observer.journal.engine.distributions),1)
            observer.store.close()

    def test_pending_legs_retained_then_released_without_gateway(self):
        async def check():
            old={k:SimpleNamespace(strike=k,conId=k) for k in (7575,7600,7625)}
            cancelled=[];o=Observer.__new__(Observer);o.state=object()
            book={'status':'INTENT_PERSISTED','intent':{'center':7600,'width':25}}
            o.journal=SimpleNamespace(engine=SimpleNamespace(source=lambda now:None,
                plan=validate_plan(plan()),books={'DV1':book}))
            o.active_options=old.copy();o.option_contracts={};o.tickers=[]
            o.ib=SimpleNamespace(cancelMktData=lambda c:cancelled.append(c.strike))
            o.emit=lambda *a,**kw:None
            async def qualify(c):c.conId=int(c.strike);return c
            o.qualify=qualify;o.subscribe=lambda c:None
            with patch('spxlab.observer.spot_quote',return_value=({'value':7605},None)):
                await o.refresh_universe()
                self.assertTrue(set(old)<=set(o.active_options));self.assertEqual(cancelled,[])
                book['status']='EXPIRED_NO_FILL';await o.refresh_universe()
            self.assertEqual(set(cancelled),set(old));self.assertEqual(set(o.active_options),{7580,7605,7630})
        asyncio.run(check())

    def test_shadow_entry_requires_fixed_real_plan_before_connect(self):
        with self.assertRaises(ContractError):asyncio.run(run_value_shadow(plan('OBSERVE_ONLY_V1'),'unused',ROOT))
        with self.assertRaises(ContractError):asyncio.run(run_value_shadow(plan('VALUE_RESEARCH_SHADOW_V1'),'unused',ROOT))
        p=plan('VALUE_RESEARCH_SHADOW_V1');p['strategies'][0]['timing']='FIXED'
        # Missing preregistration must stop before creating a run or contacting IB.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractError):asyncio.run(run_value_shadow(p,Path(tmp)/'run',ROOT))
            self.assertFalse((Path(tmp)/'run').exists())

    def test_reviewed_sources_keep_actual_receipts_and_no_midpoint_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now='2026-09-16T10:00:00+00:00';items=[]
            for day,median,outcome in [('2026-09-14',7600,7605),('2026-09-15',7600,7610)]:
                s=root/(day+'-source.json');t=root/(day+'-settlement.json')
                atomic_json(s,{'session':day,'statistic_type':'median','median':median,'published_at':day+'T13:50:00+00:00',
                    'acquired_at':now,'source_url':'https://example.test/fixture','target_status':'DIAGNOSTIC_DATE_ASSUMED',
                    'revision_status':'ORIGINAL_VERSION_UNVERIFIED'})
                atomic_json(t,{'retrieved_at':now,'rows':[{'session':day,'series':'SPXW_PM','value':outcome}]})
                items.append({'row_id':day,'session':day,'source_path':str(s),'source_hash':file_hash(s),
                              'settlement_path':str(t),'settlement_hash':file_hash(t)})
            inputs={'prepared_at':now,'evaluation_sessions':['2026-09-16'],'time_bucket':'10:05','min_independent_days':2,'records':items}
            prepared=prepare_reviewed_dataset(inputs)
            self.assertEqual(prepared['check']['independent_days'],0)
            self.assertEqual(prepared['diagnostic_independent_days'],2)
            self.assertTrue(all(r['feature_available_at']==now for r in prepared['manifest']['rows']))
            self.assertIn('TARGET_DATE_ASSUMED',prepared['check']['excluded_rows'][0]['reasons'])
            b=batch();b['classification']='MARKET_OBSERVATION'
            report=retrospective_value_report(prepared,{'batch':b,'spot':7600,'generated_at':'2026-09-16T21:00:00+00:00',
                'source':{'median':7600,'published_at':'2026-09-16T13:50:00+00:00','available_at':'2026-09-16T13:51:00+00:00',
                          'target_date':'2026-09-16','source_url':'https://example.test/fixture','status':'DIAGNOSTIC_DATE_ASSUMED'}})
            self.assertIsNone(report['selected']);self.assertEqual(report['rows'][1]['payoff_point'],'17.5')
            self.assertEqual(report['walk_forward_reconstruction'][0]['training_days'],['2026-09-14'])
            source=json.loads(Path(items[0]['source_path']).read_text());source['statistic_type']='interval_midpoint'
            atomic_json(items[0]['source_path'],source)
            self.assertEqual(len(prepare_reviewed_dataset(inputs)['rejected_inputs']),1) # artifact tampering
            items[0]['source_hash']=file_hash(items[0]['source_path'])
            self.assertIn('Explicit median',prepare_reviewed_dataset(inputs)['rejected_inputs'][0]['reason'])

    def test_point_output_is_not_a_zero_width_interval(self):
        result=value_candidates(samples(),batch(),spec(),'SYNTHETIC_REPLAY_V1')
        text=render_valuation(result)
        self.assertIn('点估值',text);self.assertNotIn('16 … 16',text)

if __name__=='__main__':unittest.main()
