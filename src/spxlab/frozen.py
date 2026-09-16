"""Snapshot dispatch and read-only replay, including legacy run implementations.

Worker imports the verified snapshot only after process isolation. Its network,
process-spawn, and write access are blocked by a Python audit hook. The hook is
an accident barrier for trusted local snapshots, not a malicious-code sandbox.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def environment(root):
    packages = {}
    for line in (Path(root)/'requirements.lock').read_text().splitlines():
        if line and not line.startswith('#'):
            name, version = line.split('==')
            actual = importlib.metadata.version(name)
            if actual != version:
                raise ValueError(f'Locked dependency mismatch: {name} {actual} != {version}')
            packages[name] = actual
    return {'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'platform_system': platform.system(), 'machine': platform.machine(), 'packages': packages}


def freeze_run(plan, directory, root):
    from .contracts import digest, validate_plan
    from .events import atomic_json
    directory, root = Path(directory).resolve(), Path(root).resolve()
    normalized = validate_plan(plan)
    if directory.exists():
        raise ValueError('Use a new run directory')
    env = environment(root)
    paths = sorted((root/'src/spxlab').glob('*.py')) + sorted((root/'scripts').glob('*.py'))
    paths += [root/'pyproject.toml', root/'requirements.lock']
    hashes = {str(p.relative_to(root)): sha(p) for p in paths}
    directory.mkdir(parents=True)
    for p in paths:
        dest = directory/'implementation'/p.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
    atomic_json(directory/'plan.json', normalized)
    manifest = {'schema_version': 2, 'run_id': normalized['run_id'], 'plan_hash': digest(normalized),
                'source_manifest': hashes, 'environment': env,
                'event_schema': 'Legacy envelope v1; research payload contracts v2',
                'snapshot_scope': 'src, operational scripts, package and dependency specifications'}
    atomic_json(directory/'run-manifest.json', manifest)
    return normalized


def verify_snapshot(directory):
    directory = Path(directory).resolve()
    plan = json.loads((directory/'plan.json').read_text())
    if plan.get('schema_version') == 2:
        manifest = json.loads((directory/'run-manifest.json').read_text())
        expected = manifest['source_manifest']
        actual_env = environment(directory/'implementation')
        if actual_env != manifest['environment']:
            raise ValueError('Frozen runtime environment mismatch; rebuild the recorded environment')
        canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if hashlib.sha256(canonical.encode()).hexdigest() != manifest['plan_hash']:
            raise ValueError('Frozen plan hash mismatch')
    else:
        expected = plan['source_manifest']
    frozen = directory/'implementation'
    for relative, h in expected.items():
        path = (frozen/relative).resolve()
        if not path.is_relative_to(frozen) or sha(path) != h:
            raise ValueError('Frozen source missing or hash mismatch: ' + relative)
    return plan


def legacy_worker(directory, plan):
    from spxlab.engine import Engine
    from spxlab.market import MarketState
    from spxlab.events import read_events
    import spxlab.runtime as runtime
    engine = Engine(plan)
    control = runtime.control_engine(plan) if hasattr(runtime, 'control_engine') else None
    state, logged, controls, count, last = MarketState(), [], [], 0, None
    for e in read_events(directory/'events.sqlite'):
        count += 1
        last = e['hash']
        if e['event_type'] == 'PLAN':
            if e['payload']['config'] != plan or e['payload']['sha256'] != runtime.digest(plan):
                raise ValueError('Committed legacy plan mismatch')
        if e['event_type'] == 'DERIVED':
            logged.append(e['payload'])
        elif e['event_type'] == 'DERIVED_CONTROL':
            controls.append(e['payload'])
        else:
            state.apply(e)
            engine.on_event(e, state)
            if control:
                control.on_event(e, state)
    checks = {'result_match': engine.result() == json.loads((directory/'decision.json').read_text()),
              'trace_match': engine.trace == logged}
    if control:
        checks.update(control_result_match=control.result() == json.loads((directory/'control-decision.json').read_text()),
                      control_trace_match=control.trace == controls)
    if not all(checks.values()):
        raise ValueError('Legacy replay mismatch: ' + str(checks))
    return {'events_verified': count, 'last_hash': last, **checks,
            'environment_scope': 'Legacy manifest did not record a full runtime; source/decisions verified only'}


def worker(directory, settlement_evidence=None):
    directory = Path(directory).resolve()
    plan = verify_snapshot(directory)
    # Only trusted source whose bytes matched its local run manifest is imported.
    sys.path.insert(0, str(directory/'implementation/src'))
    sys.dont_write_bytecode = True
    def guard(event, args):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'}:
            raise PermissionError('Frozen replay forbids networking and child processes')
        if event == 'open':
            _, mode, flags = args
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                path = Path(args[0]).resolve() if isinstance(args[0], (str, bytes, os.PathLike)) else None
                if settlement_evidence is None or path is None or path.parent != directory or not (path.name.startswith('settlement-') or path.name.startswith('latest-settlement.') or path.name in {'SETTLED_REPORT.md','FIELD.html','FIELD.html.tmp','FIELD_REPORT.md','field-status.json','field-status.json.tmp'}):
                    raise PermissionError('Frozen worker forbids this write')
    sys.addaudithook(guard)
    if settlement_evidence is not None:
        from spxlab.settlement import settle_all
        result = settle_all(directory, settlement_evidence)
    elif plan.get('schema_version') == 2:
        from spxlab.research import replay_research
        result, _ = replay_research(directory)
    else:
        result = legacy_worker(directory, plan)
    import spxlab.engine
    if not Path(spxlab.engine.__file__).resolve().is_relative_to(directory/'implementation'):
        raise ValueError('Wrong implementation imported')
    result['loaded_engine'] = spxlab.engine.__file__
    result['read_only_worker'] = settlement_evidence is None
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def replay_frozen(directory, output):
    from .events import atomic_json, utc_now
    directory, output = Path(directory).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(directory):
        raise ValueError('Replay output must be a new directory outside the protected run')
    verify_snapshot(directory)
    result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker', str(directory)],
                            capture_output=True, text=True,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    if result.returncode:
        raise ValueError('Frozen replay failed: ' + result.stderr[-4000:])
    proof = json.loads(result.stdout)
    proof.update(verified_at=utc_now(), directory=str(directory))
    output.mkdir(parents=True)
    atomic_json(output/'replay.json', proof)
    return proof


def settle_frozen(directory, evidence):
    directory = Path(directory).resolve()
    verify_snapshot(directory)
    result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--settle-worker', str(directory)],
                            input=json.dumps(evidence), capture_output=True, text=True,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    if result.returncode:
        raise ValueError('Frozen settlement failed: ' + result.stderr[-4000:])
    return json.loads(result.stdout)


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in {'--worker', '--settle-worker'}:
        raise SystemExit('Internal read-only worker; use spxlab replay-frozen')
    worker(sys.argv[2], json.load(sys.stdin) if sys.argv[1] == '--settle-worker' else None)
