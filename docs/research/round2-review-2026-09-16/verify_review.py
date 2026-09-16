"""Independent review calculations; synthetic examples, not a strategy module.

Uses the supplied reference read-only. Records limitations as counterexamples,
not proposed fixes to the external file. Requires NumPy 2.3.5 / SciPy 1.17.0.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import sys
import unittest
from zoneinfo import ZoneInfo

import numpy as np
import scipy
from scipy.integrate import quad
from scipy.optimize import linprog

PARSER = argparse.ArgumentParser()
PARSER.add_argument('--reference', type=Path, required=True)
PARSER.add_argument('--output', type=Path, required=True)
ARGS = PARSER.parse_args()
SPEC = importlib.util.spec_from_file_location('external_reference', ARGS.reference)
REF = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REF
SPEC.loader.exec_module(REF)
RESULTS = {}


class IndependentChecks(unittest.TestCase):
    def test_randomized_integrals_and_derivatives(self):
        rng = np.random.default_rng(16092026)
        errors = []
        for _ in range(150):
            mu, sigma = rng.uniform(-30, 30), rng.uniform(2, 60)
            k, w = rng.uniform(-40, 40), rng.uniform(1, 40)
            p = REF.Normal(mu, sigma)
            val = quad(lambda x: max(w-abs(x-k), 0)*p.pdf(x), k-w, k+w,
                       points=[k], epsabs=1e-10)[0]
            layer = quad(lambda u: p.cdf(k+u)-p.cdf(k-u), 0, w, epsabs=1e-10)[0]
            errors.append(abs(val-p.value(k, w)))
            self.assertAlmostEqual(val, p.value(k, w), delta=2e-10)
            self.assertAlmostEqual(val, layer, delta=2e-10)
            h = 1e-4
            dk, dw = p.gradient(k, w)
            self.assertAlmostEqual(dk, (p.value(k+h,w)-p.value(k-h,w))/(2*h), delta=2e-8)
            self.assertAlmostEqual(dw, (p.value(k,w+h)-p.value(k,w-h))/(2*h), delta=2e-8)
        RESULTS['randomized_math'] = {'cases': 150, 'max_value_error': max(errors)}

    def test_discrete_atoms_require_one_sided_derivatives(self):
        k, w, h = 0., 25., 1e-5
        at_center = lambda center: max(w-abs(center),0)
        left = (at_center(k)-at_center(k-h))/h
        right = (at_center(k+h)-at_center(k))/h
        self.assertAlmostEqual(left, 1., places=7)
        self.assertAlmostEqual(right, -1., places=7)
        RESULTS['atom_center_derivatives'] = {'left': left, 'right': right,
                                               'conclusion': 'No ordinary derivative at a mass atom'}

    def test_same_median_and_band_opposite_economics(self):
        # Both symmetric distributions have the same unique median 0,
        # and exact mass 0.5 in [-30,30]. Wings +/-25; costs are invented.
        def v(xs, ws):
            return sum(a*max(25-abs(x),0) for x,a in zip(xs,ws))
        weights = [.25,.245,.01,.245,.25]
        near = v([-100,-1,0,1,100],weights)
        far = v([-100,-29,0,29,100],weights)
        self.assertGreater(near-6.25, 0)
        self.assertLess(far-6.25, 0)
        RESULTS['identical_public_summary'] = {'near_value':near,'far_value':far,
            'cost':6.25,'near_edge':near-6.25,'far_edge':far-6.25}

    def test_actual_band_geometry_without_density_assumption(self):
        # Uses known geometry only; exact 50% mass is a hypothetical constraint,
        # not empirical calibration or resolution of the target-date ambiguity.
        xs=np.array([7500,7575,7580,7604,7605,7630,7633,7700.])
        b=np.maximum(25-np.abs(xs-7605),0)
        band=((xs>=7575)&(xs<=7633)).astype(float)
        # Median definition: P(S<m)<=.5 and P(S>m)<=.5, allowing atoms.
        inequalities=np.array([(xs<7604).astype(float),(xs>7604).astype(float)])
        kwargs=dict(A_eq=np.array([np.ones(len(xs)),band]),b_eq=[1,.5],
                    A_ub=inequalities,b_ub=[.5,.5],bounds=(0,None),method='highs')
        lo,hi=linprog(b,**kwargs),linprog(-b,**kwargs)
        self.assertTrue(lo.success and hi.success)
        self.assertAlmostEqual(lo.fun,0)
        self.assertAlmostEqual(-hi.fun,12.5)
        # Analytic enclosure is the same for arbitrary support: the fly's
        # support lies wholly inside the band; at most half mass pays <=25.
        RESULTS['public_band_geometry']={'K':7605,'w':25,'median':7604,
            'band':[7575,7633],'assumed_mass':.5,'payoff_bounds':[lo.fun,-hi.fun],
            'edge_at_illustrative_cost_6_25':[lo.fun-6.25,-hi.fun-6.25],
            'warning':'Conditional bounds, not calibrated confidence bounds or a historical trade'}

    def test_finite_support_grid_can_hide_uncertainty(self):
        grid=REF.finite_support_bounds([-50,0,50],0,25,[(-25,.25,.25),(25,.75,.75)])
        # Same constraints allow .25 at each of -50,-24.999,25,50.
        alternative=.25*REF.butterfly(-24.999,0,25)
        self.assertEqual(grid,(12.5,12.5))
        self.assertLess(alternative,.001)
        RESULTS['grid_counterexample']={'grid_bounds':grid,'allowed_off_grid_value':alternative,
                                        'continuous_infimum_supremum':[0,12.5]}

    def test_model_envelope_not_statistical_confidence(self):
        assumed=REF.Normal(0,10).value(0,25)
        omitted=REF.Normal(0,60).value(0,25)
        self.assertGreater(assumed-6.25,0)
        self.assertLess(omitted-6.25,0)
        RESULTS['model_set_dependence']={'assumed_edge':assumed-6.25,'omitted_edge':omitted-6.25}

    def test_naive_fill_probability_can_reverse_sign(self):
        outcomes=np.array([10.,-6.])
        fill=np.array([0.,1.])
        unconditional=outcomes.mean()
        naive=fill.mean()*unconditional
        actual=(fill*outcomes).mean()
        self.assertGreater(naive,0)
        self.assertLess(actual,0)
        RESULTS['fill_selection']={'unconditional_EV':unconditional,
            'P_fill':fill.mean(),'naive_product':naive,'fill_conditioned_EV':actual}

    def test_reusing_old_density_ignores_new_information(self):
        # Morning half mass at -40, half at +40; afternoon reveals the branch.
        morning=.5*REF.butterfly(40,40,25)+.5*REF.butterfly(-40,40,25)
        current=REF.butterfly(40,40,25)
        self.assertEqual((morning,current),(12.5,25))
        RESULTS['conditioning_counterexample']={'morning_value':morning,'updated_value':current,
                                               'new_information':'terminal branch revealed in toy world'}

    def test_expectation_only_does_not_identify_profit_probability(self):
        a=np.array([10.,10.]);b=np.array([0.,20.]);cost=6.25
        self.assertEqual(a.mean(),b.mean())
        self.assertNotEqual((a>cost).mean(),(b>cost).mean())
        RESULTS['mean_only']={'expected_payoff':10,'profit_probabilities':[1,.5]}

    def test_waiting_ties_and_reversed_mixture(self):
        self.assertEqual(REF.optimal_entry([[1],[1]],[[[1]]])['actions'][0],['WAIT'])
        self.assertEqual(REF.optimal_entry([[0]],[])['actions'][0],['ABSTAIN'])
        vc,vu,c=4.,17.,6.25
        threshold=(c-vu)/(vc-vu)
        self.assertGreater(.1*vc+.9*vu-c,0)
        self.assertLess(.9*vc+.1*vu-c,0)
        RESULTS['reversed_mixture']={'threshold':threshold,'positive_EV_requires':'pi below threshold'}

    def test_empty_universe_is_abstain_in_reference(self):
        self.assertEqual(REF.rank_candidates([], [REF.Normal(0,10)],6.25)['decision'],'ABSTAIN')
        RESULTS['empty_universe_reference_behavior']='ABSTAIN; production must distinguish missing coverage'

    def test_new_york_schedule_varies_in_utc(self):
        stamps=[]
        for day in ('2026-03-06','2026-03-09','2026-10-30','2026-11-02'):
            dt=datetime.fromisoformat(day+'T10:05:00').replace(tzinfo=ZoneInfo('America/New_York'))
            stamps.append(dt.astimezone(timezone.utc).isoformat())
        self.assertEqual([s[11:16] for s in stamps],['15:05','14:05','14:05','15:05'])
        RESULTS['NY_1005_UTC']=stamps


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IndependentChecks))
    out={'classification':'REVIEW_CALCULATIONS_NOT_MARKET_OR_PROFIT_EVIDENCE',
         'verified_at':datetime.now(timezone.utc).isoformat(),
         'runtime':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__},
         'reference_sha256':hashlib.sha256(ARGS.reference.read_bytes()).hexdigest(),
         'checks':{'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
                   'passed':result.wasSuccessful()},'results':RESULTS}
    ARGS.output.write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
