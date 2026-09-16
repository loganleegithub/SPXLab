"""Small manual FIELD intake: original text plus an explicitly supplied Pin."""
import hashlib
import json
from pathlib import Path
import uuid

from .calendar import NY, decision_times
from .contracts import require, stamp
from .events import atomic_json, utc_now
from .forecast import normalize_forecast


def submit_pin(directory, text_file, pin=None, *, statistic_label='unspecified_point', retract=False, session=None):
    directory=Path(directory).resolve()
    plan=json.loads((directory/'plan.json').read_text())
    require(plan['mode']=='FIELD_PAPER_V1','Manual Pin helper is FIELD-only')
    now=utc_now()
    require(stamp(now).astimezone(NY).date().isoformat()==plan['session'], 'Current New York day differs from run session')
    require(session is None or session==plan['session'], 'Submitted target session differs from run')
    require(stamp(now)<stamp(plan['schedule']['close_utc']), 'Pin target has already passed')
    require(not (directory/'capture-seal.json').exists(), 'Cannot submit to a sealed run')
    require((retract and pin is None) or (not retract and pin is not None), 'Supply Pin or retract, never both')
    raw=Path(text_file).read_bytes()
    raw.decode('utf-8')
    require(raw.strip(),'Original source text required')
    sha=hashlib.sha256(raw).hexdigest()
    inbox=directory/'source-inbox'
    previous=[]
    for path in inbox.glob('*.json'):
        r=normalize_forecast(json.loads(path.read_text()),allow_field_pin=True)
        if r['source_product_id']==plan['source_product_id'] and r['target_session']==plan['session']:
            previous.append(r)
    old=max(previous,key=lambda r:(stamp(r['available_at']),r['forecast_id'])) if previous else None
    require(not retract or old is not None,'No submitted Pin to retract')
    asset=directory/'raw-sources'/sha
    record={'schema_version':2,'forecast_id':str(uuid.uuid4()),'source_product_id':plan['source_product_id'],
        'model_version':'HUMAN_EXPERT_PIN_V1','source_role':'HUMAN_EXPERT_PIN',
        'target_session':plan['session'],'target_at':plan['schedule']['close_utc'],'series':'SPXW_PM',
        'statistic_type':statistic_label,'anchor':None if retract else str(pin),
        'first_seen_at':now,'validated_at':now,'status':'RETRACTED' if retract else 'VALIDATED',
        'reviewed_by':'HUMAN_EXPLICIT_PIN_VIA_MANUAL_HELPER','gamma':'UNKNOWN',
        'supersedes':old['forecast_id'] if old else None,
        'raw_assets':[{'sha256':sha,'received_at':now,'source_url':'manual://human-pin/'+sha,'path':str(asset)}],
        'interpretation':'Explicit human anchor only; original text is evidence, not fitted probability parameters'}
    record=normalize_forecast(record,allow_field_pin=True)
    asset.parent.mkdir(parents=True,exist_ok=True)
    if not asset.exists(): asset.write_bytes(raw)
    record=normalize_forecast(record,allow_field_pin=True,verify_assets=True)
    atomic_json(inbox/(now.replace(':','-')+'-'+record['forecast_id']+'.json'),record)
    grid=decision_times({**plan['schedule'],'entry_end_utc':plan['schedule']['close_utc']})
    decision=json.loads((directory/'decision.json').read_text()) if (directory/'decision.json').exists() else {}
    return {'status':'QUEUED_NOT_YET_APPLIED','target_session':plan['session'],'pin':record['anchor'],
        'requested_mode':'MARKET_ONLY' if retract else 'PIN','statistic_label':statistic_label,
        'received_at':now,'source_version':record['forecast_id'],'source_sha256':sha,
        'next_evaluation_at':next((t for t in grid if stamp(t)>stamp(now)),None),
        'daily_intents':{k:b['intent_count'] for k,b in decision.get('books',{}).items()},
        'note':'Observer intake confirms application; revisions never reset daily intent counts'}
