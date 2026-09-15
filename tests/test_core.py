import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from spxlab.collector import guarded_send, ALLOWED_MESSAGES
from spxlab.engine import Engine, D, payoff, nearest_center, combo_quote, estimate_fee, settle
from spxlab.events import EventStore, read_events, atomic_json
from spxlab.market import MarketState
from spxlab.runtime import Coordinator, digest, replay, save_result, validate

T=10_000_000_000


def config(gamma='LONG'):
    return {'experiment_id':'fixture','mode':'INTRADAY_DIAGNOSTIC','target_date':'2026-09-15',
            'expiry':'20260915','cutoff_utc':'2026-09-15T15:40:00+00:00',
            'width':25,'max_all_in_points':'6.25','window_seconds':15,
            'latency_seconds':1,'intent_lifetime_seconds':3,
            'allowed_forecast_statuses':['DIAGNOSTIC_DATE_ASSUMED'],
            'forecast':{'median':7604,'statistic':'median','gamma':gamma,
                        'target_date':'2026-09-15','available_at':'2026-09-15T15:00:00+00:00',
                        'status':'DIAGNOSTIC_DATE_ASSUMED','reviewed_by':'Codex','assumptions':['fixture']},
            'fees':{'kind':'IBKR_PUBLIC_CUSTOMER_SCENARIO','version':'test',
                    'regulatory_reserve_per_contract_usd':'.05'}}


class Feed:
    def __init__(self):
        self.state=MarketState();self.seq=0

    def event(self,kind,payload=None,mono=T,generation=1):
        self.seq+=1
        e={'seq':self.seq,'event_type':kind,'payload':payload or {},'generation':generation,
           'monotonic_ns':mono,'recorded_at':'2026-09-15T15:40:00+00:00'}
        self.state.apply(e)
        return e

    def setup(self,mono=T-100_000_000):
        self.event('CONNECTED',mono=mono)
        self.event('CONTRACT',{'con_id':1,'symbol':'SPX','sec_type':'IND'},mono)
        self.event('MARKET_TYPE',{'con_id':1,'market_type':1},mono)
        self.event('FIELD',{'con_id':1,'field':'last','value':7604},mono)
        for k in range(7570,7641,5):
            self.event('CONTRACT',{'con_id':k,'symbol':'SPX','sec_type':'OPT','strike':k,
                       'trading_class':'SPXW','right':'C','expiry':'20260915','currency':'USD','multiplier':'100'},mono)
            self.event('MARKET_TYPE',{'con_id':k,'market_type':1},mono)
        self.refresh(mono)
        return self

    def refresh(self,mono):
        for k in range(7570,7641,5):
            # Convex call curve: independent butterfly value 2 * .0032 * 25^2 = 4.
            price=100-.5*(k-7600)+.0032*(k-7600)**2
            for field,value in [('bid',round(price-.1,6)),('ask',round(price+.1,6)),('bid_size',10),('ask_size',10)]:
                self.event('FIELD',{'con_id':k,'field':field,'value':value},mono)

    def start(self,cfg=None):
        engine=Engine(cfg or config())
        engine.on_event(self.event('CUTOFF',{'scheduled_mono':T}),self.state)
        return engine


class EconomicsTests(unittest.TestCase):
    def test_payoff_matches_three_independent_legs(self):
        for s in (7520,7575,7581.25,7600,7618.4,7625,7700):
            legs=max(D(s)-7575,0)-2*max(D(s)-7600,0)+max(D(s)-7625,0)
            self.assertEqual(payoff(s,7600),legs)
        self.assertEqual(nearest_center('7602.5'),7600)
        self.assertEqual(nearest_center('7602.5001'),7605)

    def test_combo_conservative_price_and_leg_minimum(self):
        f=Feed().setup();q,reason=combo_quote(f.state,7605,config(),T)
        self.assertIsNone(reason)
        self.assertEqual(D(q['debit_points']),D('4.4'))
        self.assertEqual(D(q['midpoint_points_diagnostic']),D('4'))
        self.assertEqual(D(q['fees']['components_usd']['commission_leg_minima']),D('3.30'))
        self.assertGreater(D(q['all_in_points']),D('4.4'))
        parts=q['fees']['components_usd']
        self.assertEqual(D(parts['execution_surcharge']),D('.56'))
        self.assertEqual(D(parts['trade_processing']),D('.01'))
        self.assertEqual(D(parts['cboe_on_exchange_orf']),D('.04992'))
        self.assertIsNone(estimate_fee([1,2,3],{'kind':'UNKNOWN'}))

    def test_settlement_cash_math_and_unknown(self):
        f=Feed().setup();engine=f.start();engine.on_event(f.event('MARKET_BARRIER'),f.state)
        f.refresh(T+1_100_000_000);engine.on_event(f.event('MARKET_BARRIER',mono=T+1_100_000_000),f.state)
        engine.on_event(f.event('TIMER',mono=T+15_000_000_000),f.state)
        result=engine.result();result['books']['FP']={'status':'INDETERMINATE'}
        e={'target_date':'2026-09-15','series':'SPXW_PM','status':'CONFIRMED',
           'source_url':'https://www.cboe.com/fixture','asset_sha256':'fixture','value':'7605'}
        out=settle(result,e);b=out['books']['F'];q=b['fill']
        self.assertEqual(D(b['net_pnl_estimated_fees_usd']),(25-D(q['debit_points']))*100-D(q['fees']['estimated_usd']))
        self.assertIsNone(out['books']['FP']['net_pnl_estimated_fees_usd'])
        self.assertEqual(result['settlement_status'],'PENDING_OFFICIAL_PM_SETTLEMENT')
        with self.assertRaises(ValueError):settle(result,{**e,'series':'SPX_AM'})


