"""Small offline bridge from reviewed originals to usable, honest value diagnostics.

Retrospective arithmetic never creates a backdated DistributionSnapshot or intent.
"""
from datetime import datetime, timezone
from pathlib import Path
import json

from .calendar import NY, session_schedule
from .contracts import fields, file_hash, number, require, stamp
from .dataset import dataset_check, residual_samples
from .distribution import butterfly, weighted_payoff
from .engine import nearest_center
from .valuation import value_candidates


def prepare_reviewed_dataset(inputs):
    fields(inputs, ('prepared_at', 'evaluation_sessions', 'time_bucket', 'min_independent_days', 'records'))
    rows, rejected, diagnostics = [], [], []
    for item in inputs['records']:
        try:
            for prefix in ('source', 'settlement'):
                require(file_hash(item[prefix+'_path']) == item[prefix+'_hash'], prefix+' hash mismatch')
            source = json.loads(Path(item['source_path']).read_text())
            settlement = json.loads(Path(item['settlement_path']).read_text())
            day = item['session']
            require(source['session'] == day, 'Source session mismatch')
            require(source['statistic_type'] == 'median', 'Explicit median required; no interval midpoint substitution')
            matches = [r for r in settlement['rows'] if r['session'] == day and r['series'] == 'SPXW_PM']
            require(len(matches) == 1, 'Unique SPXW PM settlement required')
            schedule = session_schedule(day)
            require(stamp(settlement['retrieved_at']) >= stamp(schedule['close_utc']), 'Settlement precedes target maturity')
            require(stamp(source['acquired_at']) >= stamp(source['published_at']), 'Receipt precedes source publication')
            decision = datetime.fromisoformat(day+'T'+inputs['time_bucket']).replace(tzinfo=NY).astimezone(timezone.utc)
            warnings = []
            if source['target_status'] != 'CONFIRMED': warnings.append('TARGET_DATE_ASSUMED')
            if source['revision_status'] != 'ORIGINAL_VERSION_VERIFIED': warnings.append('ORIGINAL_VERSION_UNVERIFIED')
            row = {'row_id':item['row_id'], 'session':day, 'classification':'RETROSPECTIVE_SOURCE',
                   'anchor_kind':'FORECAST', 'time_bucket':inputs['time_bucket'],
                   'anchor':str(number(source['median'])), 'scale':'1', 'outcome':str(number(matches[0]['value'])),
                   'decision_at':decision.isoformat(), 'feature_available_at':source['acquired_at'],
                   'target_at':schedule['close_utc'], 'outcome_available_at':settlement['retrieved_at'],
                   'source_hash':item['source_hash'], 'settlement_hash':item['settlement_hash'],
                   'source_url':source['source_url'], 'published_at':source['published_at'],
                   'source_issues':warnings, 'source_path':item['source_path'],
                   'settlement_path':item['settlement_path']}
            # These are reviewed historical claims, never prospective observations.
            if stamp(source['published_at']) > decision: warnings.append('POSTED_AFTER_FIXED_TIME')
            require(stamp(source['acquired_at']) <= stamp(inputs['prepared_at']), 'Source acquired after preparation')
            require(stamp(settlement['retrieved_at']) <= stamp(inputs['prepared_at']), 'Settlement acquired after preparation')
            rows.append(row)
            if stamp(source['published_at']) <= decision and (not inputs['evaluation_sessions'] or day < min(inputs['evaluation_sessions'])):
                diagnostics.append(row)
        except (ValueError, KeyError, OSError) as exc:
            rejected.append({'row_id':item.get('row_id'), 'reason':str(exc)})
    manifest = {'schema_version':2, 'training_cutoff':inputs['prepared_at'],
                'evaluation_sessions':inputs['evaluation_sessions'], 'anchor_kind':'FORECAST',
                'time_bucket':inputs['time_bucket'], 'min_independent_days':inputs['min_independent_days'],
                'allowed_classifications':['RETROSPECTIVE_SOURCE'], 'rows':rows}
    report = dataset_check(manifest)
    return {'manifest':manifest, 'check':report, 'rejected_inputs':rejected,
            'unavailable':inputs.get('unavailable', []), 'cohort':inputs.get('cohort'),
            'diagnostic_rows':diagnostics, 'diagnostic_independent_days':len({r['session'] for r in diagnostics}),
            'diagnostic_status':'RETROSPECTIVE_DATE_ASSUMPTION_ONLY',
            'warning':'Historical publication is not system receipt; original revisions and target dates remain unconfirmed'}


