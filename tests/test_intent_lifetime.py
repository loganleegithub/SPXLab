import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_core import Feed, T, config
from spxlab.events import EventStore, atomic_json
from spxlab.market import MarketState
from spxlab.runtime import Coordinator, control_engine, digest, replay, validate


def cfg():
    c=config()
    c.update(quote_policy='SIDE_CONFIRMATION_V2',execution_policy='INTENT_LIFETIME_V2',
             intent_lifetime_seconds=10,compare_intent_lifetime_seconds=3)
    return c


class LifetimeTests(unittest.TestCase):
    def test_explicit_version_and_unchanged_constraints(self):
        validate(config(),live=False)
        validate(cfg(),live=False)
        for changes in ({'execution_policy':'UNKNOWN'}, {'execution_policy':'INTENT_LIFETIME_V1'},
                        {'intent_lifetime_seconds':15}, {'compare_intent_lifetime_seconds':None},
                        {'compare_raw_policy':True}, {'quote_policy':'RAW_FIELD_V1'},
                        {'max_all_in_points':'10'}, {'latency_seconds':0}, {'window_seconds':30}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate({**cfg(),**changes},live=False)
        c=cfg();before=copy.deepcopy(c);control=control_engine(c)
        self.assertEqual(c,before)
        self.assertEqual(control.cfg['intent_lifetime_seconds'],3)
        self.assertEqual(control.cfg['quote_policy'],c['quote_policy'])

    def test_later_valid_quote_fills_only_long_wait_and_exact_replay(self):
        with tempfile.TemporaryDirectory() as d:
            c=type('MockCollector',(),{})();c.run_id='test';c.generation=1
            c.store=EventStore(Path(d)/'events.sqlite');c.state=MarketState()
            plan=cfg();atomic_json(Path(d)/'plan.json',plan)
            coord=Coordinator(plan,d,T);coord.collector=c
            def emit(kind,p,mono):
                coord.before(mono,'2026-09-15T15:40:00+00:00',kind)
                e=c.store.append(kind,p,run_id=c.run_id,generation=1,mono=mono,
                                 utc='2026-09-15T15:40:00+00:00')
                c.state.apply(e);coord.observe(e,c.state)
            emit('PLAN',{'config':plan,'sha256':digest(plan)},T-200_000_000)
            feed=Feed().setup();emit('CONNECTED',{},T-100_000_000)
            for cid,contract in feed.state.contracts.items():
                emit('CONTRACT',contract,T-100_000_000)
                emit('MARKET_TYPE',{'con_id':cid,'market_type':1},T-100_000_000)
                for field,ref in feed.state.fields.get(cid,{}).items():
                    emit('FIELD',{'con_id':cid,'field':field,'value':ref['value']},T-100_000_000)
            emit('MARKET_BARRIER',{},T)
            emit('TIMER',{},T+999_999_999)
            self.assertEqual(coord.engine.books['F']['status'],'INTENT')
            emit('TIMER',{},T+2_000_000_000)  # stale; both retain the observation gap
            emit('TIMER',{},T+3_000_000_000)
            for cid in feed.state.contracts:
                for field,ref in feed.state.fields.get(cid,{}).items():
                    emit('FIELD',{'con_id':cid,'field':field,'value':ref['value']},T+5_000_000_000)
            emit('MARKET_BARRIER',{},T+5_000_000_000)
            self.assertEqual(coord.engine.books['F']['status'],'ASSUMED_FILLED')
            self.assertEqual(coord.control.books['F']['status'],'INDETERMINATE')
            self.assertNotIn('fill',coord.control.books['F'])
            emit('TIMER',{},T+15_000_000_000);c.store.close()
            out=replay(d)
            for key in ('result_match','trace_match','control_result_match','control_trace_match'):
                self.assertTrue(out[key])

    def test_ten_second_expiry_and_global_boundary(self):
        for selection_s,finish_s in ((0,10),(14,15)):
            f=Feed().setup();e=f.start(cfg())
            f.refresh(T+selection_s*10**9)
            e.on_event(f.event('MARKET_BARRIER',mono=T+selection_s*10**9),f.state)
            f.refresh(T+finish_s*10**9)
            e.on_event(f.event('MARKET_BARRIER',mono=T+finish_s*10**9),f.state)
            self.assertNotIn('fill',e.books['F'])

    def test_later_expensive_quote_cannot_fill(self):
        f=Feed().setup();e=f.start(cfg());e.on_event(f.event('MARKET_BARRIER'),f.state)
        f.refresh(T+5*10**9)
        f.event('FIELD',{'con_id':7580,'field':'ask','value':113.38},T+5*10**9)
        f.event('FIELD',{'con_id':7580,'field':'ask_size','value':10},T+5*10**9)
        e.on_event(f.event('MARKET_BARRIER',mono=T+5*10**9),f.state)
        self.assertNotIn('fill',e.books['F'])
        e.on_event(f.event('TIMER',mono=T+10*10**9),f.state)
        self.assertNotIn('fill',e.books['F'])


if __name__=='__main__':unittest.main()
