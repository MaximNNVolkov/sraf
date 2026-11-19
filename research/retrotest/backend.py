"""Persistent replay clock and agent feedback; no network or production writes."""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

from .common import HOME, MSK, UTC, canonical, digest, iso, timestamp, write_json
from .dataset import Dataset
from .execution import new_position, process_bar
from .versioning import code_snapshot, model_snapshot

SCHEMA = '''
CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
CREATE TABLE events(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL,
 previous_hash TEXT NOT NULL,hash TEXT NOT NULL);
CREATE TABLE contexts(context_id TEXT PRIMARY KEY,cutoff TEXT NOT NULL,payload TEXT NOT NULL);
'''


class HistoricalClient:
    def __init__(self, dataset, cutoff):
        self.dataset, self.cutoff = dataset, timestamp(cutoff)
        q = dataset.manifest['instrument_metadata']['minPriceIncrement']
        self.tick_size = int(q.get('units', 0)) + int(q.get('nano', 0)) / 1e9

    def get_candles(self, interval, start, end):
        tf = {24: 1440, 7: 10080}.get(interval, interval)
        from usdrubf.moex import Candle
        # Historical isComplete is evaluated at the simulated cutoff, never today's flag.
        return [Candle(timestamp(r['begin']).astimezone(MSK), *(r[k] for k in ('open','high','low','close','volume')), True)
                for r in self.dataset.candles(tf, self.cutoff) if timestamp(r['begin']) >= timestamp(start)]

    def get_quote(self):
        rows = self.dataset.candles(1, self.cutoff, limit=1)
        if not rows:
            raise ValueError('No observable historical price')
        row = rows[0]
        return SimpleNamespace(last=row['close'], source='T-Invest historical replay (last closed M1)',
                               last_trade_time=timestamp(row['end']).astimezone(MSK))

    def trading_status(self):
        # Historical trading status cannot be inferred from current API status.
        return {'tradingStatus': 'HISTORICAL_STATUS_UNKNOWN'}