class QualityTests(unittest.TestCase):
    def test_missing_stale_size_delayed_generation_and_crossed(self):
        for field,value,mono,expected in [('ask_size',0,T,'INSUFFICIENT_SIZE'),
                ('ask_size',10,T-1_000_000_001,'STALE_ask_size'),
                ('ask',0,T,'INVALID_OR_CROSSED_BOOK')]:
            f=Feed().setup();f.event('FIELD',{'con_id':7580,'field':field,'value':value},mono)
            self.assertEqual(combo_quote(f.state,7605,config(),T)[1],expected)
        f=Feed().setup();f.event('MARKET_TYPE',{'con_id':7580,'market_type':3})
        self.assertEqual(combo_quote(f.state,7605,config(),T)[1],'NOT_CONFIRMED_REALTIME')
        f.event('CONNECTED',generation=2)
        f.event('MARKET_TYPE',{'con_id':7580,'market_type':1},generation=1)
        self.assertNotIn('7580',f.state.types)

    def test_cross_leg_skew(self):
        f=Feed().setup()
        f.event('FIELD',{'con_id':7580,'field':'ask','value':111.38},T-1_200_000_000)
        self.assertEqual(combo_quote(f.state,7605,config(),T)[1],'CROSS_LEG_TIME_SKEW')

    def test_invalid_forecast_excludes_four_books(self):
        for override in ({'available_at':'2026-09-15T15:41:00+00:00'},
                         {'statistic':'mode'},{'target_date':'2026-09-16'}):
            f=Feed().setup();cfg=config();cfg['forecast'].update(override);e=f.start(cfg)
            self.assertEqual(e.books['S']['status'],'SELECTING')
            for b in ('F','FG','FP','FGP'):
                self.assertEqual(e.books[b]['reason'],'FORECAST_NOT_ELIGIBLE')


