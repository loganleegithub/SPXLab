"""Compact monitoring entry point for the frozen September 16 observation."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path

from spxlab.contracts import stamp


def check(directory):
    directory=Path(directory);plan=json.loads((directory/'plan.json').read_text())
    health=json.loads((directory/'health.json').read_text()) if (directory/'health.json').exists() else {}
    now=datetime.now(timezone.utc);schedule=plan['schedule']
    age=(now-stamp(health['updated_at'])).total_seconds() if health else None
    pid=health.get('pid');alive=False
    if pid:
        try:os.kill(pid,0);alive=True
        except ProcessLookupError:pass
    before=now<stamp(schedule['open_utc']);after=now>=stamp(schedule['capture_end_utc'])
    phase='PREOPEN' if before else 'POST_CAPTURE' if after else 'CAPTURE'
    issue=None
    if not after and (not alive or age is None or age>(70 if before else 15)):
        issue='OBSERVER_MISSING_OR_STALE'
    elif not before and not after and not health.get('connected'):
        issue='MARKET_DISCONNECTED'
    elif not before and not after and health.get('spot_issue'):
        issue='SPOT_NOT_QUALIFIED'
    result = {'checked_at':now.isoformat(),'run_id':plan['run_id'],'phase':phase,'health_age_seconds':age,
            'process_alive':alive,'issue':issue,'mode':plan['mode'],'books':health.get('books'),
            'current_spx':health.get('current_spx'),'spot_observed_at':health.get('spot_observed_at'),'spot_issue':health.get('spot_issue'),
            'coverage':health.get('coverage'),'source_issues':health.get('source_issues'),
            'distribution_status':health.get('distribution_status'),'metrics':health.get('metrics'),
            'sealed':(directory/'capture-seal.json').exists(),
            'settled':(directory/'latest-settlement.json').exists(),
            'broker_orders_permitted':False}
    if plan['mode'] == 'FIELD_PAPER_V1':
        field=json.loads((directory/'field-status.json').read_text()) if (directory/'field-status.json').exists() else {}
        result['field']={k:field.get(k) for k in ('completed_bars','gaps','predictions','short_hits','short_misses','short_unknown','wait_observed','wait_scores')}
        result['model']={k:field.get('model',{}).get(k) for k in ('state','status','anchor_mode','n_increments','q','kappa_raw','kappa_shrunk')}
        result['timing']={k:field.get('timing',{}).get(k) for k in ('status','H_points','C_points','action','reasons')}
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True);a=p.parse_args()
    print(json.dumps(check(a.directory),ensure_ascii=False,indent=2))
