"""Mature-label manifests and a deliberately simple day-weighted residual model."""
from collections import defaultdict
from decimal import Decimal

from .contracts import digest, fields, number, require, stamp, versioned


def dataset_check(manifest):
    versioned(manifest)
    fields(manifest, ('training_cutoff', 'evaluation_sessions', 'anchor_kind', 'time_bucket',
                      'min_independent_days', 'allowed_classifications', 'rows'))
    from .calendar import session_schedule, NY
    cutoff = stamp(manifest['training_cutoff'])
    require(type(manifest['min_independent_days']) is int and manifest['min_independent_days'] > 0,
            'Explicit minimum independent days required; not a profitability threshold')
    require(manifest['anchor_kind'] in {'SPOT', 'FORECAST'}, 'Unknown anchor kind')
    allowed = set(manifest['allowed_classifications'])
    require(allowed <= {'PROSPECTIVE_CAPTURE', 'RETROSPECTIVE_SOURCE', 'SYNTHETIC_FIXTURE'}, 'Unknown data class')
    ids, usable, excluded = set(), [], []
    for row in manifest['rows']:
        fields(row, ('row_id', 'session', 'classification', 'anchor_kind', 'time_bucket',
                     'anchor', 'scale', 'outcome', 'decision_at', 'feature_available_at',
                     'target_at', 'outcome_available_at', 'source_hash'))
        require(row['row_id'] not in ids, 'Duplicate row identity')
        ids.add(row['row_id'])
        reasons = list(row.get('source_issues', []))
        if stamp(row['target_at']) != stamp(session_schedule(row['session'])['close_utc']) or stamp(row['decision_at']).astimezone(NY).date().isoformat() != row['session']:
            reasons.append('SESSION_LABEL_CONFLICT')
        if stamp(row['decision_at']).astimezone(NY).strftime('%H:%M') != row['time_bucket']:
            reasons.append('DECISION_TIME_BUCKET_CONFLICT')
        if manifest['evaluation_sessions'] and row['session'] >= min(manifest['evaluation_sessions']):
            reasons.append('POST_EVALUATION_TRAINING_DAY')
        if row['session'] in manifest['evaluation_sessions']:
            reasons.append('EVALUATION_DAY_LEAKAGE')
        if stamp(row['feature_available_at']) > stamp(row['decision_at']):
            reasons.append('FEATURE_NOT_AVAILABLE_AT_DECISION')
        if stamp(row['decision_at']) >= stamp(row['target_at']):
            reasons.append('DECISION_AFTER_TARGET')
        if stamp(row['outcome_available_at']) < stamp(row['target_at']):
            reasons.append('LABEL_BEFORE_MATURITY')
        if stamp(row['target_at']) > cutoff or stamp(row['outcome_available_at']) > cutoff:
            reasons.append('LABEL_NOT_MATURE_AND_AVAILABLE')
        if row['anchor_kind'] != manifest['anchor_kind'] or row['time_bucket'] != manifest['time_bucket']:
            reasons.append('CONDITIONING_MISMATCH')
        if row['classification'] not in allowed:
            reasons.append('DATA_CLASS_NOT_ALLOWED')
        if not row['source_hash'] or len(row['source_hash']) != 64:
            reasons.append('PROVENANCE_MISSING')
        for key in ('anchor', 'outcome', 'scale'):
            number(row[key], key)
        if number(row['scale']) <= 0:
            reasons.append('NONPOSITIVE_SCALE')
        if reasons:
            excluded.append({'row_id': row['row_id'], 'reasons': reasons})
        else:
            usable.append(row)
    days = sorted({r['session'] for r in usable})
    ready = len(days) >= manifest['min_independent_days']
    prospective = ready and all(r['classification'] == 'PROSPECTIVE_CAPTURE' for r in usable)
    return {'schema_version': 2, 'manifest_hash': digest(manifest), 'training_cutoff': manifest['training_cutoff'],
            'status': 'ESTIMATOR_INPUT_READY' if ready else 'DATA_NOT_READY',
            'formal_calibration_status': 'NOT_ESTABLISHED', 'prospective_inputs_only': prospective,
            'independent_days': len(days), 'effective_day_count': len(days),
            'maximum_day_weight': str(Decimal(1)/len(days)) if days else None,
            'sessions': days, 'eligible_row_ids': [r['row_id'] for r in usable],
            'excluded_rows': excluded, 'excluded_count': len(excluded),
            'warning': 'Estimator readiness is not calibration, execution evidence, or profitability'}


