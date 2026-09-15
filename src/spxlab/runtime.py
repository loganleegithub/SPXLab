"""Precommitted cutoff, append-only input stream, exact decision replay."""
from __future__ import annotations

import asyncio
import hashlib
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy

from .engine import Engine
from .events import atomic_json, canonical, read_events, utc_now
from .market import MarketState
from .report import render


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_manifest(root):
    root=Path(root)
    paths=sorted((root/'src'/'spxlab').glob('*.py'))+[root/'pyproject.toml',root/'requirements.lock']
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def validate(cfg, *, live=True):
    if cfg.get('quote_policy','RAW_FIELD_V1') not in ('RAW_FIELD_V1','SIDE_CONFIRMATION_V2'):
        raise ValueError('Unknown quote policy')
    if cfg.get('compare_raw_policy') and cfg.get('quote_policy')!='SIDE_CONFIRMATION_V2':
        raise ValueError('Raw-policy comparison requires SIDE_CONFIRMATION_V2')
    if cfg['mode']!='INTRADAY_DIAGNOSTIC':
        raise ValueError('Only the diagnostic shadow mode is implemented')
    if cfg['width']!=25 or cfg['max_all_in_points']!='6.25':
        raise ValueError('V0 requires width 25 and cap 6.25; change the versioned engine to change these')
    if cfg['window_seconds']!=15 or cfg['latency_seconds']!=1 or cfg['intent_lifetime_seconds']!=3:
        raise ValueError('Unreviewed execution parameters')
    cutoff=datetime.fromisoformat(cfg['cutoff_utc'])
    if cutoff.tzinfo is None:
        raise ValueError('Cutoff requires a timezone')
    from zoneinfo import ZoneInfo
    ny=cutoff.astimezone(ZoneInfo('America/New_York'))
    if str(ny.date())!=cfg['target_date'] or cfg['expiry']!=cfg['target_date'].replace('-',''):
        raise ValueError('Cutoff, target and expiry date mismatch')
    if not ((ny.hour,ny.minute)>=(9,30) and (ny.hour,ny.minute)<(16,0)):
        raise ValueError('Diagnostic cutoff outside regular hours; half days require a separate plan')
    f=cfg['forecast']
    if f['reviewed_by']!='Codex' or not f.get('assumptions'):
        raise ValueError('This diagnostic requires the explicitly recorded source-review assumptions')
    if cfg['allowed_forecast_statuses']!=['DIAGNOSTIC_DATE_ASSUMED']:
        raise ValueError('Formal forecast eligibility is not implemented')
    if live and (cutoff-datetime.now(timezone.utc)).total_seconds()<60:
        raise ValueError('Commit the live plan at least 60 seconds before its cutoff')
    return cutoff


def control_engine(cfg):
    if not cfg.get('compare_raw_policy'):
        return None
    control=deepcopy(cfg)
    control.update(quote_policy='RAW_FIELD_V1',compare_raw_policy=False,
                   experiment_id=cfg['experiment_id']+'-raw-control')
    return Engine(control)


class Coordinator:
    def __init__(self,cfg,directory,cutoff_mono):
        self.cfg=cfg
        self.directory=Path(directory)
        self.engine=Engine(cfg)
        self.control=control_engine(cfg)
        self.cutoff_mono=cutoff_mono
        self.cutoff_sent=False
        self.persisted=False
        self.collector=None

    def before(self,mono,utc,kind):
        # The cutoff is inserted BEFORE applying the first event at/after it.
        # It freezes inputs only; a complete packet/timer may then evaluate quotes.
        if not self.cutoff_sent and mono>=self.cutoff_mono:
            self.cutoff_sent=True
            c=self.collector
            e=c.store.append('CUTOFF',{'scheduled_mono':self.cutoff_mono,
                             'scheduled_utc':self.cfg['cutoff_utc'],
                             'insertion_delay_ns':mono-self.cutoff_mono},
                             run_id=c.run_id,generation=c.generation,mono=mono,utc=utc)
            self.observe(e,c.state)

    def observe(self,e,state):
        derived=self.engine.on_event(e,state)
        control_derived=self.control.on_event(e,state) if self.control else []
        for item in derived:
            self.collector.store.append('DERIVED',item,run_id=self.collector.run_id,
                                         generation=state.generation,
                                         mono=e['monotonic_ns'],utc=e['recorded_at'])
        for item in control_derived:
            self.collector.store.append('DERIVED_CONTROL',item,run_id=self.collector.run_id,
                                        generation=state.generation,
                                        mono=e['monotonic_ns'],utc=e['recorded_at'])
        if self.engine.done and not self.persisted:
            self.persisted=True
            save_result(self.directory,self.engine.result(),self.engine.trace)
            if self.control:
                atomic_json(self.directory/'control-decision.json',self.control.result())
                atomic_json(self.directory/'control-trace.json',self.control.trace)
                (self.directory/'CONTROL_REPORT.md').write_text(render(self.control.result()))

    async def timer(self):
        while not self.collector.stopping:
            self.collector.emit('TIMER',{})
            if self.engine.done:
                return
            await asyncio.sleep(.1)


