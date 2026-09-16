"""Day-level paired evaluation, missingness and model-location attribution."""
from copy import deepcopy
from decimal import Decimal
import json
import math
from pathlib import Path
import random
import statistics

from .contracts import digest, file_hash, fields, number, require, stamp, versioned
from .distribution import expected_payoff, validate_distribution


def sample_crps(distribution, observation):
    validate_distribution(distribution)
    require(distribution['representation'] == 'WEIGHTED_SAMPLES', 'CRPS needs a full sample distribution')
    samples = sorted((number(s['value']), number(s['weight'])) for s in distribution['samples'])
    y, before, pair_term = number(observation), Decimal(0), Decimal(0)
    for x, w in samples:
        pair_term += w*x*(2*before+w-1)
        before += w
    return str(sum(w*abs(x-y) for x,w in samples)-pair_term)


def location_attribution(distribution, reference, selected, baseline, width, selected_cost, baseline_cost):
    validate_distribution(distribution)
    require(distribution['representation'] == 'WEIGHTED_SAMPLES', 'Location attribution needs common samples')
    ordered = sorted(distribution['samples'], key=lambda s: number(s['value']))
    mass, median = Decimal(0), None
    for row in ordered:
        mass += number(row['weight'])
        if mass >= Decimal('.5'):
            median = number(row['value'])
            break
    zero = deepcopy(distribution)
    for row in zero['samples']:
        row['value'] = str(number(row['value'])-median+number(reference))
    v = lambda model, k: expected_payoff(model, k, width)['point']
    a, b, a0, b0 = v(distribution,selected), v(distribution,baseline), v(zero,selected), v(zero,baseline)
    location, shape = (a-a0)-(b-b0), a0-b0
    price = -(number(selected_cost)-number(baseline_cost))
    total = a-number(selected_cost)-b+number(baseline_cost)
    require(total == location+shape+price, 'Attribution identity failed')
    return {'distribution_hash': digest(distribution), 'kind': 'FIXED_STRUCTURE_MEDIAN_RELOCATION',
            'reference': str(number(reference)), 'median': str(median),
            'location_structure_interaction_points': str(location), 'relocated_shape_points': str(shape),
            'price_cost_points': str(price), 'total_edge_difference_points': str(total),
            'delta': None, 'delta_reason': 'No qualified timestamped Greeks supplied',
            'limitation': 'Algebraic model decomposition; not hedge P&L or causal removal of all directional risk'}


def _curve(values):
    wealth, peak, drawdown = Decimal(0), Decimal(0), Decimal(0)
    for value in values:
        wealth += number(value)
        peak = max(peak, wealth)
        drawdown = max(drawdown, peak-wealth)
    return {'cumulative_usd': str(wealth), 'maximum_drawdown_usd': str(drawdown)}


def _block_interval(values, length, repeats, seed):
    rng, n, means = random.Random(seed), len(values), []
    for _ in range(repeats):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n-length+1)
            sample.extend(values[start:start+length])
        means.append(statistics.mean(sample[:n]))
    means.sort()
    return [means[int(.025*(repeats-1))], means[int(.975*(repeats-1))]]


