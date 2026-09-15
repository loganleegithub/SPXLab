"""Resume capture after a completed decision window; never instantiate an engine."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def inspect_run(directory):
    directory=Path(directory).resolve()
    cfg=json.loads((directory/'plan.json').read_text())
    protected={}
    for name in ('plan.json','decision.json','control-decision.json'):
        p=directory/name
        if name=='control-decision.json' and not p.exists():continue
        data=p.read_bytes();protected[name]=hashlib.sha256(data).hexdigest()
        if name!='plan.json' and not json.loads(data)['done']:
            raise ValueError('Only completed decision windows may resume capture')
    for relative,expected in cfg['source_manifest'].items():
        if hashlib.sha256((directory/'implementation'/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError('Frozen implementation hash mismatch')
    db=sqlite3.connect(f'file:{directory}/events.sqlite?mode=ro',uri=True)
    try:row=db.execute('SELECT seq,body FROM events ORDER BY seq DESC LIMIT 1').fetchone()
    finally:db.close()
    if not row:raise ValueError('Existing evidence required')
    return cfg,protected,row[0],json.loads(row[1])['generation']


async def resume(directory,stop_utc,client_id,recovery_directory):
    directory=Path(directory).resolve();recovery_directory=Path(recovery_directory).resolve()
    stop=datetime.fromisoformat(stop_utc)
    if stop.tzinfo is None or stop<=datetime.now(timezone.utc):
        raise ValueError('A future timezone-aware stop is required')
    lock=(directory/'resume.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cfg,protected,_,_=inspect_run(directory)
    sys.path.insert(0,str(directory/'implementation/src'))
    from spxlab.collector import Collector
    from spxlab.events import atomic_json,utc_now
    import spxlab.collector
    if Path(spxlab.collector.__file__).resolve()!=directory/'implementation/src/spxlab/collector.py':
        raise ValueError('Recovery must load the frozen collector')
    recovery_directory.mkdir(parents=True,exist_ok=True)
    stopping=False;active=None
    def request_stop(*_):
        nonlocal stopping
        stopping=True
        if active:active.stopping=True
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,request_stop)
    cycle=0
    try:
        while datetime.now(timezone.utc)<stop and not stopping:
            _,current,seq,generation=inspect_run(directory)
            if current!=protected:raise ValueError('Protected decisions changed during recovery')
            cycle+=1
            active=Collector(directory,client_id=client_id)
            active.generation=generation;active.count=seq
            active.emit('CAPTURE_RESUMED',{'classification':'MARKET_ONLY_AFTER_FROZEN_WINDOW',
                        'protected_sha256':protected,'recovery_directory':str(recovery_directory),
                        'cycle':cycle,'planned_stop_utc':stop_utc})
            active.write_health()
            atomic_json(recovery_directory/'supervisor-status.json',
                        {'updated_at':utc_now(),'cycle':cycle,'state':'CAPTURING_OR_RECONNECTING'})
            try:
                await active.run(cfg['expiry'],cfg['forecast']['median'],
                                 max(0,(stop-datetime.now(timezone.utc)).total_seconds()))
            except Exception as exc:
                logging.exception('Read-only capture cycle failed; retry after 30 seconds')
                atomic_json(recovery_directory/'supervisor-status.json',
                            {'updated_at':utc_now(),'cycle':cycle,'state':'RETRY_PENDING',
                             'error_type':type(exc).__name__,'error':str(exc)})
            if not stopping and datetime.now(timezone.utc)<stop:
                for _ in range(150):
                    if stopping or datetime.now(timezone.utc)>=stop:break
                    await asyncio.sleep(.2)
    finally:
        atomic_json(recovery_directory/'supervisor-stopped.json',
                    {'updated_at':utc_now(),'requested':stopping,'cycles':cycle})
        lock.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',required=True);p.add_argument('--stop-utc',required=True)
    p.add_argument('--client-id',type=int,required=True);p.add_argument('--recovery-directory',required=True)
    a=p.parse_args();asyncio.run(resume(a.directory,a.stop_utc,a.client_id,a.recovery_directory))
