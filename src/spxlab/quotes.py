"""Full-universe quote diagnostics, retaining every failed leg and side."""
from copy import deepcopy

from .calendar import contract_session_matches
from .contracts import digest, number
from .engine import combo_quote, confirmed_side, nearest_center
from .market import MarketState


class ResearchMarketState(MarketState):
    """Do not let stale request IDs poison raw fields in the new projection."""
    def apply(self, event):
        p = event['payload']
        if event['event_type'] == 'UNSUBSCRIBED':
            cid = str(p['con_id'])
            self.subscriptions.pop(cid, None)
            self.quote_sides.pop(cid, None)
            self.fields.pop(cid, None)
            self.types.pop(cid, None)
            return
        if event['event_type'] == 'FIELD':
            expected = self.subscriptions.get(str(p['con_id']))
            if expected is None or p.get('req_id') != expected:
                return
        super().apply(event)

    def snapshot(self):
        return {**super().snapshot(), 'quote_sides': deepcopy(self.quote_sides),
                'subscriptions': deepcopy(self.subscriptions)}


def candidate_universe(spot, median=None, width=25):
    tags = {}
    if spot is not None:
        tags.setdefault(nearest_center(spot), []).append('SPOT')
    if median is not None:
        fc = nearest_center(median)
        for k in (fc-5, fc, fc+5):
            tags.setdefault(k, []).append('FORECAST' if k == fc else 'FORECAST_NEIGHBOR')
    return [{'candidate_id': f'C{k}_W{width}', 'center': k, 'width': width, 'roles': tags[k]}
            for k in sorted(tags)]


def quote_universe(state, universe, clock, cfg, *, synthetic=False):
    rows = []
    for candidate in universe:
        center, width = candidate['center'], candidate['width']
        reasons, leg_diagnostics, cids = [], [], []
        for strike, side, qty in zip((center-width, center, center+width), ('ask', 'bid', 'ask'), (1, 2, 1)):
            matches = [(cid, c) for cid, c in state.contracts.items() if
                       c.get('sec_type') == 'OPT' and c.get('symbol') == 'SPX' and
                       c.get('trading_class') == 'SPXW' and c.get('right') == 'C' and
                       c.get('expiry', '')[:8] == cfg['session'].replace('-', '') and
                       c.get('currency') == 'USD' and str(c.get('multiplier')) == '100' and
                       number(c['strike']) == strike]
            diagnostic = {'strike': strike, 'required_side': side, 'quantity': qty, 'issues': []}
            if len(matches) != 1:
                diagnostic['issues'].append('CONTRACT_NOT_UNIQUE_OR_PREWARMED')
            else:
                cid, contract = matches[0]
                diagnostic['con_id'] = cid
                cids.append(cid)
                if not synthetic and not contract_session_matches(contract, cfg['schedule']):
                    diagnostic['issues'].append('CONTRACT_SESSION_MISMATCH')
                sides = {}
                for s in ('bid', 'ask'):
                    q, reason = confirmed_side(state, cid, s, clock.mono_ns, 1 if s == side else 2)
                    if reason:
                        diagnostic['issues'].append(s + ':' + reason)
                    else:
                        sides[s] = q
                        if s == side and number(q['size']['value']) < qty:
                            diagnostic['issues'].append('INSUFFICIENT_SIZE')
                if len(sides) == 2 and number(sides['ask']['price']['value']) < number(sides['bid']['price']['value']):
                    diagnostic['issues'].append('INVALID_OR_CROSSED_BOOK')
                diagnostic['sides'] = deepcopy(sides)
            reasons.extend(diagnostic['issues'])
            leg_diagnostics.append(diagnostic)
        legacy_cfg = {'width': width, 'expiry': cfg['session'].replace('-', ''),
                      'quote_policy': 'SIDE_CONFIRMATION_V2', 'fees': cfg['fees']}
        q, reason = combo_quote(state, center, legacy_cfg, clock.mono_ns)
        if reason:
            reasons.append(reason)
        row = {**deepcopy(candidate), 'snapshot_seq': clock.seq, 'generation': state.generation,
               'clock_epoch': clock.epoch, 'as_of': clock.utc, 'con_ids': cids,
               'leg_diagnostics': leg_diagnostics, 'reasons': sorted(set(reasons)),
               'debit_points': q['debit_points'] if q else None,
               'fees_usd': q['fees']['estimated_usd'] if q else None,
               'fee_components_usd': q['fees']['components_usd'] if q else None,
               'fee_version': cfg['fees']['version'], 'fee_applicability': 'UNCONFIRMED_ACCOUNT_SCENARIO',
               'quote_refs': q['quote_refs'] if q else []}
        rows.append(row)
    return {'schema_version': 2, 'universe': deepcopy(universe), 'universe_id': digest(universe),
            'rows': rows, 'as_of': clock.utc, 'snapshot_seq': clock.seq,
            'target_at': cfg['schedule']['close_utc'],
            'classification': 'SYNTHETIC_FIXTURE' if synthetic else 'MARKET_OBSERVATION'}
