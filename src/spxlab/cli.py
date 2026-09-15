"""SPXLab research CLI. Contains no broker order commands."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from .events import atomic_json, EventStore, utc_now
from .engine import settle, D
from .report import render
from .runtime import run_plan, replay


def settlement(directory,evidence_path):
    directory=Path(directory)
    replay(directory)
    e=json.loads(Path(evidence_path).read_text())
    asset=Path(e['asset_path'])
    if hashlib.sha256(asset.read_bytes()).hexdigest()!=e['asset_sha256']:
        raise ValueError('Settlement asset hash mismatch')
    from urllib.parse import urlparse
    from datetime import datetime
    from zoneinfo import ZoneInfo
    host=urlparse(e['source_url']).hostname or ''
    if not (host=='cboe.com' or host.endswith('.cboe.com') or host=='spglobal.com' or host.endswith('.spglobal.com')):
        raise ValueError('Official Cboe or S&P source required')
    stamp=datetime.fromisoformat(e['retrieved_at'])
    if stamp.tzinfo is None or str(stamp.astimezone(ZoneInfo('America/New_York')).date())<e['target_date']:
        raise ValueError('Invalid settlement evidence timestamp')
    if str(stamp.astimezone(ZoneInfo('America/New_York')).date())==e['target_date'] and stamp.astimezone(ZoneInfo('America/New_York')).hour<16:
        raise ValueError('Today has not closed yet')
    if not D(e['value']).is_finite() or D(e['value'])<=0 or not e.get('reviewed_by'):
        raise ValueError('Invalid or unreviewed settlement value')
    result=settle(json.loads((directory/'decision.json').read_text()),e)
    name='settlement-'+hashlib.sha256(asset.read_bytes()).hexdigest()[:16]+'.json'
    if (directory/name).exists():
        raise ValueError('This settlement evidence was already applied')
    # Separate journal avoids concurrent writes to the active market log.
    store=EventStore(directory/'settlements.sqlite')
    store.append('SETTLEMENT_EVIDENCE',e,run_id=result['experiment_id'])
    store.close()
    atomic_json(directory/name,result)
    atomic_json(directory/'latest-settlement.json',{'file':name,'recorded_at':utc_now()})
    (directory/'SETTLED_REPORT.md').write_text(render(result))
    return {'report':str(directory/'SETTLED_REPORT.md'),'status':result['settlement_status']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('run');r.add_argument('--config',required=True);r.add_argument('--directory',required=True)
    r.add_argument('--duration',type=float,default=300);r.add_argument('--client-id',type=int,default=27152)
    r=sub.add_parser('replay');r.add_argument('--directory',required=True)
    r=sub.add_parser('settle');r.add_argument('--directory',required=True);r.add_argument('--evidence',required=True)
    args=p.parse_args()
    if args.command=='run':
        asyncio.run(run_plan(json.loads(Path(args.config).read_text()),args.directory,args.duration,args.client_id))
    elif args.command=='replay':
        print(json.dumps(replay(args.directory),ensure_ascii=False,indent=2))
    else:
        print(json.dumps(settlement(args.directory,args.evidence),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
