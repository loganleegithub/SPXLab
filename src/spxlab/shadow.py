"""Fixed-limit natural-price shadow assumption; never a broker execution model."""
from copy import deepcopy

from .contracts import number


def shadow_step(book, quote, clock, *, interruption=None):
    if book['status'] != 'INTENT_PERSISTED':
        return None
    i = book['intent']
    base = {'strategy_id': book['strategy_id'], 'intent_id': i['intent_id'],
            'as_of': clock.utc, 'input_seq': clock.seq, 'model': 'FIXED_LIMIT_SHADOW_V1'}
    if interruption or clock.epoch != i['clock_epoch']:
        return {**base, 'status': 'FILL_UNKNOWN', 'reason': interruption or 'CLOCK_EPOCH_CHANGED'}
    previous = book.get('last_shadow_check_ns', i['eligible_mono_ns'])
    if clock.mono_ns >= i['eligible_mono_ns']:
        if clock.mono_ns-previous > 1_000_000_000:
            book['observation_gap'] = True
        book['last_shadow_check_ns'] = clock.mono_ns
    if clock.mono_ns >= i['expires_mono_ns']:
        gap = book['observation_gap']
        return {**base, 'status': 'FILL_UNKNOWN' if gap else 'EXPIRED_NO_FILL',
                'reason': 'OBSERVATION_GAP' if gap else 'NO_LIMIT_QUALIFIED_QUOTE'}
    if clock.mono_ns < i['eligible_mono_ns']:
        return None
    if quote is None or quote.get('reasons'):
        book['observation_gap'] = True
        return None
    if quote['generation'] != i['generation'] or quote['clock_epoch'] != i['clock_epoch']:
        return {**base, 'status': 'FILL_UNKNOWN', 'reason': 'CONNECTION_GENERATION_CHANGED'}
    if quote['candidate_id'] != i['candidate_id'] or quote.get('con_ids', []) != i['con_ids']:
        return {**base, 'status': 'FILL_UNKNOWN', 'reason': 'CONTRACT_IDENTITY_CHANGED'}
    if quote['fee_version'] != i['fee_version']:
        return {**base, 'status': 'FILL_UNKNOWN', 'reason': 'FEE_VERSION_CHANGED'}
    confirmations = []
    for leg, side in zip(quote.get('quote_refs', []), ('ask', 'bid', 'ask')):
        ref = leg.get('side_confirmation_refs', {}).get(side)
        if ref:
            confirmations.append(ref)
    if len(confirmations) != 3 or any(r['seq'] <= i['created_seq'] or r['mono'] <= i['created_mono_ns'] or r['seq'] > clock.seq or
            not 0 <= clock.mono_ns-r['mono'] <= 1_000_000_000 or r['generation'] != i['generation'] for r in confirmations):
        book['observation_gap'] = True
        return None
    debit, fees = number(quote['debit_points']), number(quote['fees_usd'])
    if debit <= 0 or fees < 0:
        book['observation_gap'] = True
        return None
    if debit <= number(i['limit_points']) and debit+fees/100 <= number(i['budget_points']):
        return {**base, 'status': 'ASSUMED_FILLED', 'reason': 'POST_INTENT_QUALIFIED_NATURAL_QUOTE',
                'quote': deepcopy(quote), 'not_a_broker_fill': True}
    return None
