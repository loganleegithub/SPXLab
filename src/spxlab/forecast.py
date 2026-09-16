"""Point-in-time source records and append-only revision projection."""
from copy import deepcopy

from .contracts import digest, fields, file_hash, number, require, stamp, versioned


def normalize_forecast(record, *, verify_assets=False):
    versioned(record)
    fields(record, ('forecast_id', 'source_product_id', 'model_version', 'target_session', 'target_at',
                    'series', 'statistic_type', 'first_seen_at', 'validated_at', 'raw_assets',
                    'status', 'reviewed_by', 'gamma', 'supersedes'))
    require(record['forecast_id'] and record['source_product_id'] and record['model_version'], 'Source identity required')
    from .calendar import session_schedule
    require(stamp(record['target_at']) == stamp(session_schedule(record['target_session'])['close_utc']), 'Forecast session/target conflict')
    require(record['series'] == 'SPXW_PM', 'Wrong forecast series')
    require(record['status'] in {'VALIDATED', 'AMBIGUOUS', 'RETRACTED'}, 'Invalid source status')
    require(record['statistic_type'] == 'median', 'Only explicit median point forecasts supported')
    require(record['gamma'] in {'LONG', 'SHORT', 'UNKNOWN'}, 'Unknown gamma label')
    require(record['reviewed_by'], 'Reviewer identity required; no privileged reviewer name')
    if record['status'] != 'RETRACTED':
        number(record.get('median'), 'median')
    first, reviewed = stamp(record['first_seen_at']), stamp(record['validated_at'])
    require(first <= reviewed, 'Review precedes receipt')
    require(record['raw_assets'], 'Source assets required')
    receipts = [first, reviewed]
    for asset in record['raw_assets']:
        fields(asset, ('sha256', 'received_at', 'source_url'))
        require(len(asset['sha256']) == 64 and asset['source_url'], 'Asset identity required')
        receipts.append(stamp(asset['received_at']))
        if verify_assets:
            require(file_hash(asset['path']) == asset['sha256'], 'Source asset hash mismatch')
    available = max(receipts)
    if record.get('available_at'):
        require(stamp(record['available_at']) >= available, 'available_at cannot precede any required asset/review')
        available = stamp(record['available_at'])
    if record.get('published_at'):
        require(stamp(record['published_at']) <= available, 'Future publication cannot be available yet')
    require(available <= stamp(record['target_at']), 'Forecast cannot first become available after target')
    out = {**deepcopy(record), 'available_at': available.isoformat()}
    out['content_hash'] = digest({k: v for k, v in out.items() if k != 'content_hash'})
    return out


def eligibility(record, as_of, target_at, source_product_id=None):
    if record is None:
        return ['SOURCE_MISSING']
    reasons = []
    if record['status'] != 'VALIDATED':
        reasons.append('SOURCE_' + record['status'])
    if stamp(record['available_at']) > stamp(as_of):
        reasons.append('SOURCE_NOT_YET_AVAILABLE')
    if stamp(record['target_at']) != stamp(target_at):
        reasons.append('SOURCE_TARGET_MISMATCH')
    if source_product_id and record['source_product_id'] != source_product_id:
        reasons.append('SOURCE_PRODUCT_MISMATCH')
    return reasons


class ForecastBook:
    def __init__(self):
        self.records = []

    def add(self, record):
        new = normalize_forecast(record)
        previous = next((r for r in self.records if r['forecast_id'] == new['forecast_id']), None)
        if previous:
            require(previous == new, 'Forecast ID reused for different content')
            return
        if new['supersedes']:
            old = next((r for r in self.records if r['forecast_id'] == new['supersedes']), None)
            require(old is not None, 'Unknown superseded forecast')
            require(old['source_product_id'] == new['source_product_id'] and old['target_at'] == new['target_at'],
                    'Revision cannot change product or target')
            require(stamp(old['available_at']) <= stamp(new['available_at']), 'Revision available before original')
        self.records.append(new)

    def latest(self, product, target_at, as_of):
        visible = [r for r in self.records if r['source_product_id'] == product and
                   stamp(r['target_at']) == stamp(target_at) and stamp(r['available_at']) <= stamp(as_of)]
        # Retractions/ambiguous revisions remain visible; no silent old-version fallback.
        return deepcopy(max(visible, key=lambda r: (stamp(r['available_at']), self.records.index(r)))) if visible else None
