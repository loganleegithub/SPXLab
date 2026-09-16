"""Single-intent causal policies; no wall clock, I/O, or broker objects."""
from copy import deepcopy

from .contracts import digest, number, stamp


def select_candidate(strategy, valuation, forecast, mode):
    if mode == 'OBSERVE_ONLY_V1':
        return None, 'UNKNOWN', ['OBSERVATION_ONLY']
    if mode == 'FIELD_PAPER_V1':
        selected = next((r for r in valuation['rows'] if r['candidate_id'] == valuation['selected']),None)
        timing = valuation.get('timing',{})
        if strategy['id'] == 'LOOKAHEAD' and selected and timing.get('action') == 'WAIT':
            return None,'WAIT',['EXPECTED_WAIT_VALUE_HIGHER']
        return selected,valuation['state'],([] if selected else sorted({
            reason for r in valuation['rows'] for reason in r['reasons']}))
    if strategy['gate'] == 'G0':
        from .forecast import eligibility
        source_issues = eligibility(forecast, valuation['as_of'], valuation['target_at'])
        if source_issues:
            return None, 'UNKNOWN', source_issues
        gamma = forecast.get('gamma', 'UNKNOWN')
        if gamma != 'LONG':
            return None, 'ABSTAIN' if gamma == 'SHORT' else 'UNKNOWN', ['G0_' + gamma]
    if strategy['selector'] != 'SPOT' and not valuation['coverage']['complete']:
        return None, 'UNKNOWN', ['UNIVERSE_INCOMPLETE']
    if strategy['selector'] == 'DV1':
        selected = next((r for r in valuation['rows'] if r['candidate_id'] == valuation['selected']), None)
        reasons = sorted({reason for r in valuation['rows'] for reason in r['reasons']})
        return selected, valuation['state'], reasons if selected is None else []
    rows = [r for r in valuation['rows'] if r['cost_points'] is not None and r['quote'] is not None and not r['quote']['reasons'] and 'INVALID_COMBO_DEBIT' not in r['reasons']]
    if strategy['selector'] in {'SPOT', 'FORECAST'}:
        rows = [r for r in rows if strategy['selector'] in r.get('roles', [])]
    if not rows:
        return None, 'UNKNOWN', ['REFERENCE_CENTER_MISSING']
    selected = min(rows, key=lambda r: (number(r['cost_points']), number(r['center']), r['candidate_id']))
    if 'COST_CAP' in selected['reasons'] or 'DOMINATED_COST' in selected['reasons']:
        return None, 'ABSTAIN', ['COST_CAP']
    return selected, 'CANDIDATE', []


