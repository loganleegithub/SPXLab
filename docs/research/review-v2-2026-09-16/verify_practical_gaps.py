"""Bounded offline probes of current code; no network, broker, or production writes.
Run from repository root with .venv/bin/python and an unused output path.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spxlab.contracts import ContractError, stamp
from spxlab.observer import Observer
from spxlab.quotes import candidate_universe
from spxlab.research import ResearchEngine
from spxlab.valuation import value_candidates

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise SystemExit('Select an unused output file')
    # A real-mode metadata fixture tests the clock adapter, not model quality.
    bundle = json.loads((ROOT/'examples/round2/value-optimum.json').read_text())
    record = deepcopy(bundle['distribution']['scenarios'][0])
    plan = json.loads((ROOT/'examples/round2/observe-2026-09-16.json').read_text())
    engine = ResearchEngine(plan)
    received = (stamp(record['available_at']) + timedelta(milliseconds=1)).isoformat()
    event = dict(seq=1, monotonic_ns=1, recorded_at=received, event_type='DISTRIBUTION',
                 payload={'record': record, '_clock_epoch': 'OFFLINE_PROBE'})
    try:
        engine.on_event(event)
        delivery = {'accepted': True}
    except ContractError as exc:
        delivery = {'accepted': False, 'reason': str(exc)}
    delivery.update(declared_available_at=record['available_at'], event_at=received,
                    consequence='A live intake adapter must stamp durable availability; do not backdate the model')

    async def subscription_probe():
        old = candidate_universe(7600)
        old_strikes = {k for c in old for k in (c['center']-25,c['center'],c['center']+25)}
        contracts = {k: SimpleNamespace(strike=k, conId=k) for k in old_strikes}
        cancelled, added = [], []
        o = Observer.__new__(Observer)  # Deliberately skip Collector/Gateway initialization.
        o.state = object()
        o.journal = SimpleNamespace(engine=SimpleNamespace(
            source=lambda now: None,
            plan=engine.plan,
            books={'PROBE': {'status':'INTENT_PERSISTED', 'intent':{'center':7600,'width':25,'con_ids':sorted(old_strikes)}}}))
        o.active_options = contracts.copy()
        o.option_contracts = {}
        o.tickers = [SimpleNamespace(contract=c) for c in contracts.values()]
        o.ib = SimpleNamespace(cancelMktData=lambda c: cancelled.append(c.strike))
        o.emit = lambda *args, **kwargs: None
        async def qualify(c):
            c.conId = int(c.strike)
            return c
        o.qualify = qualify
        o.subscribe = lambda c: added.append(c.strike)
        with patch('spxlab.observer.spot_quote', return_value=({'value':7605}, None)):
            await o.refresh_universe()
        return {'pending_intent_center':7600,'new_spot':7605,
                'old_required_strikes':sorted(old_strikes),'cancelled':sorted(cancelled),
                'new_active_strikes':sorted(o.active_options),
                'intent_legs_retained':old_strikes <= set(o.active_options),
                'scope':'Future reuse for shadow: current OBSERVE_ONLY never creates this pending intent'}

    # Explicitly synthetic contract metadata; show calculation vs eligibility.
    point = deepcopy(record)
    point.update(measure='P_ESTIMATE', calibration_status='EXPLORATORY')
    b = deepcopy(bundle['batch']); b['classification'] = 'MARKET_OBSERVATION'
    valuation = value_candidates(point, b, bundle['spec'], 'OBSERVE_ONLY_V1')
    point_probe = {'state':valuation['state'],'selected':valuation['selected'],
                   'rows':[{k:r[k] for k in ('candidate_id','payoff_point','cost_points','edge_lower','reasons')} for r in valuation['rows']],
                   'meaning':'Point valuation already exists even while formal strategy eligibility is rejected'}

    # Recompute arithmetic using existing historical records; never infer a fill.
    costs = json.loads((ROOT/'var/2026-09-15/entry-review-001/cost-comparison.json').read_text())
    terminal = Decimal('7585.73')
    arithmetic = []
    for r in costs['rows']:
        cost = Decimal(r['all_in_natural']); k = Decimal(r['center'])
        payoff = max(Decimal(25)-abs(terminal-k), Decimal(0))
        arithmetic.append(dict(book=r['book'],center=r['center'],cost_points=str(cost),
          payoff_points=str(payoff),payoff_minus_quoted_cost_usd=str(100*(payoff-cost)),
          break_even_low=str(k-(25-cost)),break_even_high=str(k+(25-cost)),
          loss_at_zero_payoff_usd=str(100*cost)))
    result = {'classification':'OFFLINE_REVIEW_NOT_MARKET_RUN', 'distribution_delivery_probe':delivery,
              'subscription_probe':asyncio.run(subscription_probe()),'point_output_probe':point_probe,
              'historical_arithmetic':{'rows':arithmetic,'not_fills':True,'not_simultaneous_across_books':True,
                'not_strategy_pnl':True,'settlement_value':'7585.73'},
              'production_source_changed':False}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
