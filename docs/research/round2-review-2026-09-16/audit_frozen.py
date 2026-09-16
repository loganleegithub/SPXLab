"""Read-only audit of frozen SPXLab runs; writes only the requested new report.

No broker imports, no run-directory writes, no call to runtime.replay (which
writes replay.json). Run each snapshot in a fresh interpreter and disable pyc.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(directory):
    directory = Path(directory).resolve()
    frozen = directory / 'implementation'
    cfg = json.loads((directory / 'plan.json').read_text())
    for relative, expected in cfg['source_manifest'].items():
        assert sha(frozen / relative) == expected, relative
    sys.path.insert(0, str(frozen / 'src'))
    import spxlab.engine as engine_module
    from spxlab.events import read_events
    from spxlab.market import MarketState
    import spxlab.runtime as runtime
    assert Path(engine_module.__file__).resolve().is_relative_to(frozen)
    assert Path(runtime.__file__).resolve().is_relative_to(frozen)
    protected = [p for p in directory.iterdir() if p.is_file() and
                 (p.suffix == '.json' or p.name == 'events.sqlite')]
    before = {p.name: sha(p) for p in protected}
    main = engine_module.Engine(cfg)
    control = runtime.control_engine(cfg) if hasattr(runtime, 'control_engine') else None
    state, logged, control_logged = MarketState(), [], []
    count = 0
    for e in read_events(directory / 'events.sqlite'):
        count += 1
        if e['event_type'] == 'PLAN':
            assert e['payload']['config'] == cfg
            assert e['payload']['sha256'] == runtime.digest(cfg)
        if e['event_type'] == 'DERIVED':
            logged.append(e['payload'])
        elif e['event_type'] == 'DERIVED_CONTROL':
            control_logged.append(e['payload'])
        else:
            state.apply(e)
            main.on_event(e, state)
            if control:
                control.on_event(e, state)
    checks = {'result_match': main.result() == json.loads((directory/'decision.json').read_text()),
              'trace_match': main.trace == logged}
    status = Counter(b['status'] for b in main.result()['books'].values())
    if control:
        checks.update(control_result_match=control.result() == json.loads((directory/'control-decision.json').read_text()),
                      control_trace_match=control.trace == control_logged)
        status.update(b['status'] for b in control.result()['books'].values())
    checks['protected_files_unchanged'] = before == {p.name: sha(p) for p in protected}
    assert all(checks.values()), checks
    return {'directory': str(directory), 'events_verified': count, 'last_hash': e['hash'],
            'loaded_engine': engine_module.__file__, 'checks': checks,
            'book_status_counts': dict(status), 'protected_sha256': before,
            'scope': 'Full event hash chain and frozen decision/trace replay; no settlement reapplication'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--worker')
    p.add_argument('--root', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.worker:
        print(json.dumps(worker(a.worker)))
        return
    rows = []
    for suffix in ('002', '003', '004'):
        directory = a.root / 'var/2026-09-15' / ('diagnostic-' + suffix)
        result = subprocess.run([sys.executable, '-B', __file__, '--worker', str(directory)],
                                capture_output=True, text=True, check=True,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        row = json.loads(result.stdout)
        rows.append(row)
        print(f"{suffix}: {row['events_verified']} events; all checks passed", flush=True)
    a.output.write_text(json.dumps({'verified_at': datetime.now(timezone.utc).isoformat(),
                                    'runs': rows}, indent=2) + '\n')


if __name__ == '__main__':
    main()