def decide(strategy, state, valuation, forecast, plan, clock, scheduled_at):
    """Return proposals. State changes only when the journal commits events."""
    if state['status'] != 'OBSERVING':
        if plan['mode'] == 'FIELD_PAPER_V1':
            return [('DECISION',{'strategy_id':strategy['id'],'scheduled_at':scheduled_at,'processed_at':clock.utc,
                'snapshot_seq':valuation['snapshot_seq'],'valuation_hash':digest(valuation),
                'classification':'MONITOR_ONLY','state':state['status'],'reasons':['DAILY_INTENT_ALREADY_USED_OR_WINDOW_CLOSED'],
                'terminal':False,'selected':None,'timing':deepcopy(valuation.get('timing')),
                'selection_scope':valuation.get('selection_scope'),'model_state':valuation.get('model_diagnostic',{}).get('state')})]
        return []
    schedule = plan['schedule']
    t = stamp(scheduled_at)
    if not stamp(schedule['fixed_utc']) <= t < stamp(schedule['entry_end_utc']):
        return []
    if strategy['timing'] == 'FIXED' and t != stamp(schedule['fixed_utc']):
        return []
    delay = (stamp(clock.utc)-t).total_seconds()*1000
    if delay < 0 or delay > float(plan['deadline_tolerance_ms']):
        selected, classification, reasons = None, 'UNKNOWN', ['MISSED_DECISION_DEADLINE']
    elif valuation.get('timing_issues'):
        selected, classification, reasons = None, 'UNKNOWN', valuation['timing_issues']
    else:
        selected, classification, reasons = select_candidate(strategy, valuation, forecast, plan['mode'])
    terminal = strategy['timing'] == 'FIXED' and selected is None
    display = classification if terminal or selected else 'WAIT'
    decision = {'strategy_id': strategy['id'], 'scheduled_at': scheduled_at, 'processed_at': clock.utc,
                'snapshot_seq': valuation['snapshot_seq'], 'valuation_hash': digest(valuation),
                'classification': classification, 'state': display, 'reasons': reasons,
                'terminal': terminal, 'selected': selected['candidate_id'] if selected else None}
    if plan['mode'] == 'FIELD_PAPER_V1':
        decision.update(timing=deepcopy(valuation.get('timing')),selection_scope=valuation.get('selection_scope'),
                        model_state=valuation.get('model_diagnostic',{}).get('state'))
    events = [('DECISION', decision)]
    if selected is not None:
        q = selected['quote']
        execution = plan['execution']
        remaining_ns = int((stamp(schedule['entry_end_utc'])-stamp(clock.utc)).total_seconds()*1e9)
        if remaining_ns <= int(number(execution['latency_seconds'])*10**9):
            events[0][1].update(classification='ABSTAIN', state='ABSTAIN', reasons=['NO_EXECUTION_TIME'], terminal=True)
            return events
        identity = {'run_id': plan['run_id'], 'session': plan['session'], 'strategy_id': strategy['id'], 'intent_index': 1}
        intent = {**identity, 'intent_id': digest(identity), 'candidate_id': selected['candidate_id'],
                  'center': selected['center'], 'width': selected['width'], 'con_ids': q.get('con_ids', []),
                  'quantity': 1, 'limit_points': q['debit_points'], 'budget_points': execution['budget_points'],
                  'fee_version': q['fee_version'], 'fee_reserve_usd': q['fees_usd'],
                  'created_seq': clock.seq, 'created_mono_ns': clock.mono_ns, 'created_at': clock.utc,
                  'eligible_mono_ns': clock.mono_ns+int(number(execution['latency_seconds'])*10**9),
                  'expires_mono_ns': clock.mono_ns+min(int(number(execution['ttl_seconds'])*10**9), remaining_ns),
                  'clock_epoch': clock.epoch, 'generation': q['generation'],
                  'forecast_hash': forecast.get('content_hash') if forecast else None,
                  'distribution_hash': valuation['distribution_hash'],
                  'valuation_hash': digest(valuation), 'execution_version': execution['version'],
                  'broker_limit_validation': 'NOT_TESTED'}
        events.append(('INTENT', intent))
    return events


def new_book(strategy):
    return {'strategy_id': strategy['id'], 'status': 'OBSERVING', 'intent': None, 'fill': None,
            'had_unknown': False, 'observation_gap': False, 'decisions': 0, 'intent_count': 0}


def apply_book_event(book, kind, payload):
    if kind == 'DECISION':
        book['decisions'] += 1
        book['had_unknown'] |= payload['classification'] == 'UNKNOWN'
        if payload['terminal']:
            book['status'] = 'CLOSED_UNKNOWN' if book['had_unknown'] else 'CLOSED_ABSTAIN'
        book['last_decision'] = deepcopy(payload)
    elif kind == 'INTENT':
        from .contracts import require
        require(book['intent_count'] == 0 and book['status'] == 'OBSERVING', 'Duplicate or invalid intent transition')
        book.update(status='INTENT_PERSISTED', intent=deepcopy(payload), intent_count=1)
    elif kind == 'EXECUTION':
        from .contracts import require
        require(book['status'] == 'INTENT_PERSISTED' and book['intent']['intent_id'] == payload['intent_id'],
                'Execution without matching committed intent')
        book['status'] = payload['status']
        book['last_execution'] = deepcopy(payload)
        if payload['status'] == 'ASSUMED_FILLED':
            book['fill'] = deepcopy(payload['quote'])
    elif kind == 'SOURCE_IGNORED_FOR_INTENT':
        book['ignored_source_revisions'] = book.get('ignored_source_revisions', 0)+1
        book['last_ignored_source'] = deepcopy(payload)
    elif kind == 'BOOK_CLOSED':
        book.update(status=payload['status'], reason=payload['reason'])
