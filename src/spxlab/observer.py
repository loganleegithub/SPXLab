"""Finite read-only observation service. No order-capable protocol messages.

Decision timers precede the next inbound raw callback. Timer lateness remains
visible; no quote is backfilled into an earlier decision. Every restart has a
new monotonic epoch and ends any previous pending shadow intention.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import time
import uuid

from .calendar import decision_times
from .collector import Collector
from .contracts import digest, require, stamp, file_hash, validate_plan
from .engine import spot_quote
from .events import atomic_json, utc_now
from .forecast import normalize_forecast, eligibility, anchor_value
from .frozen import freeze_run, verify_snapshot, sha
from .quotes import candidate_universe
from .research import ResearchEngine, ResearchJournal, replay_research


class Observer(Collector):
    def __init__(self, directory, plan, *, client_id=27216, port=4001):
        directory = Path(directory)
        existing = (directory/'events.sqlite').exists()
        engine = replay_research(directory, check_result=False)[1] if existing else ResearchEngine(plan)
        super().__init__(directory, client_id=client_id, port=port)
        self.journal = ResearchJournal(directory/'events.sqlite', plan, store=self.store, engine=engine)
        self.state, self.run_id = engine.market, plan['run_id']
        self.generation = self.state.generation
        self.epoch = str(uuid.uuid4())
        self.grid = decision_times(plan['schedule'])
        if engine.field:
            # Continue forecasts and diagnostics after entry closes, until PM.
            self.grid = decision_times({**plan['schedule'],'entry_end_utc':plan['schedule']['close_utc']})
        self.grid_index = sum(stamp(t) <= stamp(engine.last_evaluation) for t in self.grid) if engine.last_evaluation else 0
        self.last_wall, self.last_mono = None, None
        self.in_timer = False
        self.seen_inbox = set()
        self.active_options = {}
        self.option_contracts = {}
        self.count = self.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.emit('RESTART' if existing else 'RESEARCH_PLAN',
                  {'plan_hash': digest(engine.plan), 'pid':os.getpid(), 'source_intake':'MANUAL_REVIEWED_ASSETS_ONLY'})
        if engine.field and not existing and plan.get('history_reference'):
            reference = plan['history_reference']
            path = Path(reference['path'])
            require(file_hash(path) == reference['sha256'], 'Historical reference changed before intake')
            record = json.loads(path.read_text())
            require(stamp(record['acquired_at']) <= stamp(utc_now()) and
                    all(day < plan['session'] for day in record['actual_sessions']), 'Historical reference contains future sessions')
            archive = self.directory/'history-reference.json'
            archive.write_bytes(path.read_bytes())
            self.emit('HISTORY_REFERENCE',{'sha256':reference['sha256'],'record':record})

    def _hooks(self):
        super()._hooks()
        arrived = self.ib.client._tcpDataArrived
        processed = self.ib.client._tcpDataProcessed
        self.in_packet = False
        def begin_packet():
            self.in_packet = False
            self.emit('PACKET_STARTED', {})
            self.in_packet = True
            if arrived:
                arrived()
        def end_packet():
            try:
                if processed:
                    processed()
            finally:
                self.in_packet = False
        self.ib.client._tcpDataArrived = begin_packet
        self.ib.client._tcpDataProcessed = end_packet

    def emit(self, kind, payload):
        mono, utc = time.monotonic_ns(), utc_now()
        if self.last_wall is not None:
            drift = (stamp(utc)-stamp(self.last_wall)).total_seconds() - (mono-self.last_mono)/1e9
            if abs(drift) > 1:
                self.epoch = str(uuid.uuid4())
                self.state.fields, self.state.quote_sides = {}, {}
        self.last_wall, self.last_mono = utc, mono
        if not self.in_timer and not self.in_packet and kind != 'RESEARCH_PLAN':
            self.in_timer = True
            try:
                self.evaluate_due(mono, utc)
            finally:
                self.in_timer = False
            if self.journal.engine.field:
                # Model production has a real duration; the next raw event
                # must not retain the timestamp sampled before inference.
                mono,utc = time.monotonic_ns(),utc_now()
                self.last_wall,self.last_mono = utc,mono
        if kind == 'DISTRIBUTION':
            from .distribution import validate_distribution
            payload = deepcopy(payload)
            record = validate_distribution(payload['record'])
            require(stamp(record['available_at']) <= stamp(utc), 'Model not yet available')
            payload['producer_available_at'] = record['available_at']
            record['available_at'] = utc
            payload['record'] = validate_distribution(record)
        event, derived = self.journal.append(kind, payload, clock_epoch=self.epoch,
                       mono=mono, utc=utc, generation=self.generation)
        self.count += 1+len(derived)
        return event

    def evaluate_due(self, mono, utc):
        while self.grid_index < len(self.grid) and stamp(self.grid[self.grid_index]) <= stamp(utc):
            scheduled = self.grid[self.grid_index]
            self.grid_index += 1
            if self.journal.engine.field:
                self.publish_field_model(mono,utc,scheduled)
                mono,utc = time.monotonic_ns(),utc_now()
            _, derived = self.journal.append('EVALUATE', {'scheduled_at':scheduled},
                         clock_epoch=self.epoch, mono=mono, utc=utc, generation=self.generation)
            self.count += 1+len(derived)

    def publish_field_model(self, mono, utc, scheduled):
        from .field_model import build_model
        engine = self.journal.engine
        # Never condition a missed historical grid using current information.
        if not 0 <= (stamp(utc)-stamp(scheduled)).total_seconds()*1000 <= float(engine.plan['deadline_tolerance_ms']):
            return
        if any(stamp(ref['utc']) > stamp(scheduled) for fields in self.state.fields.values() for ref in fields.values()):
            return
        spot,_ = spot_quote(self.state,mono)
        source = engine.source(utc)
        if eligibility(source,utc,engine.plan['schedule']['close_utc']): source = None
        record,diagnostic = build_model(engine.field.tape,spot,utc,engine.plan['schedule']['close_utc'],source,
            computed_at=utc,algorithm_hash=digest({'model_version':engine.plan['model_version'],'plan':digest(engine.plan)}),
            previous_pin=engine.field.last_pin)
        from .field import event_risk
        diagnostic['known_event_risk'] = event_risk(engine.field.known_events,utc,engine.plan['schedule']['close_utc'])
        if engine.field.history_reference:
            from .calendar import NY
            key = stamp(utc).astimezone(NY).strftime('%H:%M')
            diagnostic['historical_same_minute_reference'] = engine.field.history_reference['record']['reference_q_by_minute'].get(key)
            if record: record['diagnostic'] = diagnostic
        done = utc_now()
        if record is not None:
            record['computed_at'] = record['available_at'] = done
            self.emit('DISTRIBUTION',{'record':record,'automatic_producer':True})
        else:
            self.emit('FIELD_MODEL_STATUS',{'diagnostic':diagnostic,'computed_at':done})

    async def prepare(self, expiry, median=None):
        from ib_async import Index
        index = await self.qualify(Index('SPX', 'CBOE', 'USD'))
        require(index is not None, 'SPX contract not uniquely qualified')
        self.subscribe(index)
        self.active_options = {}
        self.option_contracts = {}
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            quote, _ = spot_quote(self.state, time.monotonic_ns())
            if quote:
                await self.refresh_universe()
                self.emit('READY', {'subscriptions': len(self.tickers), 'expiry':expiry})
                return
            await asyncio.sleep(.1)
        raise RuntimeError('No qualified live SPX spot within 20 seconds')

    async def refresh_universe(self):
        from ib_async import Option
        now = utc_now()
        spot, reason = spot_quote(self.state, time.monotonic_ns())
        source = self.journal.engine.source(now)
        if eligibility(source, now, self.journal.engine.plan['schedule']['close_utc']):
            source = None
        universe = candidate_universe(spot['value'] if spot else None, anchor_value(source) if source else None,
                                     market_neighbors=self.journal.engine.plan['mode']=='FIELD_PAPER_V1')
        # Keep the locked structure observable until its pending intent terminates.
        pending = [b['intent'] for b in self.journal.engine.books.values()
                   if b['status'] == 'INTENT_PERSISTED']
        strikes = sorted({k for c in universe + pending
                          for k in (c['center']-c['width'], c['center'], c['center']+c['width'])})
        require(len(strikes) <= self.journal.engine.plan['max_option_subscriptions'], 'Subscription budget exceeded')
        if set(strikes) == set(self.active_options):
            return
        self.emit('UNIVERSE', {'candidates':universe,'strikes':strikes,'spot_issue':reason,
                               'source_missing': source is None,'max_option_subscriptions':40})
        for k in set(self.active_options)-set(strikes):
            c = self.active_options.pop(k)
            self.ib.cancelMktData(c)
            self.emit('UNSUBSCRIBED', {'con_id':c.conId})
            self.tickers = [t for t in self.tickers if t.contract.conId != c.conId]
        for k in strikes:
            if k in self.active_options:
                continue
            c = self.option_contracts.get(k)
            if c is None:
                c = await self.qualify(Option('SPX',self.journal.engine.plan['session'].replace('-',''),k,'C','SMART',
                                             multiplier='100',currency='USD',tradingClass='SPXW'))
                if c is not None:
                    self.option_contracts[k] = c
            if c is not None:
                self.subscribe(c)
                self.active_options[k] = c

    def intake(self):
        self.intake_distributions()
        inbox = self.directory/'source-inbox'
        if not inbox.exists():
            return
        for path in sorted(inbox.glob('*.json')):
            key = file_hash(path)
            if key in self.seen_inbox:
                continue
            self.seen_inbox.add(key)
            try:
                record = normalize_forecast(json.loads(path.read_text()), verify_assets=True,
                                            allow_field_pin=self.journal.engine.field is not None)
                if self.journal.engine.field:
                    require(record['target_session'] == self.journal.engine.plan['session'], 'Pin target differs from FIELD session')
                old = next((r for r in self.journal.engine.forecasts.records if r['forecast_id']==record['forecast_id']), None)
                if old:
                    require(old.get('intake_sha256') == key, 'Existing source ID changed; submit an explicit new revision')
                    continue
                record['available_at'] = max(stamp(record['available_at']), stamp(utc_now())).isoformat()
                record['intake_sha256'] = key
                record = normalize_forecast(record,allow_field_pin=self.journal.engine.field is not None)
                for asset in record['raw_assets']:
                    archive = self.directory/'raw-sources'/asset['sha256']
                    if not archive.exists():
                        archive.parent.mkdir(parents=True, exist_ok=True)
                        archive.write_bytes(Path(asset['path']).read_bytes())
                    asset['path'] = str(archive.resolve())
                self.emit('FORECAST', {'record':record,'intake_file_sha256':key})
            except (ValueError, KeyError, OSError) as ex:
                if self.journal.failed:
                    raise
                self.emit('SOURCE_INTAKE_REJECTED', {'file_sha256':key,'reason':str(ex)})

    def intake_distributions(self):
        """Consume completed local model artifacts, with real receipt clocks."""
        if self.journal.engine.field:
            return # The frozen FIELD algorithm is its own producer, no daily inbox editing.
        from .distribution import validate_distribution
        for path in sorted((self.directory/'model-inbox').glob('*.json')):
            key = file_hash(path)
            if key in self.seen_inbox:
                continue
            self.seen_inbox.add(key)
            if any(r.get('intake_sha256') == key for r in self.journal.engine.distributions):
                continue
            try:
                record = validate_distribution(json.loads(path.read_text()))
                provenance = record['provenance']
                artifact = Path(provenance['model_artifact_path'])
                artifact_hash = file_hash(artifact)
                require(artifact_hash == provenance['model_artifact_hash'], 'Model artifact hash mismatch')
                if self.journal.engine.plan['mode'] == 'VALUE_RESEARCH_SHADOW_V1':
                    require(artifact_hash in self.journal.engine.plan['preregistration']['accepted_model_hashes'],
                            'Unregistered model artifact')
                require(stamp(record['target_at']) == stamp(self.journal.engine.plan['schedule']['close_utc']),
                        'Model target differs from session')
                archive = self.directory/'model-assets'/artifact_hash
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    archive.write_bytes(artifact.read_bytes())
                require(file_hash(archive) == artifact_hash, 'Archived model artifact hash mismatch')
                provenance['model_artifact_path'] = str(archive.resolve())
                record['intake_sha256'] = key
                self.emit('DISTRIBUTION', {'record':record, 'intake_file_sha256':key})
            except (ValueError, KeyError, OSError) as ex:
                if self.journal.failed:
                    raise
                self.emit('MODEL_INTAKE_REJECTED', {'file_sha256':key,'reason':str(ex)})

    def write_health(self):
        result = self.journal.save(self.directory)
        v = self.journal.engine.latest_valuation or {}
        spot, spot_issue = spot_quote(self.state, time.monotonic_ns())
        atomic_json(self.directory/'health.json', {'schema_version':2,'updated_at':utc_now(),
            'pid':os.getpid(),'run_id':self.run_id,'mode':result['mode'],'event_count':self.count,
            'clock_epoch':self.epoch,'generation':self.generation,'connected':self.state.connected,
            'valuation_count':result['valuation_count'],'current_spx':spot['value'] if spot else None,
            'spot_observed_at':spot['utc'] if spot else None,'spot_issue':spot_issue,'coverage':v.get('coverage'),
            'source_issues':v.get('source_issues', ['NOT_EVALUATED']),
            'distribution_status':'DATA_NOT_READY' if not self.journal.engine.distributions else 'SEE_VALUATION',
            'books':{k:{'status':b['status'],'intents':b['intent_count']} for k,b in result['books'].items()},
            'metrics':self.journal.metrics(),'broker_orders_permitted':False,
            'stop_requested':self.stopping,'schedule':self.journal.engine.plan['schedule']})
        if self.journal.engine.field:
            from .field import write_page
            write_page(self.directory,self.journal.engine,health=json.loads((self.directory/'health.json').read_text()))

    async def observe(self):
        schedule = self.journal.engine.plan['schedule']
        end = stamp(schedule['capture_end_utc'])
        begin = stamp(schedule['open_utc'])-timedelta(minutes=5)
        attempts = 0
        try:
            while datetime.now(timezone.utc) < begin and not self.stopping:
                self.write_health()
                await asyncio.sleep(min(30, max(.01,(begin-datetime.now(timezone.utc)).total_seconds())))
            while datetime.now(timezone.utc) < end and not self.stopping:
                try:
                    await self.connect()
                    self.tickers = []
                    await self.prepare(self.journal.engine.plan['session'].replace('-',''))
                    attempts = 0
                    last_refresh, last_health = 0, 0
                    while self.ib.isConnected() and datetime.now(timezone.utc) < end and not self.stopping:
                        self.emit('TIMER', {})
                        now = time.monotonic()
                        if now-last_health >= 5:
                            self.write_health()
                            self.intake()
                            last_health = now
                        if now-last_refresh >= 10:
                            await self.refresh_universe()
                            last_refresh = now
                        await asyncio.sleep(.05)
                except Exception as ex:
                    if self.journal.failed:
                        raise
                    attempts += 1
                    self.emit('COLLECTOR_FAILURE', {'type':type(ex).__name__,'reason':str(ex),'attempt':attempts})
                    self.write_health()
                    if attempts >= 5:
                        self.emit('SUPERVISOR_DEGRADED', {'reason':'FIVE_FAILURES','retry_after_seconds':60})
                finally:
                    self.ib.disconnect()
                if datetime.now(timezone.utc) < end and not self.stopping:
                    await asyncio.sleep(min(60, 2**min(attempts,6)))
        finally:
            if not self.journal.failed:
                self.emit('RUN_STOPPED', {'requested':self.stopping, 'at_or_after_capture_end':datetime.now(timezone.utc)>=end})
                self.write_health()
                atomic_json(self.directory/'capture-seal.json', {'last_hash':self.store.previous,
                    'event_count':self.count,'result_hash':digest(self.journal.engine.result()),'stopped_at':utc_now()})
            self.store.close()


async def run_observer(plan, directory, root, *, client_id=27216, port=4001):
    require(plan['mode'] == 'OBSERVE_ONLY_V1', 'This runtime is authorized and implemented for observation only')
    await _run_readonly(plan, directory, root, client_id=client_id, port=port)


async def run_value_shadow(plan, directory, root, *, client_id=27217, port=4001):
    require(plan['mode'] == 'VALUE_RESEARCH_SHADOW_V1', 'Real shadow requires its own frozen research plan')
    require(all(s['timing'] == 'FIXED' for s in plan['strategies']), 'Initial real shadow driver is fixed-time only')
    await _run_readonly(plan, directory, root, client_id=client_id, port=port)


async def run_field_shadow(plan, directory, root, *, client_id=27218, port=4001):
    require(plan['mode'] == 'FIELD_PAPER_V1', 'FIELD requires its own plan and directory')
    await _run_readonly(plan,directory,root,client_id=client_id,port=port)


async def _run_readonly(plan, directory, root, *, client_id, port):
    directory = Path(directory)
    if not directory.exists():
        plan = freeze_run(plan,directory,root)
    else:
        plan_frozen = verify_snapshot(directory)
        require(digest(plan_frozen)==digest(validate_plan(plan)), 'Resume plan differs')
        plan = plan_frozen
    manifest = json.loads((directory/'run-manifest.json').read_text())
    require(all(sha(Path(root)/p)==h for p,h in manifest['source_manifest'].items()), 'Observer must run its frozen implementation')
    require(datetime.now(timezone.utc) < stamp(plan['schedule']['capture_end_utc']), 'Session capture already ended')
    # Advisory process lock survives neither crash nor normal close; never duplicate a live writer.
    import fcntl
    with (directory/'observer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        c = Observer(directory,plan,client_id=client_id,port=port)
        for sig in (signal.SIGINT,signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig,lambda:setattr(c,'stopping',True))
        remaining = max(.01, (stamp(plan['schedule']['capture_end_utc'])-datetime.now(timezone.utc)).total_seconds())
        try:
            await asyncio.wait_for(c.observe(), remaining)
        except asyncio.TimeoutError:
            # wait_for cancels through the collector's durable shutdown finally.
            if datetime.now(timezone.utc) < stamp(plan['schedule']['capture_end_utc']):
                raise