def retrospective_value_report(prepared, bundle):
    fields(bundle, ('batch', 'source', 'spot', 'generated_at'))
    batch, source = bundle['batch'], bundle['source']
    fields(source, ('median', 'published_at', 'available_at', 'target_date', 'source_url', 'status'))
    day = source['target_date']
    require(stamp(batch['target_at']) == stamp(session_schedule(day)['close_utc']), 'Source/quote target mismatch')
    require(stamp(source['published_at']) <= stamp(batch['as_of']), 'Source published after quote')
    require(stamp(bundle['generated_at']) >= stamp(prepared['manifest']['training_cutoff']), 'Report precedes data acquisition')
    rows = [r for r in prepared['diagnostic_rows'] if r['session'] < day and stamp(r['target_at']) < stamp(source['published_at'])]
    require(rows, 'No prior reviewed days for retrospective diagnostic')
    samples = residual_samples(rows, source['median'])
    spec = {'budget_points':'6.25', 'min_edge_points':'0', 'allowed_uncertainty':[], 'max_conditioning_age_seconds':30}
    result = value_candidates(None, batch, spec, 'OBSERVE_ONLY_V1')
    # value_candidates checks the real batch; no false past availability is supplied.
    for candidate in result['rows']:
        k, w = candidate['center'], candidate['width']
        point = weighted_payoff(samples, k, w)
        cost = number(candidate['cost_points']) if candidate['cost_points'] is not None else None
        by_day = sorted({r['session'] for r in rows})
        leave_one = [weighted_payoff(residual_samples([r for r in rows if r['session'] != d], source['median']), k, w)
                     for d in by_day] if len(by_day)>1 else []
        shifted = {str(shift):str(weighted_payoff(residual_samples(rows,number(source['median'])+shift),k,w)) for shift in (-5,0,5)}
        candidate.update(payoff_point=str(point), point_ev=str(point-cost) if cost is not None else None,
                         payoff_over_width=str(point/number(w)), cost_over_width=str(cost/number(w)) if cost is not None else None,
                         leave_one_day_payoff_range=[str(min(leave_one)),str(max(leave_one))] if leave_one else None,
                         anchor_shift_payoff_points=shifted, uncertainty_kind='POINT_ONLY',
                         breakevens=[str(number(k)-number(w)+cost),str(number(k)+number(w)-cost)] if cost is not None and 0<cost<number(w) else None)
        candidate['reasons'] = sorted(set(candidate['reasons'] + ['RETROSPECTIVE_RECONSTRUCTION','MODEL_NOT_READY',
            'UNCERTAINTY_NOT_ELIGIBLE','TARGET_DATE_ASSUMED','CONDITIONING_TIME_MISMATCH']))
    # Chronological reconstruction, with every scoring day excluded from its own fit.
    walk = []
    for current in sorted(rows,key=lambda r:r['session']):
        earlier = [r for r in rows if stamp(r['target_at']) < stamp(current['published_at'])]
        if not earlier: continue
        s = residual_samples(earlier,current['anchor'])
        k = nearest_center(current['anchor'])
        estimates = []
        for offset in (-5,0,5):
            pred = weighted_payoff(s,k+offset,25)
            realized = butterfly(current['outcome'],k+offset,25)
            estimates.append({'center':k+offset,'predicted_points':str(pred),'realized_points':str(realized),
                              'error_points':str(pred-realized)})
        walk.append({'session':current['session'],'training_days':sorted({r['session'] for r in earlier}),
                     'payoff_errors':estimates})
    result.update(classification='RETROSPECTIVE_RECONSTRUCTION', generated_at=bundle['generated_at'],
                  source=source, spot=bundle['spot'], independent_days=len({r['session'] for r in rows}),
                  training_sessions=sorted({r['session'] for r in rows}),
                  dataset_manifest_hash=prepared['check']['manifest_hash'], eligible_live_days=prepared['check']['independent_days'],
                  walk_forward_reconstruction=walk, selected=None, state='DIAGNOSTIC_ONLY',
                  training_rows=rows, evidence=bundle.get('evidence', {}))
    return result


