from decimal import Decimal
import math
from pathlib import Path
from statistics import NormalDist
import tempfile
import unittest
from unittest.mock import patch

from spxlab.contracts import Clock,ContractError,validate_plan
from spxlab.diagnostics import prediction_diagnostics,reselection_ablation
from spxlab.distribution import expected_payoff
from spxlab.frozen import freeze_run
from spxlab.observer import Observer
from spxlab.quotes import quote_universe
from test_research_core import ASOF,plan,samples,batch
from test_core import Feed,T

ROOT=Path(__file__).resolve().parents[1]


class MathObserverTests(unittest.TestCase):
    def test_A05_normal_closed_form_against_direct_quadrature_and_common_samples(self):
        normal=NormalDist();n=2000
        for mu,sd,k,w in [(7600,10,7600,25),(7604,30,7595,25),(100,2,101,5),(0,3,-2,1)]:
            def call(strike):
                z=(mu-strike)/sd
                return (mu-strike)*normal.cdf(z)+sd*math.exp(-z*z/2)/math.sqrt(2*math.pi)
            closed=call(k-w)-2*call(k)+call(k+w)
            dx=2*w/n
            weights=[];values=[]
            # Integrate only nonzero payoff; split at the center kink.
            integral=0
            for lo,hi in [(k-w,k),(k,k+w)]:
                step=(hi-lo)/n
                for i in range(n+1):
                    x=lo+i*step
                    pdf=math.exp(-((x-mu)/sd)**2/2)/(sd*math.sqrt(2*math.pi))
                    integral+=(1 if i in (0,n) else 4 if i%2 else 2)*max(w-abs(x-k),0)*pdf*step/3
            self.assertAlmostEqual(closed,integral,places=8)
            # Quantile-midpoint sample integration covers both tails, without truncation normalization.
            d=samples();count=10000
            d['samples']=[{'value':str(mu+sd*normal.inv_cdf((i+.5)/count)),'weight':'.0001'} for i in range(count)]
            self.assertLess(abs(float(expected_payoff(d,k,w)['point'])-closed),.002)

    def test_A18_atom_PIT_and_reselection_are_explicit(self):
        d=samples();r=prediction_diagnostics(d,7604,7600,25)
        self.assertEqual(r['pit_interval'],['0','1'])
        self.assertEqual(r['crps'],'0')
        b=batch();r=reselection_ablation(d,7500,b['universe'],{q['candidate_id']:q['debit_points'] for q in b['rows']})
        self.assertEqual(r['original_selected'],'C7600_W25')
        self.assertEqual(r['relocated_selected'],'C7595_W25')

    def test_A08_all_leg_diagnostics_preserved(self):
        f=Feed().setup()
        for c in f.state.contracts.values():
            if c['sec_type']=='OPT':
                c['expiry']='20260916';c['time_zone']='US/Central';c['liquid_hours']='20260916:0830-20260916:1500'
        f.event('FIELD',{'con_id':7580,'field':'ask_size','value':0})
        f.event('FIELD',{'con_id':7605,'field':'bid_size','value':1})
        f.event('FIELD',{'con_id':7630,'field':'ask_size','value':0})
        q=quote_universe(f.state,[{'candidate_id':'c','center':7605,'width':25}],Clock(f.seq,T,ASOF,'a'),validate_plan(plan()),synthetic=True)['rows'][0]
        self.assertEqual(len(q['leg_diagnostics']),3)
        self.assertTrue(all(x['issues'] for x in q['leg_diagnostics']))
        self.assertIn('INSUFFICIENT_SIZE',q['reasons'])

    def test_A13_packet_crossing_deadline_does_not_admit_future_projection(self):
        with tempfile.TemporaryDirectory() as t:
            directory=Path(t)/'run';p=freeze_run(plan('OBSERVE_ONLY_V1'),directory,ROOT)
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T14:04:59.999+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=10_000_000_000):
                c=Observer(directory,p)
                c.generation=1
                c.emit('CONNECTED',{})
                c.emit('SUBSCRIBED',{'con_id':100,'req_id':100})
                c.ib.client._tcpDataArrived()
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T14:05:00.001+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=10_002_000_000):
                c.emit('FIELD',{'con_id':100,'req_id':100,'field':'last','value':7600})
                self.assertEqual(c.journal.engine.valuation_count,0)
                c.ib.client._tcpDataProcessed()
                self.assertEqual(c.journal.engine.valuation_count,0)
                c.emit('TIMER',{})
            value=c.journal.engine.latest_valuation
            self.assertEqual(value['timing_issues'],['POST_DEADLINE_PACKET_INPUT'])
            self.assertEqual(c.journal.engine.books['DV1']['last_decision']['reasons'],['POST_DEADLINE_PACKET_INPUT'])
            c.journal.save(directory);c.store.close()

    def test_A13_due_timer_precedes_new_market_input_and_observe_has_no_intent(self):
        with tempfile.TemporaryDirectory() as t:
            directory=Path(t)/'run';p=freeze_run(plan('OBSERVE_ONLY_V1'),directory,ROOT)
            with patch('spxlab.observer.utc_now',return_value='2026-09-16T14:04:59+00:00'),patch('spxlab.observer.time.monotonic_ns',return_value=10_000_000_000):
                c=Observer(directory,p)
            with patch('spxlab.observer.utc_now',return_value=ASOF),patch('spxlab.observer.time.monotonic_ns',return_value=11_000_000_000):
                c.emit('CONNECTED',{})
            from spxlab.events import read_events
            events=list(read_events(directory/'events.sqlite'));kinds=[e['event_type'] for e in events]
            self.assertLess(kinds.index('EVALUATE'),kinds.index('CONNECTED'))
            self.assertNotIn('INTENT',kinds)
            self.assertEqual(c.journal.engine.latest_valuation['coverage']['qualified'],0)
            c.journal.save(directory);c.store.close()


if __name__=='__main__':unittest.main()
