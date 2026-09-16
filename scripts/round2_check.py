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
    return {'checked_at':now.isoformat(),'run_id':plan['run_id'],'phase':phase,'health_age_seconds':age,
            'process_alive':alive,'issue':issue,'mode':plan['mode'],'books':health.get('books'),
            'current_spx':health.get('current_spx'),'spot_observed_at':health.get('spot_observed_at'),'spot_issue':health.get('spot_issue'),
            'coverage':health.get('coverage'),'source_issues':health.get('source_issues'),
            'distribution_status':health.get('distribution_status'),'metrics':health.get('metrics'),
            'sealed':(directory/'capture-seal.json').exists(),
            'settled':(directory/'latest-settlement.json').exists(),
            'broker_orders_permitted':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True);a=p.parse_args()
    print(json.dumps(check(a.directory),ensure_ascii=False,indent=2))
