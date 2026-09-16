"""Version-two research contracts. No implicit defaults for research decisions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

SCHEMA = 2
MODES = {'SYNTHETIC_REPLAY_V1', 'OBSERVE_ONLY_V1', 'VALUE_RESEARCH_SHADOW_V1'}
UNCERTAINTY = {'POINT_ONLY', 'IDENTIFICATION_BOUND', 'MODEL_ENVELOPE', 'STATISTICAL_INTERVAL'}


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def number(value, name='number'):
    require(not isinstance(value, bool) and value is not None, f'{name}: number required')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ContractError(f'{name}: invalid number') from None
    require(result.is_finite(), f'{name}: finite number required')
    return result


def stamp(value):
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ContractError('Timezone-aware ISO timestamp required') from None
    require(result.tzinfo is not None and result.utcoffset() is not None, 'Timezone required')
    return result.astimezone(timezone.utc)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fields(obj, names):
    require(isinstance(obj, dict), 'Object required')
    missing = set(names) - obj.keys()
    require(not missing, 'Missing fields: ' + ', '.join(sorted(missing)))


def versioned(obj):
    require(obj.get('schema_version') == SCHEMA, 'Expected schema_version=2; legacy contracts remain isolated')


@dataclass(frozen=True)
class Clock:
    seq: int
    mono_ns: int
    utc: str
    epoch: str

    def __post_init__(self):
        require(type(self.seq) is int and self.seq >= 0, 'Invalid sequence')
        require(type(self.mono_ns) is int and self.mono_ns >= 0, 'Invalid monotonic clock')
        stamp(self.utc)
        require(isinstance(self.epoch, str) and bool(self.epoch), 'Clock epoch required')


def validate_plan(plan):
    """Validate new plans only; never loosen the legacy diagnostic validator."""
    from .calendar import session_schedule
    versioned(plan)
    fields(plan, ('run_id', 'mode', 'session', 'calendar_version', 'strategies', 'execution',
                  'valuation', 'max_option_subscriptions', 'decision_grid_seconds',
                  'deadline_tolerance_ms', 'fees', 'fixed_costs'))
    require(plan['mode'] in MODES, 'Unsupported mode')
    require(isinstance(plan['run_id'], str) and plan['run_id'], 'run_id required')
    schedule = session_schedule(plan['session'], plan['calendar_version'])
    require(plan['strategies'], 'At least one strategy required')
    ids = []
    for strategy in plan['strategies']:
        fields(strategy, ('id', 'selector', 'timing', 'gate'))
        require(strategy['selector'] in {'DV1', 'CHEAP_U', 'SPOT', 'FORECAST'}, 'Unknown selector')
        require(strategy['timing'] in {'FIXED', 'FIRST_TRIGGER'}, 'Unknown timing')
        require(strategy['gate'] in {'NONE', 'G0'}, 'Unknown gate')
        ids.append(strategy['id'])
    require(len(set(ids)) == len(ids) and all(isinstance(x, str) and x for x in ids), 'Unique strategy IDs required')
    execution = plan['execution']
    fields(execution, ('version', 'quantity', 'width', 'budget_points', 'latency_seconds', 'ttl_seconds'))
    require(execution['version'] == 'FIXED_LIMIT_SHADOW_V1', 'Unknown execution model')
    require(execution['quantity'] == 1 and execution['width'] == 25, 'Round 2 fixes quantity=1, width=25')
    require(number(execution['budget_points']) == Decimal('6.25'), 'Round 2 budget is 6.25 points')
    require(number(execution['latency_seconds']) == 1 and number(execution['ttl_seconds']) == 10,
            'Round 2 uses 1s latency and 10s TTL')
    require(type(plan['max_option_subscriptions']) is int and 1 <= plan['max_option_subscriptions'] <= 40,
            'Subscription budget must be 1..40')
    require(plan['decision_grid_seconds'] == 30, 'Round 2 decision grid is 30 seconds')
    require(0 < number(plan['deadline_tolerance_ms']) <= 1000, 'Explicit deadline tolerance in (0,1000] ms required')
    v = plan['valuation']
    fields(v, ('min_edge_points', 'allowed_uncertainty', 'max_conditioning_age_seconds'))
    require(number(v['min_edge_points']) >= 0, 'Nonnegative edge threshold required')
    require(set(v['allowed_uncertainty']) <= UNCERTAINTY and v['allowed_uncertainty'], 'Unknown uncertainty type')
    require(number(v['max_conditioning_age_seconds']) >= 0, 'Nonnegative conditioning age required')
    require('POINT_ONLY' not in v['allowed_uncertainty'], 'DV1 needs an explicit value bound')
    require(plan['fees'].get('version'), 'Fee version required')
    require(plan['fixed_costs'].get('status') in {'KNOWN', 'UNKNOWN'}, 'Explicit fixed cost status required')
    if plan['fixed_costs']['status'] == 'KNOWN':
        require(number(plan['fixed_costs']['per_session_usd']) >= 0, 'Invalid fixed cost')
    if plan['mode'] == 'VALUE_RESEARCH_SHADOW_V1':
        registration = plan.get('preregistration', {})
        fields(registration, ('sha256', 'frozen_at', 'calibration_report_sha256', 'economic_delta_usd',
                              'precision_usd', 'evaluation_sessions', 'accepted_model_hashes'))
        for key in ('sha256', 'calibration_report_sha256'):
            require(len(registration[key]) == 64, 'Missing frozen research evidence hash')
        require(stamp(registration['frozen_at']) < stamp(schedule['open_utc']), 'Research must freeze before session')
        require(plan['session'] in registration['evaluation_sessions'], 'Unregistered session')
        require(registration['accepted_model_hashes'], 'No accepted model hashes')
        require(number(registration['economic_delta_usd']) > 0 and number(registration['precision_usd']) > 0,
                'Economic effect and precision must be declared')
    return {**deepcopy(plan), 'schedule': schedule}
