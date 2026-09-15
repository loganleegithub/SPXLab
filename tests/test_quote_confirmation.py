import unittest
from test_core import Feed, config, T
from spxlab.engine import confirmed_side, combo_quote


def cfg():
    c=config();c['quote_policy']='SIDE_CONFIRMATION_V2';return c


class ConfirmationTests(unittest.TestCase):
    def test_quantity_and_cross_leg_limits_remain_enforced(self):
        f=Feed().setup()
        f.event('FIELD',{'con_id':7605,'field':'bid_size','value':1})
        self.assertEqual(combo_quote(f.state,7605,cfg(),T)[1],'INSUFFICIENT_SIZE')
        f=Feed().setup()
        for cid in (7580,7605,7630):
            for side in ('bid','ask'):
                f.event('FIELD',{'con_id':cid,'field':side+'_size','value':10},T+30_000_000_000)
        self.assertIsNone(combo_quote(f.state,7605,cfg(),T+30_000_000_000)[1])
        self.assertEqual(combo_quote(f.state,7605,cfg(),T+31_000_000_001)[1],'STALE_SIDE_ask')

    def test_size_confirms_unchanged_price_without_rewriting_timestamp(self):
        f=Feed().setup();before=dict(f.state.fields['7630']['ask'])
        for cid in (7580,7605,7630):
            for side in ('bid','ask'):
                f.event('FIELD',{'con_id':cid,'field':side+'_size','value':10},T+30_000_000_000)
        self.assertEqual(f.state.fields['7630']['ask'],before)
        quote,reason=combo_quote(f.state,7605,cfg(),T+30_000_000_000)
        self.assertIsNone(reason)
        self.assertIn('side_confirmation_refs',quote['quote_refs'][2])
        self.assertEqual(combo_quote(f.state,7605,config(),T+30_000_000_000)[1],'STALE_bid')

    def test_other_side_last_and_greeks_do_not_refresh_ask(self):
        f=Feed().setup()
        for field in ('bid_size','last','delta'):
            f.event('FIELD',{'con_id':7630,'field':field,'value':10},T+3_000_000_000)
        self.assertEqual(confirmed_side(f.state,7630,'ask',T+3_000_000_000,1)[1],'STALE_SIDE_ask')

    def test_zero_size_prevents_resurrection(self):
        f=Feed().setup()
        for value in (0,10):
            f.event('FIELD',{'con_id':7630,'field':'ask_size','value':value})
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')
        f.event('FIELD',{'con_id':7630,'field':'ask','value':1})
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':2})
        self.assertIsNone(confirmed_side(f.state,7630,'ask',T,1)[1])

    def test_unknown_price_and_invalid_price_are_not_restored_by_size(self):
        f=Feed().setup()
        f.event('FIELD',{'con_id':999,'field':'ask_size','value':5})
        f.event('MARKET_TYPE',{'con_id':999,'market_type':1})
        self.assertEqual(confirmed_side(f.state,999,'ask',T,1)[1],'UNCONFIRMED_ask')
        for value in (-1,None,0):
            f.event('FIELD',{'con_id':7630,'field':'ask','value':value})
            f.event('FIELD',{'con_id':7630,'field':'ask_size','value':10})
            self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')

    def test_new_subscription_filters_late_old_request(self):
        f=Feed().setup()
        f.event('SUBSCRIBED',{'con_id':7630,'req_id':20})
        f.event('FIELD',{'con_id':7630,'field':'ask','value':1,'req_id':20})
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':10,'req_id':20})
        f.event('FIELD',{'con_id':7630,'field':'ask','value':999,'req_id':19})
        q,reason=confirmed_side(f.state,7630,'ask',T,1)
        self.assertIsNone(reason);self.assertEqual(q['price']['value'],1)
        f.event('SUBSCRIBED',{'con_id':7630,'req_id':21})
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':10,'req_id':20})
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')

    def test_type_change_reconnect_and_old_generation(self):
        f=Feed().setup()
        for kind,payload in [('MARKET_TYPE',{'con_id':7630,'market_type':2}),
                             ('MARKET_TYPE',{'con_id':7630,'market_type':1})]:
            f.event(kind,payload)
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':10})
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')
        f.event('CONNECTED',generation=2)
        f.event('MARKET_TYPE',{'con_id':7630,'market_type':1},generation=2)
        f.event('FIELD',{'con_id':7630,'field':'ask','value':1},generation=1)
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':10},generation=2)
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')

    def test_size_before_price_and_regressing_mono_cannot_confirm(self):
        f=Feed().setup()
        f.event('FIELD',{'con_id':7630,'field':'ask','value':1},T)
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':10},T-1)
        self.assertEqual(confirmed_side(f.state,7630,'ask',T,1)[1],'UNCONFIRMED_ask')


if __name__=='__main__':unittest.main()
