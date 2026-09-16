"""Deterministic event reduction and transactional research journal.

Input plus its decisions/intents/executions commit in one SQLite transaction.
No derived output is exposed before commit. A write failure poisons the writer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import time

from .contracts import Clock, digest, number, require, stamp, validate_plan
from .distribution import validate_distribution
from .engine import spot_quote
from .events import EventStore, atomic_json, read_events
from .forecast import ForecastBook, eligibility, normalize_forecast
from .policy import apply_book_event, decide, new_book
from .quotes import ResearchMarketState, candidate_universe, quote_universe
from .shadow import shadow_step
from .valuation import value_candidates

DERIVED = {'VALUATION', 'DECISION', 'INTENT', 'EXECUTION', 'BOOK_CLOSED', 'SOURCE_IGNORED_FOR_INTENT'}
from .field import DERIVED_FIELD
DERIVED |= DERIVED_FIELD


class ResearchEngine:
    def __init__(self, plan):
        self.plan = validate_plan(plan)
        self.market = ResearchMarketState()
        self.forecasts = ForecastBook()
        self.distributions = []
        self.books = {s['id']: new_book(s) for s in self.plan['strategies']}
        self.last_evaluation = None
        self.latest_valuation = None
        self.valuation_count = 0
        self.last_epoch = None
        self.last_clock = None
        self.closed = False
        self.field = None
        if self.plan['mode'] == 'FIELD_PAPER_V1':
            from .field import FieldTracker
            self.field = FieldTracker(self.plan)

    def source(self, as_of):
        product = self.plan.get('source_product_id')
        if not product:
            return None
        source = self.forecasts.latest(product, self.plan['schedule']['close_utc'], as_of)
        return source

    def distribution(self, as_of):
        available = [r for r in self.distributions if stamp(r['available_at']) <= stamp(as_of)]
        if self.field:
            available = [r for r in available if
                0 <= (stamp(as_of)-stamp(r['conditioning_as_of'])).total_seconds() <= 1 and
                self.field.diagnostic.get('status') == 'READY']
        return available[-1] if available else None

    def batch(self, clock, source):
        spot, _ = spot_quote(self.market, clock.mono_ns)
        usable_source = source if not eligibility(source, clock.utc, self.plan['schedule']['close_utc']) else None
        universe = candidate_universe(spot['value'] if spot else None,
                                      usable_source['median'] if usable_source else None,
                                      market_neighbors=self.field is not None)
        batch = quote_universe(self.market, universe, clock, self.plan,
                               synthetic=self.plan['mode'] == 'SYNTHETIC_REPLAY_V1')
        batch['universe_issues'] = ([] if spot else ['SPOT_CENTER_MISSING']) + ([] if usable_source or self.field else ['FORECAST_CENTER_MISSING'])
        return batch

    def close_events(self, reason):
        result = []
        for key, book in self.books.items():
            if book['status'] == 'OBSERVING':
                unknown = book['had_unknown'] or not book['decisions'] or reason != 'ENTRY_WINDOW_CLOSED'
                result.append(('BOOK_CLOSED', {'strategy_id': key,
                               'status': 'CLOSED_UNKNOWN' if unknown else 'CLOSED_ABSTAIN', 'reason': reason}))
        return result

    def on_event(self, event):
        kind, p = event['event_type'], event['payload']
        if kind in DERIVED:
            if kind in DERIVED_FIELD:
                require(self.field is not None, 'FIELD event in legacy mode')
                self.field.apply(kind,p)
            elif kind == 'VALUATION':
                self.latest_valuation = deepcopy(p)
                self.valuation_count += 1
            else:
                apply_book_event(self.books[p['strategy_id']], kind, p)
                if self.field and kind == 'DECISION': self.field.apply(kind,p)
            return []
        epoch = p.get('_clock_epoch', self.last_epoch or 'UNSET')
        clock = Clock(event['seq'], event['monotonic_ns'], event['recorded_at'], epoch)
        epoch_change = self.last_epoch is not None and epoch != self.last_epoch
        if self.last_clock is not None and not epoch_change:
            require(clock.mono_ns >= self.last_clock.mono_ns and stamp(clock.utc) >= stamp(self.last_clock.utc),
                    'Regressing clock requires a new epoch')
        self.last_epoch, self.last_clock = epoch, clock
        if epoch_change:
            self.market.fields, self.market.quote_sides = {}, {}
        self.market.apply(event)
        if self.field:
            self.field.market_event(event,self.market,clock,epoch_change)
        if kind == 'FORECAST':
            record = normalize_forecast(p['record'])
            if self.plan['mode'] != 'SYNTHETIC_REPLAY_V1':
                record['available_at'] = max(stamp(record['available_at']), stamp(clock.utc)).isoformat()
            self.forecasts.add(record)
        if kind == 'DISTRIBUTION':
            r = validate_distribution(p['record'])
            require(stamp(r['available_at']) <= stamp(clock.utc), 'Distribution delivered before declared availability')
            require(stamp(r['available_at']) >= stamp(event['recorded_at']) or p.get('historical_import', False),
                    'Live distribution availability cannot be backdated')
            if p.get('historical_import'):
                require(self.plan['mode'] == 'SYNTHETIC_REPLAY_V1', 'Historical model import only in synthetic replay')
            self.distributions.append(r)
        if kind == 'RESEARCH_PLAN':
            require(p['plan_hash'] == digest(self.plan), 'Plan hash mismatch')
        proposals = []
        interruption = None
        if epoch_change or kind in {'DISCONNECTED', 'DATA_LOST', 'RESTART', 'RUN_STOPPED'}:
            interruption = 'CLOCK_EPOCH_CHANGED' if epoch_change else kind
        source = self.source(clock.utc)
        if kind == 'FORECAST' and source and source['status'] != 'VALIDATED':
            interruption = 'SOURCE_' + source['status']
        if kind == 'FORECAST' and source and not interruption:
            for book in self.books.values():
                if book['status'] == 'INTENT_PERSISTED' and book['intent']['forecast_hash'] != source['content_hash']:
                    proposals.append(('SOURCE_IGNORED_FOR_INTENT', {'strategy_id':book['strategy_id'],
                        'intent_id':book['intent']['intent_id'],'new_forecast_hash':source['content_hash'],
                        'reason':'NEW_FORECAST_DOES_NOT_REPRICE_LOCKED_INTENT'}))
        if interruption:
            for b in self.books.values():
                execution = shadow_step(b, None, clock, interruption=interruption)
                if execution:
                    proposals.append(('EXECUTION', execution))
            # FIELD may continue observing after recovery; committed intent
            # count still prevents another attempt. Old modes retain closure.
            if kind == 'RUN_STOPPED' or (kind == 'RESTART' or epoch_change) and not self.field:
                proposals.extend(self.close_events(interruption))
        if kind in {'MARKET_BARRIER', 'TIMER', 'HEARTBEAT', 'SYNTHETIC_FRAME'} and not interruption:
            for book in self.books.values():
                if book['status'] != 'INTENT_PERSISTED':
                    continue
                i = book['intent']
                universe = [{'candidate_id': i['candidate_id'], 'center': i['center'], 'width': i['width']}]
                if kind == 'SYNTHETIC_FRAME':
                    require(self.plan['mode'] == 'SYNTHETIC_REPLAY_V1', 'Synthetic frame prohibited')
                    q = next((q for q in p['batch']['rows'] if q['candidate_id'] == i['candidate_id']), None)
                else:
                    q = quote_universe(self.market, universe, clock, self.plan,
                                       synthetic=self.plan['mode'] == 'SYNTHETIC_REPLAY_V1')['rows'][0]
                execution = shadow_step(book, q, clock)
                if execution:
                    proposals.append(('EXECUTION', execution))
        if (kind == 'EVALUATE' or kind == 'SYNTHETIC_FRAME' and p.get('scheduled_at')) and not interruption:
            if kind == 'SYNTHETIC_FRAME':
                require(self.plan['mode'] == 'SYNTHETIC_REPLAY_V1', 'Synthetic frame prohibited')
                batch, distribution = deepcopy(p['batch']), deepcopy(p.get('distribution'))
                require(stamp(batch['as_of']) == stamp(clock.utc), 'Fixture snapshot clock mismatch')
                # Input seq always belongs to the durable event, never a client assertion.
                batch['snapshot_seq'] = clock.seq
                for q in batch['rows']:
                    q['snapshot_seq'] = clock.seq
            else:
                batch, distribution = self.batch(clock, source), self.distribution(clock.utc)
            scheduled = p['scheduled_at']
            require(self.last_evaluation is None or stamp(scheduled) > stamp(self.last_evaluation), 'Duplicate/regressing evaluation')
            self.last_evaluation = scheduled
            spec = {**self.plan['valuation'], 'budget_points': self.plan['execution']['budget_points'],
                    'accepted_model_hashes': self.plan.get('preregistration', {}).get('accepted_model_hashes', [])}
            value = value_candidates(distribution, batch, spec, self.plan['mode'])
            value['timing_issues'] = []
            if kind == 'EVALUATE' and any(stamp(ref['utc']) > stamp(scheduled)
                    for fields in self.market.fields.values() for ref in fields.values()):
                value['timing_issues'].append('POST_DEADLINE_PACKET_INPUT')
                value['selected'], value['state'] = None, 'UNKNOWN'
            value['source_issues'] = eligibility(source, clock.utc, self.plan['schedule']['close_utc'])
            if self.field:
                from .timing import lookahead
                value['scheduled_at'] = scheduled
                value['model_diagnostic'] = deepcopy(self.field.diagnostic)
                value['timing'] = lookahead(distribution,value,self.plan)
                if value['timing_issues']:
                    value['timing'].update(status='INPUT_UNAVAILABLE',action='WAIT')
                prediction = self.field.prediction(distribution,value,clock)
                if prediction:
                    proposals.append(('FIELD_PREDICTION',prediction))
                proposals.extend(self.field.mature(self.market,clock,value))
            proposals.append(('VALUATION', value))
            for strategy in self.plan['strategies']:
                proposals.extend(decide(strategy, self.books[strategy['id']], value, source,
                                        self.plan, clock, scheduled))
        elif self.field and kind == 'TIMER':
            proposals.extend(self.field.mature(self.market,clock))
        if stamp(clock.utc) >= stamp(self.plan['schedule']['entry_end_utc']):
            proposals.extend(self.close_events('ENTRY_WINDOW_CLOSED'))
            self.closed = True
        seen_closes = set()
        unique = []
        for kind, payload in proposals:
            if kind == 'BOOK_CLOSED':
                if payload['strategy_id'] in seen_closes:
                    continue
                seen_closes.add(payload['strategy_id'])
            unique.append((kind, payload))
        return unique

    def result(self):
        result = {'schema_version': 2, 'run_id': self.plan['run_id'], 'mode': self.plan['mode'],
                'session': self.plan['session'], 'target_at': self.plan['schedule']['close_utc'],
                'plan_hash': digest(self.plan), 'books': deepcopy(self.books), 'entry_window_closed': self.closed,
                'valuation_count': self.valuation_count, 'settlement_status': 'PENDING_OFFICIAL_PM_SETTLEMENT',
                'fixed_costs': deepcopy(self.plan['fixed_costs']), 'fee_status': 'UNCONFIRMED_ACCOUNT_SCENARIO'}
        if self.field:
            result['field'] = self.field.summary()
        return result


class ResearchJournal:
    def __init__(self, path, plan, *, store=None, engine=None):
        self.store = store or EventStore(path)
        self.engine = engine or ResearchEngine(plan)
        self.failed = False
        self.latencies_ns = []
        self.stage_samples = []

    def append(self, kind, payload, *, clock_epoch, mono, utc, generation=0):
        require(not self.failed, 'Writer is failed; recovery required')
        require(kind not in DERIVED, 'Derived events are internal to the transaction')
        start = time.monotonic_ns()
        prior_hash = self.store.previous
        try:
            self.store.db.execute('BEGIN IMMEDIATE')
            e = self.store.append(kind, {**deepcopy(payload), '_clock_epoch': clock_epoch},
                                  run_id=self.engine.plan['run_id'], generation=generation, mono=mono, utc=utc)
            input_written = time.monotonic_ns()
            proposals = self.engine.on_event(e)
            evaluated = time.monotonic_ns()
            committed = []
            for name, value in proposals:
                d = self.store.append(name, value, run_id=self.engine.plan['run_id'],
                                      generation=generation, mono=mono, utc=utc)
                self.engine.on_event(d)
                committed.append(d)
            self.store.db.execute('COMMIT')
        except BaseException:
            if self.store.db.in_transaction:
                self.store.db.execute('ROLLBACK')
            self.store.previous = prior_hash
            self.failed = True
            raise
        committed_at = time.monotonic_ns()
        self.latencies_ns.append(committed_at-start)
        self.stage_samples.append({'kind':kind, 'input_seq':e['seq'], 'input_write_ms':(input_written-start)/1e6,
            'reduction_ms':(evaluated-input_written)/1e6,'derived_write_commit_ms':(committed_at-evaluated)/1e6})
        # Keep a bounded rolling instrumented window; whole-run counts live in events.
        if len(self.latencies_ns) > 10000:
            del self.latencies_ns[:5000]
            del self.stage_samples[:5000]
        return e, committed

    def metrics(self):
        times = sorted(self.latencies_ns)
        return {'measurement': 'journal_begin_to_commit_local_wall_duration', 'sample_window_count': len(times),
                'p99_ms': times[min(len(times)-1, int(len(times)*.99))]/1e6 if times else None,
                'stage_p99_ms': {k: sorted(r[k] for r in self.stage_samples)[min(len(times)-1,int(len(times)*.99))] if times else None
                                 for k in ('input_write_ms','reduction_ms','derived_write_commit_ms')},
                'queue_mode': 'synchronous_single_writer', 'network_latency_measured': False}

    def save(self, directory):
        require(not self.failed, 'Cannot save uncommitted state after write failure')
        directory = Path(directory)
        result = self.engine.result()
        atomic_json(directory/'decision.json', result)
        atomic_json(directory/'performance.json', self.metrics())
        atomic_json(directory/'decision-digest.json', {'result_sha256': digest(result)})
        return result


def replay_research(directory, *, check_result=True):
    directory = Path(directory)
    import json
    plan = json.loads((directory/'plan.json').read_text())
    engine = ResearchEngine(plan)
    pending, count, final_hash = [], 0, None
    for e in read_events(directory/'events.sqlite'):
        count += 1
        if count == 1:
            require(e['event_type'] == 'RESEARCH_PLAN', 'First committed event must bind the frozen plan')
        require(e['run_id'] == plan['run_id'], 'Wrong run identity')
        final_hash = e['hash']
        if e['event_type'] in DERIVED:
            require(pending, 'Unexpected derived event')
            kind, payload = pending.pop(0)
            require((kind, payload) == (e['event_type'], e['payload']), 'Derived event differs from frozen replay')
            engine.on_event(e)
        else:
            require(not pending, 'Missing derived event before next input')
            pending = engine.on_event(e)
    require(not pending, 'Incomplete transaction/derived tail')
    require(count > 0, 'Empty research journal')
    expected = json.loads((directory/'decision.json').read_text()) if check_result else engine.result()
    require(engine.result() == expected, 'Research result replay mismatch')
    if check_result and (directory/'capture-seal.json').exists():
        seal = json.loads((directory/'capture-seal.json').read_text())
        require(seal['last_hash'] == final_hash and seal['event_count'] == count and seal['result_hash'] == digest(expected),
                'Final capture seal mismatch')
    return {'events_verified': count, 'last_hash': final_hash, 'result_match': True, 'trace_match': True,
            'result_sha256': digest(expected), 'scope': 'Read-only full research event replay'}, engine