def compare_rows(study, rows):
    """Rows must contain at most one result per declared day and strategy."""
    versioned(study)
    fields(study, ('study_id', 'planned_sessions', 'strategies', 'primary_comparison',
                   'training_sessions', 'purpose', 'inference'))
    require(study.get('classification') in {'SYNTHETIC_FIXTURE','SHADOW_RESEARCH','OBSERVATION_ONLY','FIELD_PAPER'}, 'Explicit study evidence class required')
    require(study['purpose'] in {'DESCRIPTIVE', 'FROZEN_EVALUATION'}, 'Unknown evaluation purpose')
    require(len(set(study['strategies'])) == len(study['strategies']), 'Duplicate strategy')
    planned = study['planned_sessions']
    require(planned == sorted(set(planned)), 'Unique chronological planned sessions required')
    require(not set(planned) & set(study['training_sessions']), 'Training/evaluation day overlap')
    by_key = {}
    for row in rows:
        key = row['session'], row['strategy_id']
        require(key not in by_key and row['session'] in planned and row['strategy_id'] in study['strategies'],
                'Duplicate/unplanned day or strategy; do not select a best run')
        if row['net_pnl_usd'] is not None:
            number(row['net_pnl_usd'])
        by_key[key] = row
    summaries = {}
    for strategy in study['strategies']:
        series = [by_key.get((day,strategy), {}).get('net_pnl_usd') for day in planned]
        observed = [x for x in series if x is not None]
        complete = len(observed) == len(series) and bool(series)
        summaries[strategy] = {'planned_days': len(planned), 'known_days': len(observed),
                               'unknown_days': len(planned)-len(observed), 'complete': complete,
                               'observed_subset_sum_usd': str(sum(map(number,observed), Decimal(0))),
                               **(_curve(series) if complete else {'cumulative_usd': None,'maximum_drawdown_usd':None})}
    pair = study['primary_comparison']
    fields(pair, ('candidate', 'baseline', 'require_matched_valuation'))
    require(pair['candidate'] in study['strategies'] and pair['baseline'] in study['strategies'] and pair['candidate'] != pair['baseline'], 'Invalid primary pair')
    differences, missing, unmatched = [], [], []
    for day in planned:
        a,b = (by_key.get((day,pair[key])) for key in ('candidate','baseline'))
        if not a or not b or a['net_pnl_usd'] is None or b['net_pnl_usd'] is None:
            missing.append(day)
            continue
        if pair['require_matched_valuation'] and (not a.get('valuation_hash') or a['valuation_hash'] != b.get('valuation_hash')):
            unmatched.append(day)
            continue
        differences.append({'session':day,'difference_usd':str(number(a['net_pnl_usd'])-number(b['net_pnl_usd']))})
    values = [float(number(r['difference_usd'])) for r in differences]
    inference = study['inference']
    fields(inference, ('min_independent_days','block_length','bootstrap_repeats','seed','economic_delta_usd','precision_usd'))
    minimum, block, repeats = (inference[k] for k in ('min_independent_days','block_length','bootstrap_repeats'))
    require(type(minimum) is int and minimum >= 2 and type(block) is int and block >= 1,
            'Declared day-level inference settings required')
    require(type(repeats) is int and repeats >= 1000, 'At least 1000 declared bootstrap replicates required')
    for name in ('economic_delta_usd', 'precision_usd'):
        if inference[name] is not None or study['purpose'] == 'FROZEN_EVALUATION':
            require(number(inference[name]) > 0, 'Positive declared effect/precision required')
    interval = None
    inference_ready = not missing and not unmatched and len(values) >= max(minimum,2*block)
    if inference_ready and study['purpose'] == 'FROZEN_EVALUATION':
        require(study.get('preregistration_hash') and study.get('frozen_at'), 'Frozen evaluation evidence required')
        require(stamp(study['frozen_at']).date().isoformat() < planned[0], 'Study must freeze before evaluation days')
        interval = _block_interval(values,block,repeats,inference['seed'])
    return {'schema_version':2,'study_id':study['study_id'],'study_hash':digest(study),
            'scope':'SHADOW_RESULTS_WITH_DECLARED_FEE_SCENARIO_NOT_LIVE_EXECUTION',
            'classification':study['classification'],
            'strategy_summaries':summaries,'primary_comparison':pair,
            'paired_differences':differences,'missing_pair_days':missing,'unmatched_pair_days':unmatched,
            'mean_paired_difference_usd': statistics.mean(values) if values else None,
            'paired_day_standard_deviation_usd':statistics.stdev(values) if len(values)>1 else None,
            'interval_95_usd':interval,'interval_method':'MOVING_DAY_BLOCK_BOOTSTRAP' if interval else None,
            'economic_delta_usd':inference['economic_delta_usd'],'precision_usd':inference['precision_usd'],
            'conclusion':'DESCRIPTIVE_ONLY' if interval is None else 'FROZEN_SHADOW_ESTIMATE_REQUIRES_ECONOMIC_REVIEW',
            'trading_edge_established':False,
            'limitations':['Unknown days never become zero; paired observable subset is separately identified',
                           'Counterfactual strategies are not added into a portfolio',
                           'Bootstrap precision does not establish a reliable tail with few independent days']}


def compare_study(study):
    rows = []
    expected_modes={'SYNTHETIC_FIXTURE':'SYNTHETIC_REPLAY_V1','SHADOW_RESEARCH':'VALUE_RESEARCH_SHADOW_V1','OBSERVATION_ONLY':'OBSERVE_ONLY_V1','FIELD_PAPER':'FIELD_PAPER_V1'}
    for source in study['runs']:
        directory = Path(source['directory'])
        target = directory/source['settlement_file']
        require(target.resolve().is_relative_to(directory.resolve()), 'Settlement path outside run')
        require(file_hash(target) == source['settlement_sha256'], 'Settlement result changed')
        result = json.loads(target.read_text())
        require(result['mode'] == expected_modes.get(study.get('classification')), 'Study/run mode mixing prohibited')
        require(result['plan_hash'] == source['plan_hash'], 'Unexpected plan')
        for key, book in result['books'].items():
            rows.append({'session':result['session'],'strategy_id':key,
                         'net_pnl_usd':book.get('net_pnl_estimated_fees_usd'),
                         'valuation_hash':(book.get('intent') or {}).get('valuation_hash') or
                                           book.get('last_decision',{}).get('valuation_hash'),
                         'fixed_costs':result['fixed_costs']})
    report = compare_rows(study,rows)
    report['fixed_costs_status'] = 'UNKNOWN' if any(r['fixed_costs']['status']=='UNKNOWN' for r in rows) else 'KNOWN_SEPARATELY_REPORTED'
    report['profit_scope'] = 'DIRECT_ESTIMATED_FEES_ONLY'
    return report