def build_residual_distribution(manifest, context):
    fields(context, ('anchor', 'scale', 'as_of', 'available_at', 'target_at', 'time_bucket', 'anchor_kind'))
    report = dataset_check(manifest)
    require(report['status'] == 'ESTIMATOR_INPUT_READY', 'DATA_NOT_READY')
    require(context['time_bucket'] == manifest['time_bucket'] and context['anchor_kind'] == manifest['anchor_kind'],
            'Current features do not match training conditioning bucket')
    require(stamp(manifest['training_cutoff']) <= stamp(context['as_of']), 'Training follows current information')
    from .calendar import NY
    require(stamp(context['as_of']).astimezone(NY).strftime('%H:%M') == context['time_bucket'], 'Current clock does not match conditioning bucket')
    anchor, scale = number(context['anchor']), number(context['scale'])
    require(scale > 0, 'Positive scale required')
    samples = residual_samples([r for r in manifest['rows'] if r['row_id'] in report['eligible_row_ids']], anchor, scale)
    synthetic = any(r['classification'] == 'SYNTHETIC_FIXTURE' for r in manifest['rows']
                    if r['row_id'] in report['eligible_row_ids'])
    record = {'schema_version': 2, 'model_version': 'DAY_BALANCED_RESIDUAL_V1',
              'measure': 'ASSUMED' if synthetic else 'P_ESTIMATE', 'representation': 'WEIGHTED_SAMPLES',
              'target_at': context['target_at'], 'information_cutoff': context['as_of'],
              'conditioning_as_of': context['as_of'], 'computed_at': context['available_at'],
              'available_at': context['available_at'], 'training_cutoff': manifest['training_cutoff'],
              'calibration_status': 'SYNTHETIC' if synthetic else 'EXPLORATORY',
              'provenance': {'dataset_hash': report['manifest_hash'], 'eligible_row_ids': report['eligible_row_ids'],
                             'independent_days': report['independent_days'], 'weighting': 'EQUAL_DAYS_EQUAL_ROWS_WITHIN_DAY'},
              'samples': samples}
    from .distribution import validate_distribution
    return validate_distribution(record)


def residual_samples(rows, anchor, scale=1):
    """Day-balanced arithmetic; the caller owns availability/eligibility checks."""
    anchor, scale = number(anchor), number(scale)
    require(rows and scale > 0, "Nonempty rows and positive scale required")
    by_day = defaultdict(list)
    for row in rows:
        by_day[row['session']].append(row)
    # Repeating decimals get a deterministic residual allocation at each level.
    # This is explicit day-balanced weighting, never tail truncation/renormalization.
    samples, allocated_days = [], Decimal(0)
    days = sorted(by_day)
    for j, day in enumerate(days):
        day_weight = Decimal(1)/len(days) if j < len(days)-1 else Decimal(1)-allocated_days
        allocated_days += day_weight
        rows = sorted(by_day[day], key=lambda r: r['row_id'])
        allocated_rows = Decimal(0)
        for i, row in enumerate(rows):
            weight = day_weight/len(rows) if i < len(rows)-1 else day_weight-allocated_rows
            allocated_rows += weight
            z = (number(row['outcome'])-number(row['anchor']))/number(row['scale'])
            samples.append({'value': str(anchor+scale*z), 'weight': str(weight), 'row_id': row['row_id']})
    return samples