class DecisionTests(unittest.TestCase):
    def test_atomic_packet_latency_and_same_input_equivalence(self):
        f=Feed().setup();e=f.start()
        self.assertEqual(e.books['F']['status'],'SELECTING')
        e.on_event(f.event('FIELD',{'con_id':7605,'field':'bid_size','value':10}),f.state)
        self.assertEqual(e.books['F']['status'],'SELECTING')
        e.on_event(f.event('MARKET_BARRIER'),f.state)
        self.assertEqual(e.books['F']['status'],'INTENT')
        e.on_event(f.event('TIMER',mono=T+999_999_999),f.state)
        self.assertEqual(e.books['F']['status'],'INTENT')
        f.refresh(T+1_000_000_000);e.on_event(f.event('MARKET_BARRIER',mono=T+1_000_000_000),f.state)
        for b in ('S','F','FG','FP','FGP'):
            self.assertEqual(e.books[b]['status'],'ASSUMED_FILLED')
        for b in ('S','F','FG'):
            self.assertEqual(e.books[b]['fill'],e.books['F']['fill'])
        self.assertEqual(e.books['FP']['fill'],e.books['FGP']['fill'])

    def test_short_and_unknown_gate(self):
        for gamma,status in [('SHORT','NO_TRADE'),('UNKNOWN','INDETERMINATE')]:
            f=Feed().setup();e=f.start(config(gamma));e.on_event(f.event('MARKET_BARRIER'),f.state)
            for b in ('FG','FGP'):self.assertEqual(e.books[b]['status'],status)
            self.assertEqual(e.books['F']['status'],'INTENT')

    def test_p0_requires_every_candidate_and_can_change_center(self):
        f=Feed().setup();del f.state.fields['7575']['ask_size'];e=f.start()
        e.on_event(f.event('MARKET_BARRIER'),f.state)
        self.assertEqual(e.books['FP']['status'],'SELECTING')
        self.assertEqual(e.books['F']['status'],'INTENT')
        f=Feed().setup()
        # Improve the 7600 body's bid by .05; this makes that candidate .10 cheaper.
        f.event('FIELD',{'con_id':7600,'field':'bid','value':99.95})
        e=f.start();e.on_event(f.event('MARKET_BARRIER'),f.state)
        self.assertEqual(e.books['FP']['center'],7600)
        self.assertEqual(e.books['F']['center'],7605)

    def test_cost_rejection_is_terminal_not_reselected(self):
        f=Feed().setup();f.event('FIELD',{'con_id':7580,'field':'ask','value':113.38})
        e=f.start();e.on_event(f.event('MARKET_BARRIER'),f.state)
        self.assertEqual(e.books['F']['reason'],'COST_CAP')
        f.refresh(T+500_000_000);e.on_event(f.event('MARKET_BARRIER',mono=T+500_000_000),f.state)
        self.assertEqual(e.books['F']['reason'],'COST_CAP')

    def test_end_boundary_and_missing_observation(self):
        f=Feed().setup();e=f.start()
        f.refresh(T+14_000_000_000);e.on_event(f.event('MARKET_BARRIER',mono=T+14_000_000_000),f.state)
        f.refresh(T+15_000_000_000);e.on_event(f.event('TIMER',mono=T+15_000_000_000),f.state)
        self.assertNotIn('fill',e.books['F'])
        f=Feed().setup();e=f.start();e.on_event(f.event('TIMER',mono=T+15_000_000_000),f.state)
        self.assertEqual(e.books['F']['status'],'INDETERMINATE')

    def test_connection_loss_never_reuses_intent(self):
        f=Feed().setup();e=f.start();e.on_event(f.event('MARKET_BARRIER'),f.state)
        e.on_event(f.event('DISCONNECTED',mono=T+500_000_000),f.state)
        f.event('CONNECTED',mono=T+1_000_000_000,generation=2)
        e.on_event(f.event('TIMER',mono=T+15_000_000_000,generation=2),f.state)
        self.assertEqual(e.books['F']['status'],'INDETERMINATE')


class EvidenceTests(unittest.TestCase):
    def test_protocol_allowlist(self):
        sent=[];send=guarded_send(lambda *a,**kw:sent.append(a))
        for i in ALLOWED_MESSAGES:send(i,'test')
        for i in (3,4,5,6,7,8,15,58,61,73):
            with self.assertRaises(PermissionError):send(i,'blocked')
        self.assertEqual(len(sent),len(ALLOWED_MESSAGES))

    def test_hash_chain_tamper_detection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'events.sqlite';s=EventStore(p);s.append('TEST',{'x':1},run_id='x');s.close()
            self.assertEqual(len(list(read_events(p))),1)
            db=sqlite3.connect(p);db.execute("UPDATE events SET body=replace(body,'TEST','EVIL')");db.commit();db.close()
            with self.assertRaises(ValueError):list(read_events(p))

    def test_cutoff_freezes_before_new_field_and_exact_replay(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config();c=type('MockCollector',(),{})();c.run_id='test';c.generation=1
            c.store=EventStore(Path(d)/'events.sqlite');c.state=MarketState()
            coord=Coordinator(cfg,d,T);coord.collector=c
            atomic_json(Path(d)/'plan.json',cfg)
            def emit(kind,p,mono):
                coord.before(mono,'2026-09-15T15:40:00+00:00',kind)
                ev=c.store.append(kind,p,run_id=c.run_id,generation=1,mono=mono,utc='2026-09-15T15:40:00+00:00')
                c.state.apply(ev);coord.observe(ev,c.state)
            emit('PLAN',{'config':cfg,'sha256':digest(cfg)},T-200_000_000)
            f=Feed().setup()
            emit('CONNECTED',{},T-100_000_000)
            for cid,contract in f.state.contracts.items():
                emit('CONTRACT',contract,T-100_000_000)
                emit('MARKET_TYPE',{'con_id':cid,'market_type':1},T-100_000_000)
                for field,ref in f.state.fields.get(cid,{}).items():
                    emit('FIELD',{'con_id':cid,'field':field,'value':ref['value']},T-100_000_000)
            emit('FIELD',{'con_id':1,'field':'last','value':7700},T)
            self.assertEqual(coord.engine.frozen['spot']['value'],7604)
            emit('MARKET_BARRIER',{},T)
            emit('TIMER',{},T+15_000_000_000)
            c.store.close()
            out=replay(d)
            self.assertTrue(out['trace_match']);self.assertTrue(out['result_match'])


if __name__=='__main__':unittest.main()