def render_practical_report(result):
    def fmt(v): return '未知' if v is None else f'{number(v):.4f}'
    lines = ['# 真实报价截面的事后价值诊断', '', '**DIAGNOSTIC_ONLY：无意图、无成交、非当时可用估值。**', '',
             f"报价：{result['as_of']}，同一事件序号 {result['snapshot_seq']}；重建完成：{result['generated_at']}。",
             f"训练：{result['independent_days']} 个此前独立日，{result['training_sessions'][0]} 至 {result['training_sessions'][-1]}；严格时点合格日 {result['eligible_live_days']}。",
             f"作者中位数 {result['source']['median']}，发布 {result['source']['published_at']}，系统取得 {result['source']['available_at']}；报价时现价 {result['spot']}。",
             '原始点数残差、每天等权、scale=1；图题目标日冲突及历史修订未知。约09:50的预测没有更新为报价时的条件分布，下面只能看敏感性。', '',
             '| 中心 | 预期兑付点估值 | natural含费成本 | 点EV | 删一日兑付范围 | 盈亏平衡 | 最大损失美元 |',
             '|---|---:|---:|---:|---|---|---:|']
    for row in result['rows']:
        sensitivity=' … '.join(fmt(x) for x in row['leave_one_day_payoff_range']) if row['leave_one_day_payoff_range'] else '未知'
        breakevens=' … '.join(fmt(x) for x in row['breakevens']) if row['breakevens'] else '未知'
        lines.append(f"| {row['center']} | {fmt(row['payoff_point'])} | {fmt(row['cost_points'])} | {fmt(row['point_ev'])} | {sensitivity} | {breakevens} | {fmt(row['max_loss_usd'])} |")
    lines += ['', '删一日范围不是置信区间；点EV未计固定数据费、排队及实际组合执行差异。盈亏平衡和最大损失仅适用于完整等翼结构与表列费用。', '',
              '| 中心 | natural净借记 | 三腿中间价诊断 | 直接费用美元 |', '|---|---:|---:|---:|']
    for r in result['rows']:
        q=r['quote'] or {}
        lines.append(f"| {r['center']} | {fmt(q.get('debit_points'))} | {fmt(q.get('midpoint_diagnostic_points'))} | {fmt(q.get('fees_usd'))} |")
    lines += ['',
              '| 中心 | 锚点−5/原值/+5的预期兑付 | 限制/排除原因 |','|---|---|---|']
    for r in result['rows']:
        shifts='/'.join(fmt(r['anchor_shift_payoff_points'][str(x)]) for x in (-5,0,5))
        lines.append(f"| {r['center']} | {shifts} | {', '.join(r['reasons'])} |")
    lines += ['', '正式DV1未就绪；没有把点估值包装成价值下界。自然腿价不是组合成交承诺；中间价不计成交。',
              '当前现价的同刻历史残差缺失，未生成现价锚点模型。现价中心只作为同一个作者分布下的候选位置。',
              '逐日向前重建保存在JSON：训练日期均早于评分日，但来源实际于事后取得，因此不是盲样本外结果，也不是有成本的收益回测。','']
    return '\n'.join(lines)
