"""FIELD M3: one-step continuation under P, quote-derived local Q repricing."""
from functools import lru_cache
from datetime import timedelta
import math
from statistics import median

import numpy as np

from .contracts import require, stamp
from .field_model import condition, marginal, normal_butterfly, normal_call, payoff, transition


@lru_cache(maxsize=4)
def nodes(count):
    x,w = np.polynomial.hermite.hermgauss(count)
    return list(zip((x*math.sqrt(2)).tolist(),(w/math.sqrt(math.pi)).tolist()))


def implied_sd(spot, strike, price):
    intrinsic = max(spot-strike,0.)
    require(price > intrinsic+1e-5, 'LEG_HAS_NO_IDENTIFIABLE_TIME_VALUE')
    lo,hi = 0.,spot*.25
    require(normal_call(spot,hi,strike) > price, 'Q_SCALE_OUTSIDE_DOMAIN')
    for _ in range(55):
        mid = (lo+hi)/2
        if normal_call(spot,mid,strike) < price: lo = mid
        else: hi = mid
    return (lo+hi)/2


def fit_quote(row, process):
    """One local normal variance rate per structure, identified from leg mids.

    F≈S, zero rates. The same fitted scale reprices all three legs, preserving
    a nonnegative butterfly. An additive debit residual anchors natural price.
    """
    q = row['quote']
    k,w,s,t = float(row['center']),float(row['width']),process['spot'],process['minutes']
    refs = q['quote_refs']
    require(len(refs) == 3 and t > 0, 'Q_LEG_INPUT_MISSING')
    mids,spreads,scales = [],[],[]
    for ref,strike in zip(refs,(k-w,k,k+w)):
        bid,ask = (float(ref['fields'][side]['value']) for side in ('bid','ask'))
        require(0 <= bid <= ask, 'Q_INVALID_LEG')
        mids.append((bid+ask)/2);spreads.append(ask-bid)
        scales.append(implied_sd(s,strike,mids[-1]))
    sd = median(scales)
    residuals = [normal_call(s,sd,strike)-mid for strike,mid in zip((k-w,k,k+w),mids)]
    require(all(abs(r) <= max(2.,2*spread) for r,spread in zip(residuals,spreads)), 'Q_LOCAL_CURVE_MISFIT')
    model_debit = normal_butterfly(s,sd,k,w)
    adjustment = float(q['debit_points'])-model_debit
    require(adjustment >= 0, 'Q_NEGATIVE_EXECUTION_ADJUSTMENT')
    return {'candidate_id':row['candidate_id'],'center':k,'width':w,'q_variance_per_minute':sd*sd/t,
            'natural_anchor_adjustment':adjustment,'leg_mid_residuals':residuals,
            'fee_points':float(q['fees_usd'])/100,'current_model_debit':model_debit,
            'assumptions':['F_APPROX_SPOT','ZERO_INTRADAY_INTEREST','FROZEN_NORMAL_Q_SCALE',
                           'FROZEN_ADDITIVE_NATURAL_EXECUTION_ADJUSTMENT']}


def continuation(process, fits, delta, budget=6.25, buffer=.25, count=15):
    require(0 < delta < process['minutes'], 'Invalid continuation horizon')
    total = 0.
    for component in process['components']:
        mean,var = transition(component,process['spot'],delta)
        for z,weight in nodes(count):
            future = mean+math.sqrt(var)*z
            require(future > 0, 'FUTURE_PROCESS_OUTSIDE_DOMAIN')
            terminal = marginal(condition(process,future,delta))
            best = 0.
            for fit in fits:
                debit = normal_butterfly(future,math.sqrt(fit['q_variance_per_minute']*(process['minutes']-delta)),
                                        fit['center'],fit['width'])+fit['natural_anchor_adjustment']
                cost = debit+fit['fee_points']
                # These are predicted costs, never eligible shadow fills. A
                # structure outside the budget/payoff domain is unavailable.
                if 0 < debit < fit['width'] and cost <= budget:
                    edge = payoff(terminal,fit['center'],fit['width'])-cost-buffer
                    best = max(best,edge)
            total += component['weight']*weight*best
    return total


def lookahead(distribution, value, plan):
    known = [r for r in value['rows'] if r.get('actionable_edge') is not None and
             not set(r['reasons'])-{'VALUE_NOT_ABOVE_THRESHOLD','COST_CAP','DOMINATED_COST'}]
    affordable = [r for r in known if 'COST_CAP' not in r['reasons'] and 'DOMINATED_COST' not in r['reasons']]
    # Missing input is not zero. Preserve negative values when prices are
    # known; zero only denotes an empty, known risk-admissible action set.
    h = max((float(r['actionable_edge']) for r in affordable),default=0. if known else None)
    enter_now = h is not None and h > 0
    base = {'version':'FIELD_ONE_STEP_V1','H_points':h,'C_points':None,'action':'WAIT',
            'forecast_only':True,'fits':[],'reasons':[],
            'scope':'CURRENT_QUALIFIED_CANDIDATES_ONLY'}
    if distribution is None or h is None:
        return {**base,'status':'TIMING_FALLBACK','reasons':['MODEL_UNAVAILABLE' if distribution is None else 'CURRENT_VALUE_INPUT_UNAVAILABLE']}
    remaining = (stamp(plan['schedule']['entry_end_utc'])-stamp(value['as_of'])).total_seconds()
    if remaining <= 120:
        # Entry window endpoint is exclusive: there is no new right to enter
        # at that endpoint. Truncating the step there therefore means C=0.
        return {**base,'status':'WINDOW_ENDPOINT','C_points':0.,'delta_seconds':max(0.,remaining),
                'action':'ENTER' if enter_now else 'WAIT','reasons':['NO_NEW_ENTRY_AT_WINDOW_ENDPOINT']}
    qualified = [r for r in value['rows'] if r['cost_points'] is not None and r['quote'] and
                 not r['quote']['reasons'] and 'INVALID_COMBO_DEBIT' not in r['reasons']]
    if not qualified:
        return {**base,'status':'TIMING_FALLBACK','reasons':['NO_QUALIFIED_REPRICING_QUOTES']}
    try:
        fits = [fit_quote(r,distribution['process']) for r in qualified]
        c15 = continuation(distribution['process'],fits,2.,count=15)
        c31 = continuation(distribution['process'],fits,2.,count=31)
        error = abs(c15-c31)
        base.update(fits=fits,quadrature_15=c15,quadrature_31=c31,integration_difference_points=error,
                    delta_seconds=120,verify_at=(stamp(value.get('scheduled_at',value['as_of']))+timedelta(seconds=120)).isoformat())
        require(error <= .05, 'CONTINUATION_INTEGRATION_UNSTABLE')
        # Close decisions need both resolutions on the same side. Otherwise
        # use the declared baseline, not a numerically fragile timing claim.
        require((h >= c15) == (h >= c31) or h <= 0, 'CONTINUATION_DECISION_NUMERICALLY_AMBIGUOUS')
        return {**base,'status':'OK','C_points':c31,'action':'ENTER' if enter_now and h >= c31 else 'WAIT'}
    except (ValueError,KeyError,ZeroDivisionError,OverflowError) as ex:
        return {**base,'status':'TIMING_FALLBACK','action':'ENTER' if enter_now else 'WAIT','reasons':[str(ex)]}