def append_event(db, kind, at, payload):
    previous = db.execute('SELECT hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
    previous = previous[0] if previous else '0' * 64
    envelope = {'kind': kind, 'at': iso(at), 'payload': payload, 'previous_hash': previous}
    db.execute('INSERT INTO events(kind,at,payload,previous_hash,hash) VALUES(?,?,?,?,?)',
               (kind, iso(at), canonical(payload), previous, digest(envelope)))


def save(db, state):
    db.execute('INSERT OR REPLACE INTO state VALUES(1,?)', (canonical(state),))
    append_event(db, 'STATE_SAVED', state['cursor'], {'sha256': digest(state)})


def create_run(dataset_path: Path, origin: str, version='v0.1001', *, results_root=None, metadata=None):
    dataset = Dataset(dataset_path)
    try:
        model = model_snapshot(version)
        config = model['config']
        day = date.fromisoformat(origin)
        if not max(config['period_start'], dataset.manifest['period_start']) <= origin <= min(config['period_end_inclusive'], dataset.manifest['period_end_inclusive']):
            raise ValueError('Origin is outside the confirmed historical period')
        if origin not in dataset.trading_dates():
            raise ValueError('Origin is not an observed trading date')
        cutoff = datetime.combine(day, time(8), MSK)
        daily_count = dataset.db.execute('SELECT COUNT(*) FROM candles WHERE timeframe=1440 AND end<=?', (iso(cutoff),)).fetchone()[0]
        if daily_count < config['min_daily_warmup']:
            raise ValueError(f'Insufficient warm-up: {daily_count} D1 bars, need {config["min_daily_warmup"]}')
        for tf in (60,240,1440,10080):
            if not dataset.candles(tf, cutoff, limit=1):
                raise ValueError(f'Missing historical timeframe: {tf}')
        run_id = uuid.uuid4().hex
        version_root = (results_root or HOME / 'results') / version
        version_root.mkdir(parents=True, exist_ok=True)
        lock = version_root / 'model.json'
        if lock.exists():
            if json.loads(lock.read_text())['sha256'] != model['sha256']:
                raise ValueError('Model changed under an existing version; bump the version before tuning')
        else:
            with lock.open('x') as output:
                output.write(canonical(model))
        path = version_root / run_id
        path.mkdir(parents=True, exist_ok=False)
        manifest = {'run_id': run_id, 'version': version, 'dataset_path': str(dataset.path),
                    'dataset_sha256': dataset.hash, 'model': model, 'code': code_snapshot(),
                    'origin': origin, 'mode': 'non-deterministic-agent-research-replay',
                    'metadata': metadata or {}, 'created_at': iso(datetime.now(UTC))}
        write_json(path / 'manifest.json', manifest)
        state = {'run_id': run_id, 'version': version, 'cursor': iso(cutoff), 'previous_cursor': None,
                 'phase': 'RUNNING', 'plan': None, 'pending_entry': None, 'position': None,
                 'trades': [], 'last_decision': None, 'decision_due': True,
                 'context_id': None, 'revision': 0}
        db = sqlite3.connect(path / 'run.sqlite')
        db.executescript(SCHEMA)
        with db:
            save(db, state)
            append_event(db, 'RUN_CREATED', cutoff, {'manifest_sha256': digest(manifest)})
        db.close()
        return {'run_id': run_id, 'version': version, 'path': str(path)}
    finally:
        dataset.close()


class Replay:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.manifest = json.loads((self.path / 'manifest.json').read_text())
        self.dataset = Dataset(Path(self.manifest['dataset_path']))
        if self.dataset.hash != self.manifest['dataset_sha256']:
            self.dataset.close()
            raise ValueError('Dataset manifest changed since run creation')
        frozen = self.manifest['model']
        if digest(frozen['files']) != frozen['sha256'] or frozen['config'] != json.loads(frozen['files']['config.json']):
            raise ValueError('Frozen model snapshot is inconsistent')
        self.config = frozen['config']
        if code_snapshot()['sha256'] != self.manifest['code']['sha256']:
            self.dataset.close()
            raise ValueError('Replay code changed: create a new run to preserve provenance')
        self.db = sqlite3.connect(self.path / 'run.sqlite')
        self.db.row_factory = sqlite3.Row
        # Audit integrity is checked before accepting any mutation or serving a context.
        previous = '0' * 64
        for event in self.db.execute('SELECT * FROM events ORDER BY seq'):
            envelope = {'kind': event['kind'], 'at': event['at'], 'payload': json.loads(event['payload']), 'previous_hash': previous}
            if event['previous_hash'] != previous or event['hash'] != digest(envelope):
                self.close()
                raise ValueError('Audit hash chain is damaged')
            previous = event['hash']
        last_state = self.db.execute("SELECT payload FROM events WHERE kind='STATE_SAVED' ORDER BY seq DESC LIMIT 1").fetchone()
        if not last_state or json.loads(last_state[0])['sha256'] != digest(self.state):
            self.close()
            raise ValueError('Run state does not match its audit record')

    def close(self):
        self.db.close()
        self.dataset.close()

    @property
    def state(self):
        return json.loads(self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()[0])

    def _payload(self, state):
        from usdrubf.process import collect_compact
        cutoff = timestamp(state['cursor']).astimezone(MSK)
        payload, _, _ = collect_compact('USDRUBF', cutoff, HistoricalClient(self.dataset, cutoff))
        payload.update(model_version=state['version'], strategy_version=state['version'],
                       simulation=True, run_id=state['run_id'], source_cutoff=state['cursor'],
                       historical_trading_status='UNKNOWN', phase=state['phase'],
                       market_setup=state['plan'], simulated_position=state['position'],
                       pending_entry=state['pending_entry'], previous_agent_analysis=state['last_decision'],
                       dataset_sha256=self.dataset.hash, dataset_quality=self.dataset.manifest['quality_status'])
        # Prod-shaped facts plus all locally completed OHLCV, never future bars.
        payload['candles'] = {key: self.dataset.candles(tf, cutoff, limit=limit) for key,tf,limit in
                              [('w1',10080,260),('d1',1440,1100),('h4',240,1100),('h1',60,1000)]}
        new = self.dataset.candles(60, cutoff, after=state['previous_cursor']) if state['previous_cursor'] else []
        payload['new_h1_candles'] = new
        payload['agent_handoff'] = {
            'task': 'MONITOR_POSITION' if state['position'] else 'WAIT_ENTRY' if state['pending_entry'] else 'REVIEW_OR_CONFIRM' if state['plan'] else 'CREATE_PLAN',
            'actions': ['WAIT'] if state['position'] or state['pending_entry'] else ['PLAN','NO_TRADE','WAIT','CANCEL'] +
                       (['CONFIRM_ENTRY'] if state['plan'] and cutoff.hour > 8 else []),
            'rule': 'CONFIRM_ENTRY only after agent verifies its prior condition; entry at monitoring time +15m. No real broker calls.'}
        return payload

    def context(self):
        state = self.state
        if state['context_id']:
            row = self.db.execute('SELECT payload FROM contexts WHERE context_id=?', (state['context_id'],)).fetchone()
            return json.loads(row[0])
        payload = self._payload(state)
        context_id = digest({'revision': state['revision'], 'payload': payload})
        payload['context_id'] = context_id
        state['context_id'] = context_id
        with self.db:
            self.db.execute('INSERT INTO contexts VALUES(?,?,?)', (context_id, state['cursor'], canonical(payload)))
            append_event(self.db, 'CONTEXT_ISSUED', state['cursor'], {'context_id': context_id, 'payload_sha256': digest(payload)})
            save(self.db, state)
        return payload

    def _check(self, state, decision):
        if state['phase'] != 'RUNNING':
            raise ValueError('Run has finished')
        if not state['decision_due']:
            raise ValueError('Decision already submitted; advance clock first')
        if not state['context_id'] or decision.get('context_id') != state['context_id']:
            raise ValueError('A fresh context_id is required')
        if not isinstance(decision.get('reason'), str) or not decision['reason'].strip():
            raise ValueError('Decision requires a reason')

    def submit(self, decision):
        state = self.state
        self._check(state, decision)
        action = decision.get('action')
        if action not in {'PLAN','NO_TRADE','WAIT','CANCEL','CONFIRM_ENTRY'}:
            raise ValueError('Unsupported action')
        if (state['position'] or state['pending_entry']) and action != 'WAIT':
            raise ValueError('Entry/position is locked; only WAIT is allowed')
        if action == 'PLAN':
            plan = {k: decision.get(k) for k in ('direction','entry','stop','tp1','tp2','entry_condition','reason','analysis')}
            if plan['direction'] not in {'LONG','SHORT'}:
                raise ValueError('Direction must be LONG or SHORT')
            levels = [plan[k] for k in ('entry','stop','tp1','tp2')]
            if not all(type(v) in (int,float) and math.isfinite(v) and v > 0 for v in levels):
                raise ValueError('All four finite positive price levels are required')
            e,s,t1,t2 = levels
            if not ((s < e < t1 < t2) if plan['direction'] == 'LONG' else (s > e > t1 > t2)):
                raise ValueError('Invalid direction/level ordering; TP2 must be beyond TP1')
            tick = HistoricalClient(self.dataset, state['cursor']).tick_size
            if any(abs(x / tick - round(x / tick)) > 1e-5 for x in levels):
                raise ValueError('Price level is not aligned with metadata tick size')
            if not isinstance(plan['entry_condition'], str) or not plan['entry_condition'].strip():
                raise ValueError('PLAN must describe the exact condition to confirm later')
            plan.update(created_at=state['cursor'], context_id=state['context_id'])
            state['plan'] = plan
        elif action in {'NO_TRADE','CANCEL'}:
            if action == 'NO_TRADE' and not decision.get('entry_condition'):
                raise ValueError('NO_TRADE requires a condition for review')
            state['plan'] = None
        elif action == 'CONFIRM_ENTRY':
            if not state['plan'] or timestamp(state['cursor']).astimezone(MSK).hour == 8:
                raise ValueError('Confirm a prior PLAN during monitoring after 08:00')
            evidence = decision.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                raise ValueError('Confirmation requires evidence referencing visible bars')
            visible = {r['begin'] for tf in (60,240,1440,10080) for r in self.dataset.candles(tf,state['cursor'])}
            for item in evidence:
                if not isinstance(item, dict) or not item.get('reason') or iso(item['begin']) not in visible:
                    raise ValueError('Evidence must reference a completed visible candle with reason')
            state['pending_entry'] = {'confirmed_at': state['cursor'],
                                      'entry_at': iso(timestamp(state['cursor']) + timedelta(minutes=15)),
                                      'confirmation': decision}
        state['last_decision'] = decision
        state['decision_due'] = False
        with self.db:
            append_event(self.db, 'AGENT_DECISION', state['cursor'], decision)
            save(self.db, state)
        return {'status': 'ACCEPTED', 'state': state}

    def _next_cutoff(self, state):
        current = timestamp(state['cursor']).astimezone(MSK)
        hours = self.config['monitor_hours']
        for hour in hours:
            if hour > current.hour:
                return datetime.combine(current.date(), time(hour), MSK)
        for day in self.dataset.trading_dates():
            if day > str(current.date()) and day >= self.config['period_start']:
                return datetime.combine(date.fromisoformat(day), time(8), MSK)
        return timestamp(self.dataset.manifest['data_end'])

    def _move(self, state, until):
        events = []
        old = timestamp(state['cursor'])
        end = min(timestamp(until), timestamp(self.dataset.manifest['data_end']))
        if end < old:
            raise ValueError('Clock cannot move backward')
        execution_start = old
        pending = state['pending_entry']
        if pending and timestamp(pending['entry_at']) <= end:
            bar = self.dataset.entry_bar(pending['confirmed_at'])
            state['pending_entry'] = None
            if self.dataset.conflicts_between(pending['confirmed_at'], pending['entry_at']):
                events.append({'type':'CANCEL_ENTRY_DATA_CONFLICT', 'at':pending['entry_at']})
                state['plan'] = None
            elif not bar or bar['end'] != pending['entry_at']:
                events.append({'type':'CANCEL_ENTRY_NO_BAR', 'at':pending['entry_at']})
                state['plan'] = None
            else:
                position = new_position(state['plan'], bar['close'], bar['end'])
                if position is None:
                    events.append({'type':'CANCEL_ENTRY_INVALID_LEVELS','at':bar['end'],'price':bar['close']})
                    state['plan'] = None
                else:
                    position['plan'] = state['plan']
                    position['confirmation'] = pending['confirmation']
                    state['position'] = position
                    execution_start = timestamp(bar['end'])
                    events.append({'type':'ENTRY','at':bar['end'],'price':bar['close'], 'source_bar':bar})
        if state['position']:
            for bar in self.dataset.execution(execution_start, end):
                # Never apply price extremes from before the delayed entry.
                if timestamp(bar['begin']) < timestamp(state['position']['entry_time']):
                    continue
                conflicts = self.dataset.conflicts_between(bar['begin'], bar['end'])
                if conflicts:
                    state['position'].update(status='DATA_CONFLICT',outcome='UNRESOLVED')
                    events.append({'type':'DATA_CONFLICT','time_from':bar['begin'],'time_to':bar['end'],'conflicts':conflicts})
                else:
                    events.extend(process_bar(state['position'], bar))
                if state['position']['status'] != 'ACTIVE':
                    if state['position']['status'] in {'AMBIGUOUS','DATA_CONFLICT'}:
                        state['phase'] = 'UNRESOLVED'
                    state['trades'].append(state['position'])
                    state['position'] = None
                    state['plan'] = None
                    break
        state.update(previous_cursor=state['cursor'], cursor=iso(end), context_id=None,
                     decision_due=state['phase'] == 'RUNNING', revision=state['revision'] + 1)
        if end >= timestamp(self.dataset.manifest['data_end']):
            self._end(state, events)
        return events

    def _end(self, state, events):
        if state['position']:
            state['position'].update(status='OPEN_AT_DATA_END',outcome='UNRESOLVED')
            state['trades'].append(state['position'])
            state['position'] = None
        if state['pending_entry']:
            events.append({'type':'ENTRY_PENDING_AT_DATA_END','pending':state['pending_entry']})
            state['pending_entry'] = None
        state.update(phase='FINISHED', decision_due=False, context_id=None)

    def advance(self):
        state = self.state
        if state['phase'] != 'RUNNING' or state['decision_due']:
            raise ValueError('Submit the current agent decision before advancing')
        until = self._next_cutoff(state)
        with self.db:
            events = self._move(state, until)
            for event in events:
                append_event(self.db, 'EXECUTION', state['cursor'], event)
            append_event(self.db, 'CLOCK_ADVANCED', state['cursor'], {'previous_cursor':state['previous_cursor']})
            save(self.db, state)
        return {'state':state,'events':events,'context':self.context() if state['phase'] == 'RUNNING' else None}

    def finish(self):
        state = self.state
        if state['phase'] != 'RUNNING':
            return self.results()
        # Explicit terminal operation: disclose outcomes, permanently end this run.
        with self.db:
            events = self._move(state, self.dataset.manifest['data_end'])
            for event in events:
                append_event(self.db, 'EXECUTION', state['cursor'], event)
            append_event(self.db, 'RUN_FINISHED', state['cursor'], {})
            save(self.db, state)
        return self.results()

    def results(self):
        state = self.state
        closed = [p for p in state['trades'] if p['status'] == 'CLOSED']
        result = {'run_id':state['run_id'],'version':state['version'],'phase':state['phase'],
                  'cursor':state['cursor'],'trades':state['trades'],'open_position':state['position'],
                  'pending_entry':state['pending_entry'],'pending_plan':state['plan'],
                  'closed_count':len(closed),'wins':sum(p['outcome']=='WIN' for p in closed),
                  'losses':sum(p['outcome']=='LOSS' for p in closed),
                  'breakeven':sum(p['outcome']=='BREAKEVEN' for p in closed),
                  'unresolved':sum(p['status']!='CLOSED' for p in state['trades']),
                  'net_pct':None,'return_basis':'gross price return; not account return; no funding/fees/slippage',
                  'dataset_sha256':self.dataset.hash,'model_sha256':self.manifest['model']['sha256']}
        write_json(self.path / 'results.json', result)
        return result
