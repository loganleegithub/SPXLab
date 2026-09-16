"""All-book, idempotent settlement revisions with explicit official evidence."""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse

from .calendar import session_schedule
from .contracts import digest, file_hash, fields, number, require, stamp
from .distribution import butterfly
from .events import EventStore, atomic_json, read_events, utc_now


def validate_evidence(evidence, session, *, synthetic=False):
    fields(evidence, ('target_session', 'series', 'value', 'status', 'source_url', 'asset_path',
                      'asset_sha256', 'retrieved_at', 'reviewed_by', 'supersedes'))
    require(evidence['target_session'] == session and evidence['series'] == 'SPXW_PM', 'Settlement target mismatch')
    require(number(evidence['value']) > 0, 'Positive settlement value required')
    require(evidence['reviewed_by'], 'Reviewed source content required')
    require(file_hash(evidence['asset_path']) == evidence['asset_sha256'], 'Settlement source hash mismatch')
    if synthetic:
        require(evidence['status'] == 'SYNTHETIC_FIXTURE', 'Synthetic run cannot be presented as official settlement')
    else:
        require(evidence['status'] == 'CONFIRMED', 'Official settlement still pending')
        host = urlparse(evidence['source_url']).hostname or ''
        require(any(host == d or host.endswith('.'+d) for d in ('cboe.com', 'spglobal.com')), 'Official source domain required')
    require(stamp(evidence['retrieved_at']) >= stamp(session_schedule(session)['close_utc']),
            'Settlement evidence predates session close')


def settled_result(result, evidence):
    out = deepcopy(result)
    for book in out['books'].values():
        if book['status'] == 'ASSUMED_FILLED':
            q = book['fill']
            value = butterfly(evidence['value'], q['center'], q['width'])
            pnl = (value-number(q['debit_points']))*100-number(q['fees_usd'])
            book.update(payoff_points=str(value), net_pnl_estimated_fees_usd=str(pnl))
        elif book['status'] in {'CLOSED_ABSTAIN', 'EXPIRED_NO_FILL'}:
            book['net_pnl_estimated_fees_usd'] = '0'
        else:
            book['net_pnl_estimated_fees_usd'] = None
    out.update(settlement_evidence=deepcopy(evidence),
               settlement_status='SYNTHETIC_SETTLEMENT' if evidence['status'] == 'SYNTHETIC_FIXTURE' else 'CONFIRMED_VALUE_ESTIMATED_FEES')
    return out


def settle_all(directory, evidence):
    from .research import replay_research
    directory = Path(directory)
    plan = json.loads((directory/'plan.json').read_text())
    require(plan.get('schema_version') == 2, 'Use original settlement path for legacy runs')
    # Caller must use the matching implementation; automatic replay-frozen is the public preflight.
    from .frozen import verify_snapshot, sha
    verify_snapshot(directory)
    manifest = json.loads((directory/'run-manifest.json').read_text())
    root = Path(__file__).resolve().parents[2]
    require(all(sha(root/p) == h for p,h in manifest['source_manifest'].items()),
            'Current implementation differs; invoke the frozen settle worker/version')
    _,engine = replay_research(directory)
    result = json.loads((directory/'decision.json').read_text())
    require(result['entry_window_closed'], 'Cannot settle an unfinished entry window')
    validate_evidence(evidence, result['session'], synthetic=result['mode'] == 'SYNTHETIC_REPLAY_V1')
    revision_id = digest(evidence)
    journal = directory/'settlements-v2.sqlite'
    prior = list(read_events(journal)) if journal.exists() else []
    match = next((e for e in prior if e['payload']['revision_id'] == revision_id), None)
    if match:
        # Regenerate derived files after a previous crash; never append a duplicate revision.
        settled = match['payload']['result']
    else:
        previous = prior[-1]['payload']['revision_id'] if prior else None
        require(evidence['supersedes'] == previous, 'Explicit prior revision required')
        settled = settled_result(result, evidence)
        store = EventStore(journal)
        try:
            store.append('SETTLEMENT_REVISION', {'revision_id': revision_id, 'result': settled}, run_id=result['run_id'])
        finally:
            store.close()
    atomic_json(directory/f'settlement-{revision_id}.json', settled)
    # An idempotent call to an older revision cannot move the latest pointer backwards.
    latest_events = list(read_events(journal))
    latest = latest_events[-1]['payload']
    atomic_json(directory/'latest-settlement.json', {'revision_id': latest['revision_id'],
                'file': f"settlement-{latest['revision_id']}.json", 'recorded_at': utc_now()})
    lines = ['# 第二轮统一结算', '', '各账本为独立反事实；金额含声明的估计直接费用，固定成本另列。', '', '| 账本 | 状态 | 影子净额美元 |', '|---|---|---|']
    lines.extend(f"| {k} | {b['status']} | {b.get('net_pnl_estimated_fees_usd')} |" for k,b in latest['result']['books'].items())
    (directory/'SETTLED_REPORT.md').write_text('\n'.join(lines)+'\n')
    if engine.field:
        from .field import terminal_scores,write_page
        evaluation = terminal_scores(engine,latest['result'])
        atomic_json(directory/f'settlement-field-{latest["revision_id"]}.json',evaluation)
        lines += ['', '## FIELD 原始预测评分', '',
            f'终值预测 {len(evaluation["terminal_predictions"])} 次；80%模型区间失配 {evaluation["error_categories"]["state_distribution"]["terminal_interval_misses"]} 次。',
            f'LOOKAHEAD 减 FIRST_POSITIVE 当日影子净额差：{evaluation["paired_net_difference_usd"]} 美元。',
            '择时比较按同一天配对，允许不同入场时点；单日不证明盈利优势。',
            '状态/分布、选价、等待、执行/费用四类记录及所有原始预测的评分见 settlement-field JSON。']
        (directory/'SETTLED_REPORT.md').write_text('\n'.join(lines)+'\n')
        health=json.loads((directory/'health.json').read_text()) if (directory/'health.json').exists() else None
        write_page(directory,engine,health=health,settled=latest['result'])
    return {'revision_id': revision_id, 'idempotent': match is not None,
            'latest_revision_id': latest['revision_id'], 'result': settled}