def save_result(directory,result,trace):
    directory=Path(directory)
    atomic_json(directory/'decision.json',result)
    atomic_json(directory/'decision-trace.json',trace)
    atomic_json(directory/'decision-digest.json',{'result_sha256':digest(result),'trace_sha256':digest(trace)})
    (directory/'REPORT.md').write_text(render(result))


async def run_plan(cfg,directory,duration,client_id):
    from .collector import Collector
    cutoff=validate(cfg)
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    if (directory/'plan.json').exists() or (directory/'events.sqlite').exists():
        raise ValueError('Experiment directory already exists; never reuse a decision run')
    # Validate the current implementation against the code manifest committed in the plan.
    root=Path(__file__).resolve().parents[2]
    if cfg['source_manifest']!=source_manifest(root):
        raise ValueError('Source code changed after the plan was prepared')
    source=Path(cfg['forecast']['archive_directory'])
    for filename,key in [('source.png','image_sha256'),('supplied-text.txt','text_sha256')]:
        if hashlib.sha256((source/filename).read_bytes()).hexdigest()!=cfg['forecast'][key]:
            raise ValueError('Forecast source hash mismatch')
    atomic_json(directory/'plan.json',cfg)
    for relative in cfg['source_manifest']:
        dest=directory/'implementation'/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes((root/relative).read_bytes())
    cutoff_mono=time.monotonic_ns()+int((cutoff-datetime.now(timezone.utc)).total_seconds()*1e9)
    coord=Coordinator(cfg,directory,cutoff_mono)
    c=Collector(directory,client_id=client_id,observer=coord.observe,before_event=coord.before)
    coord.collector=c
    c.emit('PLAN',{'config':cfg,'sha256':digest(cfg),'committed_at':utc_now(),
                   'cutoff_mono':cutoff_mono})
    c.emit('MISSED_FORMAL_WINDOW',{'cutoff_utc':cfg['target_date']+'T14:05:00+00:00',
                                  'status':'NO_OBSERVATION','reason':'SERVICE_NOT_STARTED'})
    for s in (signal.SIGTERM,signal.SIGINT):
        signal.signal(s,lambda *_:setattr(c,'stopping',True))
    timer=asyncio.create_task(coord.timer())
    try:
        await c.run(cfg['expiry'],cfg['forecast']['median'],duration)
    finally:
        timer.cancel()
        try:
            await timer
        except asyncio.CancelledError:
            pass


def replay(directory):
    directory=Path(directory)
    cfg=json.loads((directory/'plan.json').read_text())
    engine=Engine(cfg);control=control_engine(cfg)
    state=MarketState(); logged=[];control_logged=[]; count=0; last_hash=None
    for e in read_events(directory/'events.sqlite'):
        count+=1;last_hash=e['hash']
        if e['event_type']=='PLAN' and (e['payload']['config']!=cfg or e['payload']['sha256']!=digest(cfg)):
            raise ValueError('Plan file does not match committed evidence')
        if e['event_type']=='DERIVED':
            logged.append(e['payload'])
            continue
        if e['event_type']=='DERIVED_CONTROL':
            control_logged.append(e['payload'])
            continue
        state.apply(e)
        engine.on_event(e,state)
        if control:
            control.on_event(e,state)
    expected=json.loads((directory/'decision.json').read_text())
    out={'verified_at':utc_now(),'events_verified':count,'last_hash':last_hash,
         'result_match':engine.result()==expected,'trace_match':engine.trace==logged,
         'result_sha256':digest(engine.result()),'trace_sha256':digest(engine.trace),
         'scope':'All available events in a consistent SQLite read snapshot; collection may continue'}
    if control:
        expected_control=json.loads((directory/'control-decision.json').read_text())
        out['control_result_match']=control.result()==expected_control
        out['control_trace_match']=control.trace==control_logged
    atomic_json(directory/'replay.json',out)
    if not all(out[k] for k in ('result_match','trace_match','control_result_match','control_trace_match') if k in out):
        raise ValueError('Replay differs from recorded decision')
    return out
