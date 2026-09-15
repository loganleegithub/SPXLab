"""Retrospective quote-eligibility sensitivity for an already frozen FP intent.

Does not create decisions, fills, or P&L. Keeps the original 15-second window.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from spxlab.engine import D, combo_quote, confirmed_side, estimate_fee
from spxlab.events import atomic_json, read_events, utc_now
from spxlab.market import MarketState


def observation(state, center, cfg, now, required_age, opposite_age, skew):
    prices, marks = [], []
    for strike, side, quantity in ((center-cfg['width'], 'ask', 1),
                                  (center, 'bid', 2), (center+cfg['width'], 'ask', 1)):
        cids = [cid for cid, c in state.contracts.items() if c['sec_type']=='OPT'
                and c['symbol']=='SPX' and c['trading_class']=='SPXW' and c['right']=='C'
                and c['expiry'][:8]==cfg['expiry'] and c['currency']=='USD'
                and c['multiplier']=='100' and D(c['strike'])==strike]
        if len(cids)!=1:
            return None, 'CONTRACT'
        quotes = {}
        for s in ('bid', 'ask'):
            q, reason = confirmed_side(state, cids[0], s, now,
                                       required_age if s==side else opposite_age)
            if q is None:
                return None, reason
            quotes[s] = q
        bid, ask = (D(quotes[s]['price']['value']) for s in ('bid', 'ask'))
        if bid<0 or ask<=0 or ask<bid:
            return None, 'INVALID_OR_CROSSED_BOOK'
        if D(quotes[side]['size']['value'])<quantity:
            return None, 'INSUFFICIENT_SIZE'
        prices.append(quotes[side]['price']['value'])
        marks.append(quotes[side]['confirmation']['mono'])
    if max(marks)-min(marks)>skew*1e9:
        return None, 'CROSS_LEG_TIME_SKEW'
    debit = D(prices[0])-2*D(prices[1])+D(prices[2])
    if not 0<debit<D(cfg['width']):
        return None, 'INVALID_COMBO_DEBIT'
    fees = estimate_fee(prices, cfg['fees'])
    if fees is None:
        return None, 'FEE_MODEL_MISSING'
    return str(debit+D(fees['estimated_usd'])/100), None


def review(directory, output):
    directory = Path(directory)
    cfg = json.loads((directory/'plan.json').read_text())
    result = json.loads((directory/'decision.json').read_text())
    book = result['books']['FP']
    scenarios = [
        ('original', 1, 2, 1, 3),
        ('required_age_2_only', 2, 2, 1, 3),
        ('required_age_5_only', 5, 2, 1, 3),
        ('cross_leg_skew_3_only', 1, 2, 3, 3),
        ('required_age_3_and_skew_3', 3, 2, 3, 3),
        ('intent_lifetime_10_only', 1, 2, 1, 10),
        ('intent_lifetime_15_only', 1, 2, 1, 15),
    ]
    rows = {name: {'required_age_s': age, 'opposite_age_s': opposite, 'skew_s': skew,
                   'intent_lifetime_s': ttl, 'frames': 0, 'qualified_frames': 0,
                   'within_original_cost_cap_frames': 0, 'first_within_cap': None,
                   'reasons': Counter()}
            for name, age, opposite, skew, ttl in scenarios}
    state = MarketState(); window_end = None; baseline_checks = 0; last = None
    unchanged_quality_checks = 0; unchanged_quality_positive_checks = 0
    for e in read_events(directory/'events.sqlite'):
        state.apply(e); last = e
        if e['event_type']=='CUTOFF':
            window_end = e['payload']['scheduled_mono']+int(cfg['window_seconds']*1e9)
        if window_end is not None and e['monotonic_ns']>=window_end:
            break
        now = e['monotonic_ns']
        if now<book['eligible_mono'] or e['event_type'] not in ('MARKET_BARRIER','TIMER','HEARTBEAT'):
            continue
        for name, age, opposite, skew, ttl in scenarios:
            if now>=min(window_end,book['intent_mono']+int(ttl*1e9)):
                continue
            row = rows[name]; row['frames'] += 1
            cost, reason = observation(state,book['center'],cfg,now,age,opposite,skew)
            if (age, opposite, skew)==(1, 2, 1):
                q, why = combo_quote(state,book['center'],cfg,now)
                assert (cost,reason)==(q['all_in_points'] if q else None,why)
                unchanged_quality_checks += 1
                unchanged_quality_positive_checks += int(q is not None)
            if name=='original':
                baseline_checks += 1
            row['reasons'][reason or 'QUALIFIED'] += 1
            if cost is not None:
                row['qualified_frames'] += 1
                if D(cost)<=D(cfg['max_all_in_points']):
                    row['within_original_cost_cap_frames'] += 1
                    if row['first_within_cap'] is None:
                        row['first_within_cap'] = {'seq':e['seq'],'utc':e['recorded_at'],
                                                  'all_in_points':cost}
    assert baseline_checks>0
    out = {'generated_at':utc_now(),'classification':'RETROSPECTIVE_ENGINEERING_SENSITIVITY',
           'not_a_trade_or_profit_result':True,'center_fixed':book['center'],
           'intent_seq_fixed':book['intent_seq'],'cost_cap_fixed':cfg['max_all_in_points'],
           'original_window_seconds_fixed':cfg['window_seconds'],
           'quantity_requirement_fixed':[1,2,1],
           'original_function_comparisons_passed':baseline_checks,
           'unchanged_quality_function_comparisons_passed':unchanged_quality_checks,
           'unchanged_quality_positive_comparisons_passed':unchanged_quality_positive_checks,
           'verified_event_prefix_end':{'seq':last['seq'],'hash':last['hash']},
           'input_sha256':{f:hashlib.sha256((directory/f).read_bytes()).hexdigest()
                           for f in ('plan.json','decision.json')},
           'scenarios':rows,
           'caveat':'Correlated observations of one fixed intent; no independent samples, fills, '
                    'orders or P&L. Cannot establish better strategy or safe quote age.'}
    atomic_json(output,out)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True); parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(review(args.directory,args.output),ensure_ascii=False,indent=2))
