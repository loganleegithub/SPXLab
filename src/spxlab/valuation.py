"""Pure full-universe valuation with cash and explicit unidentified value."""
from copy import deepcopy
from decimal import Decimal

from .contracts import digest, fields, number, require, stamp, versioned
from .distribution import distribution_issues, expected_payoff


def value_candidates(distribution, batch, spec, mode):
    versioned(batch)
    fields(batch, ('universe', 'rows', 'as_of', 'target_at', 'snapshot_seq', 'classification'))
    require(mode in {'SYNTHETIC_REPLAY_V1', 'OBSERVE_ONLY_V1', 'VALUE_RESEARCH_SHADOW_V1'}, 'Unknown mode')
    require(mode == 'SYNTHETIC_REPLAY_V1' or batch['classification'] == 'MARKET_OBSERVATION',
            'Synthetic quotes cannot enter real market research')
    planned = [c['candidate_id'] for c in batch['universe']]
    require(len(set(planned)) == len(planned), 'Duplicate planned candidate')
    quotes = {q['candidate_id']: q for q in batch['rows']}
    require(len(quotes) == len(batch['rows']) and set(quotes) <= set(planned), 'Duplicate/unplanned quote')
    issues = distribution_issues(distribution, batch['as_of'], batch['target_at'], spec, mode)
    budget, threshold = number(spec['budget_points']), number(spec['min_edge_points'])
    require(budget > 0 and threshold >= 0, 'Invalid budget/threshold')
    rows = []
    for candidate in batch['universe']:
        fields(candidate, ('candidate_id', 'center', 'width'))
        width = number(candidate['width'])
        require(width > 0, 'Positive width required')
        q = quotes.get(candidate['candidate_id'])
        row = {**deepcopy(candidate), 'quote': deepcopy(q), 'reasons': list(issues),
               'payoff_point': None, 'payoff_lower': None, 'payoff_upper': None,
               'edge_lower': None, 'edge_upper': None, 'cost_points': None, 'max_loss_usd': None,
               'uncertainty_kind': None, 'eligible': False}
        if q is None:
            row['reasons'].append('CANDIDATE_QUOTE_MISSING')
        else:
            row['reasons'].extend(q.get('reasons', []))
            require(q['snapshot_seq'] == batch['snapshot_seq'], 'Mixed quote snapshots')
            if q.get('debit_points') is not None and q.get('fees_usd') is not None:
                debit, fees = number(q['debit_points']), number(q['fees_usd'])
                require(fees >= 0, 'Negative fees')
                cost = debit + fees/100
                row.update(cost_points=str(cost), max_loss_usd=str(cost*100))
                if not 0 < debit < width:
                    row['reasons'].append('INVALID_COMBO_DEBIT')
                if cost >= width:
                    row['reasons'].append('DOMINATED_COST')
                if cost > budget:
                    row['reasons'].append('COST_CAP')
            else:
                row['reasons'].append('COST_UNKNOWN')
        if distribution is not None and 'MODEL_NOT_YET_AVAILABLE' not in issues and 'MODEL_TARGET_MISMATCH' not in issues:
            v = expected_payoff(distribution, candidate['center'], width)
            row.update(payoff_point=str(v['point']) if v['point'] is not None else None,
                       payoff_lower=str(v['lower']), payoff_upper=str(v['upper']), uncertainty_kind=v['kind'])
            if v['kind'] not in spec['allowed_uncertainty']:
                row['reasons'].append('UNCERTAINTY_NOT_ELIGIBLE')
            if row['cost_points'] is not None:
                low, high = v['lower']-number(row['cost_points']), v['upper']-number(row['cost_points'])
                row.update(edge_lower=str(low), edge_upper=str(high))
                if low <= threshold:
                    row['reasons'].append('VALUE_UNIDENTIFIED' if high > threshold else 'VALUE_NOT_ABOVE_THRESHOLD')
        row['reasons'] = sorted(set(row['reasons']))
        row['eligible'] = not row['reasons']
        rows.append(row)
    quote_qualified = [r for r in rows if r['quote'] is not None and not r['quote'].get('reasons')
                       and r['cost_points'] is not None and 'INVALID_COMBO_DEBIT' not in r['reasons']]
    complete = not batch.get('universe_issues') and bool(planned) and len(quote_qualified) == len(planned)
    eligible = sorted([r for r in rows if r['eligible']],
                      key=lambda r: (-number(r['edge_lower']), number(r['cost_points']), number(r['width']),
                                     number(r['center']), r['candidate_id']))
    known_rejections = {'COST_CAP', 'DOMINATED_COST', 'VALUE_NOT_ABOVE_THRESHOLD'}
    unknown = (not complete or any(set(r['reasons'])-known_rejections for r in rows))
    selected = eligible[0]['candidate_id'] if complete and eligible else None
    state = 'CANDIDATE' if selected else ('UNKNOWN' if unknown else 'ABSTAIN')
    return {'schema_version': 2, 'as_of': batch['as_of'], 'target_at': batch['target_at'],
            'snapshot_seq': batch['snapshot_seq'], 'universe_hash': digest(batch['universe']),
            'distribution_hash': digest(distribution) if distribution else None, 'batch_hash': digest(batch),
            'rows': rows, 'selected': selected, 'state': state, 'cash_value_points': '0',
            'universe_issues': batch.get('universe_issues', []),
            'coverage': {'planned': len(planned), 'qualified': len(quote_qualified), 'complete': complete},
            'limitations': ['Natural leg prices imply a shadow assumption, not a guaranteed combination fill',
                            'Value estimates are conditional on their declared information and model class']}


def render_valuation(result):
    lines = ['# 蝶式价值计算', '', f"状态：**{result['state']}**；选中：{result['selected'] or '现金/未知'}。",
             f"信息时点：{result['as_of']}；同期报价序号：{result['snapshot_seq']}。", '',
             '| 候选 | 中心/翼宽 | 期望兑付界限(点) | 含费成本(点) | EV界限(点) | 最大损失(美元) | 原因 |',
             '|---|---|---|---|---|---|---|']
    for r in result['rows']:
        lines.append(f"| {r['candidate_id']} | {r['center']}/{r['width']} | {r['payoff_lower']} … {r['payoff_upper']} | "
                     f"{r['cost_points']} | {r['edge_lower']} … {r['edge_upper']} | {r['max_loss_usd']} | "
                     f"{', '.join(r['reasons']) or '合格'} |")
    lines += ['', '现金价值为0；null表示未知。模型界限不是自动校准的统计置信区间。',
              '每行最大损失只适用于完整结构和声明的常数费用；本表不是券商成交或收益验证。', '']
    return '\n'.join(lines)
