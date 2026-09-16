"""Bounded butterfly expectations from common samples or complete mass partitions."""
from copy import deepcopy
from decimal import Decimal

from .contracts import digest, fields, number, require, stamp, versioned

ZERO, ONE = Decimal(0), Decimal(1)


def butterfly(s, center, width):
    s, center, width = number(s), number(center), number(width)
    require(width > 0, 'Positive wing width required')
    return max(width - abs(s-center), ZERO)


def validate_distribution(record):
    versioned(record)
    fields(record, ('model_version', 'measure', 'representation', 'target_at', 'information_cutoff',
                    'conditioning_as_of', 'computed_at', 'available_at', 'training_cutoff',
                    'calibration_status', 'provenance'))
    require(record['measure'] in {'P_ESTIMATE', 'Q_IMPLIED', 'ASSUMED'}, 'Unknown probability measure')
    times = [stamp(record[x]) for x in ('information_cutoff', 'conditioning_as_of', 'computed_at', 'available_at')]
    require(times == sorted(times) and times[-1] < stamp(record['target_at']), 'Invalid distribution time order')
    require(stamp(record['training_cutoff']) <= times[0], 'Training data follows information cutoff')
    require(record['calibration_status'] in {'NOT_READY', 'EXPLORATORY', 'FROZEN_VALIDATED', 'SYNTHETIC'},
            'Unknown calibration status')
    kind = record['representation']
    if kind == 'WEIGHTED_SAMPLES':
        require(record.get('samples'), 'Nonempty samples required')
        for row in record['samples']:
            number(row['value'])
            require(number(row['weight']) >= 0, 'Negative weight')
        require(sum(number(row['weight']) for row in record['samples']) == ONE,
                'Weights must sum exactly to one; no silent renormalization')
    elif kind == 'PARTITION_MASS_BOUNDS':
        bins = record.get('bins', [])
        require(bins and bins[0]['lo'] is None and bins[-1]['hi'] is None, 'Both unbounded tails required')
        prior = None
        for i, cell in enumerate(bins):
            lo, hi = cell['lo'], cell['hi']
            require(lo == prior, 'Bins must be adjacent; overlaps/gaps unsupported')
            require((lo is not None or i == 0) and (hi is not None or i == len(bins)-1), 'Only endpoint tails may be unbounded')
            if lo is not None and hi is not None:
                require(number(lo) < number(hi), 'Empty/reversed interval')
            require(number(cell['mass']) >= 0, 'Negative mass')
            prior = hi
        require(sum(number(b['mass']) for b in bins) == ONE, 'Partition masses must sum exactly to one')
        require(record.get('boundary_convention') == 'LEFT_CLOSED_RIGHT_OPEN_CLOSURE_BOUNDS', 'Boundary convention required')
    elif kind == 'SCENARIO_SET':
        require(record['measure'] == 'ASSUMED' and record.get('scenarios'), 'Scenario sets are explicitly assumed')
        for child in record['scenarios']:
            validate_distribution(child)
            require(child['representation'] != 'SCENARIO_SET', 'Nested scenarios unsupported')
            require(stamp(child['conditioning_as_of']) == stamp(record['conditioning_as_of']), 'Scenario conditioning mismatch')
            require(stamp(child['target_at']) == stamp(record['target_at']), 'Scenario target mismatch')
            require(stamp(child['available_at']) <= stamp(record['available_at']), 'Scenario not yet available')
    else:
        require(False, 'Unsupported representation; expected-payoff-only is reserved for a later estimator')
    return deepcopy(record)


def expected_payoff(record, center, width):
    validate_distribution(record)
    center, width = number(center), number(width)
    require(width > 0, 'Positive width required')
    kind = record['representation']
    if kind == 'WEIGHTED_SAMPLES':
        value = weighted_payoff(record['samples'], center, width)
        return {'point': value, 'lower': value, 'upper': value, 'kind': 'POINT_ONLY'}
    if kind == 'SCENARIO_SET':
        values = [expected_payoff(s, center, width) for s in record['scenarios']]
        return {'point': None, 'lower': min(v['lower'] for v in values),
                'upper': max(v['upper'] for v in values), 'kind': 'MODEL_ENVELOPE'}
    lower, upper = ZERO, ZERO
    for cell in record['bins']:
        lo = None if cell['lo'] is None else number(cell['lo'])
        hi = None if cell['hi'] is None else number(cell['hi'])
        endpoints = [ZERO if v is None else butterfly(v, center, width) for v in (lo, hi)]
        nearest = center
        if lo is not None:
            nearest = max(nearest, lo)
        if hi is not None:
            nearest = min(nearest, hi)
        lower += number(cell['mass'])*min(endpoints)
        upper += number(cell['mass'])*butterfly(nearest, center, width)
    require(ZERO <= lower <= upper <= width, 'Payoff bounds violate support')
    return {'point': None, 'lower': lower, 'upper': upper, 'kind': 'IDENTIFICATION_BOUND'}


def weighted_payoff(samples, center, width):
    """Arithmetic shared by live snapshots and explicitly retrospective diagnostics."""
    require(samples and sum(number(s['weight']) for s in samples) == ONE, 'Weights must sum exactly to one')
    require(all(number(s['weight']) >= ZERO for s in samples), 'Negative weight')
    return sum(number(s['weight'])*butterfly(s['value'], center, width) for s in samples)


def distribution_issues(record, as_of, target_at, spec, mode):
    if record is None:
        return ['DISTRIBUTION_MISSING']
    validate_distribution(record)
    issues = []
    if stamp(record['available_at']) > stamp(as_of):
        issues.append('MODEL_NOT_YET_AVAILABLE')
    if stamp(record['target_at']) != stamp(target_at):
        issues.append('MODEL_TARGET_MISMATCH')
    age = Decimal(str((stamp(as_of)-stamp(record['conditioning_as_of'])).total_seconds()))
    if age < 0 or age > number(spec['max_conditioning_age_seconds']):
        issues.append('STALE_INFORMATION_VALUATION')
    if mode != 'SYNTHETIC_REPLAY_V1':
        if record['measure'] != 'P_ESTIMATE':
            issues.append('NOT_REAL_WORLD_ESTIMATE')
        if record['calibration_status'] != 'FROZEN_VALIDATED':
            issues.append('MODEL_NOT_READY')
    if mode == 'VALUE_RESEARCH_SHADOW_V1' and record['provenance'].get('model_artifact_hash') not in spec.get('accepted_model_hashes', []):
        issues.append('UNREGISTERED_MODEL')
    return issues
