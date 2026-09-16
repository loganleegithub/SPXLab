"""Launch exactly the frozen observation implementation and bounded wake lease.

Safe for heartbeat recovery: advisory locking in the observer prevents duplicate
writers; fresh runtime checks reject modifications to the frozen manifest.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from spxlab.contracts import stamp,require
from spxlab.events import atomic_json,utc_now
from spxlab.frozen import verify_snapshot


def start(directory):
    directory=Path(directory).resolve();plan=verify_snapshot(directory)
    require(plan['mode'] in {'OBSERVE_ONLY_V1','FIELD_PAPER_V1'},'Unsupported frozen launcher mode')
    require(datetime.now(timezone.utc)<stamp(plan['schedule']['capture_end_utc']),'Capture ended; do not restart')
    import fcntl
    with (directory/'observer.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'ALREADY_RUNNING','directory':str(directory)}
    with (directory/'observer.log').open('ab') as log:
        code="import sys; sys.path.insert(0, sys.argv.pop(1)); from spxlab.cli import main; main()"
        proc=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(directory/'implementation/src'),
            'field-shadow' if plan['mode']=='FIELD_PAPER_V1' else 'observe','--plan',str(directory/'plan.json'),'--directory',str(directory)],
            cwd=directory,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    # Keep this machine awake only while this particular capture process exists.
    awake=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(proc.pid)],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    result={'status':'STARTED','pid':proc.pid,'caffeinate_pid':awake.pid,'started_at':utc_now(),
            'run_id':plan['run_id'],'implementation':str(directory/'implementation'),'no_broker_orders':True}
    atomic_json(directory/'process.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True);a=p.parse_args()
    print(json.dumps(start(a.directory),indent=2))
