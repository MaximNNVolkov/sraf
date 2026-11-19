"""Download once, validate, seal; replay only opens SQLite in read-only mode."""
from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, time as daytime, timedelta
from pathlib import Path

from .common import HOME, MSK, UTC, canonical, digest, file_hash, iso, timestamp, write_json

TIMEFRAMES = (1, 15, 30, 60, 240, 1440, 10080)
SCHEMA = '''
CREATE TABLE candles(
 timeframe INTEGER NOT NULL, begin TEXT NOT NULL, end TEXT NOT NULL,
 open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
 volume REAL NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY(timeframe,begin), CHECK(end>begin),
 CHECK(low>0 AND high>=open AND high>=close AND low<=open AND low<=close AND volume>=0)
) WITHOUT ROWID;
CREATE INDEX candle_cutoff ON candles(timeframe,end);
CREATE TABLE requests(id INTEGER PRIMARY KEY, timeframe INTEGER, begin TEXT, end TEXT,
 source TEXT NOT NULL, rows INTEGER NOT NULL, sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL);
'''


def validate_prices(o, h, l, c, v):
    if not all(math.isfinite(x) for x in (o, h, l, c, v)) or not (0 < l <= min(o, c) <= max(o, c) <= h and v >= 0):
        raise ValueError('Invalid OHLCV')


def insert(db, tf, begin, o, h, l, c, v, source):
    validate_prices(o, h, l, c, v)
    begin = timestamp(begin)
    row = (tf, iso(begin), iso(begin + timedelta(minutes=tf)), o, h, l, c, v, source)
    old = db.execute('SELECT * FROM candles WHERE timeframe=? AND begin=?', row[:2]).fetchone()
    if old is not None:
        if tuple(old) != row:
            raise ValueError(f'Conflicting duplicate: {tf} {begin}')
        return
    db.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?)', row)


def download_archive(client, year: int, raw: Path) -> tuple[bytes, str]:
    url = 'https://invest-public-api.tbank.ru/history-data?' + urllib.parse.urlencode({
        'instrumentId': client.instrument['uid'], 'year': year})
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / f'USDRUBF_{year}.zip'
    if path.exists():
        data = path.read_bytes()
        if zipfile.is_zipfile(io.BytesIO(data)):
            return data, url
        raise ValueError(f'Invalid cached archive: {path}')
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + client.token})
            with urllib.request.urlopen(request, timeout=50) as response:
                data = response.read()
            if not zipfile.is_zipfile(io.BytesIO(data)):
                raise ValueError('History response is not a ZIP archive')
            temp = path.with_suffix('.tmp')
            temp.write_bytes(data)
            temp.replace(path)
            return data, url
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError('Archive unavailable')


def build_dataset(destination: Path, *, start='2023-01-01', end='2026-06-01', history_start='2022-04-26', progress=print):
    # Only this explicit ingestion command loads the existing read-only data token.
    from usdrubf.tbank import INTERVALS, TBankClient
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / 'candles.sqlite'
    if target.exists() or (destination / 'manifest.json').exists():
        raise ValueError('Dataset already sealed; use a new directory')
    start_date, end_date, warm_date = map(date.fromisoformat, (start, end, history_start))
    if not warm_date <= start_date <= end_date or end_date >= datetime.now(MSK).date():
        raise ValueError('Invalid historical range')
    left = datetime.combine(warm_date, daytime(), MSK)
    right = datetime.combine(end_date + timedelta(days=1), daytime(), MSK)
    client = TBankClient('USDRUBF')
    instrument = client.instrument
    staging = destination / 'candles.building.sqlite'
    # Incomplete datasets are resumed at the request level, never exposed to replay.
    db = sqlite3.connect(staging)
    if not db.execute("SELECT name FROM sqlite_master WHERE name='candles'").fetchone():
        db.executescript(SCHEMA)
    expected = {'start': start, 'end': end, 'history_start': history_start, 'instrument_uid': instrument['uid']}
    build_config = destination / 'build.json'
    if build_config.exists() and json.loads(build_config.read_text()) != expected:
        raise ValueError('Cannot resume a different dataset into the same directory')
    write_json(build_config, expected)
    raw = HOME / 'raw'
    for year in range(warm_date.year, end_date.year + 1):
        if db.execute('SELECT 1 FROM requests WHERE timeframe=1 AND begin=?', (str(year),)).fetchone():
            continue
        data, url = download_archive(client, year, raw)
        count = 0
        with db, zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in sorted(archive.namelist()):
                if not name.endswith('.csv'):
                    continue
                with archive.open(name) as member:
                    for row in csv.reader(io.TextIOWrapper(member, encoding='utf-8-sig'), delimiter=';'):
                        if not row:
                            continue
                        if row[0] != instrument['uid']:
                            raise ValueError('Unexpected instrument in archive')
                        begin = timestamp(row[1])
                        if left <= begin < right:
                            o, c, h, l, v = map(float, row[2:7])
                            insert(db, 1, begin, o, h, l, c, v, 'T-Invest minute archive')
                            count += 1
            db.execute('INSERT INTO requests(timeframe,begin,end,source,rows,sha256,fetched_at) VALUES(?,?,?,?,?,?,?)',
                       (1, str(year), str(year + 1), url, count, __import__('hashlib').sha256(data).hexdigest(), iso(datetime.now(UTC))))
        progress(f'M1 {year}: {count} rows', flush=True)
    for tf in TIMEFRAMES[1:]:
        api_tf = {1440: 24, 10080: 7}.get(tf, tf)
        days = INTERVALS[api_tf][1]
        cursor = left
        batches = 0
        while cursor < right:
            stop = min(cursor + timedelta(days=days), right)
            if not db.execute('SELECT 1 FROM requests WHERE timeframe=? AND begin=? AND end=?', (tf, iso(cursor), iso(stop))).fetchone():
                rows = client.get_candles(api_tf, cursor, stop)
                count = 0
                serial = []
                with db:
                    for c in rows:
                        if not c.is_complete or not left <= c.begin < right:
                            continue
                        insert(db, tf, c.begin, c.open, c.high, c.low, c.close, c.volume, 'T-Invest GetCandles exchange')
                        serial.append(c.to_dict())
                        count += 1
                    db.execute('INSERT INTO requests(timeframe,begin,end,source,rows,sha256,fetched_at) VALUES(?,?,?,?,?,?,?)',
                               (tf, iso(cursor), iso(stop), 'T-Invest GetCandles exchange', count, digest(serial), iso(datetime.now(UTC))))
                batches += 1
                if batches % 20 == 0:
                    progress(f'{tf}m through {stop.date()}', flush=True)
                time.sleep(0.12)
            cursor = stop
        progress(f'{tf}m: complete', flush=True)
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('SQLite integrity check failed')
    counts = {str(tf): dict(zip(('rows', 'first_begin', 'last_end'), db.execute(
        'SELECT COUNT(*),MIN(begin),MAX(end) FROM candles WHERE timeframe=?', (tf,)).fetchone())) for tf in TIMEFRAMES}
    if any(not x['rows'] for x in counts.values()):
        raise ValueError('Required timeframe is empty')
    # Missing M1 rows can mean no trades; never claim exchange-calendar completeness.
    day_counts = [dict(zip(('utc_date','minute_rows'), row)) for row in db.execute(
        'SELECT substr(begin,1,10),COUNT(*) FROM candles WHERE timeframe=1 GROUP BY substr(begin,1,10)')]
    warmup_count = db.execute('SELECT COUNT(*) FROM candles WHERE timeframe=1440 AND end<=?',
                             (iso(datetime.combine(start_date, daytime(8), MSK)),)).fetchone()[0]
    requests = [dict(zip(('timeframe','begin','end','source','rows','sha256','fetched_at'), row)) for row in db.execute(
        'SELECT timeframe,begin,end,source,rows,sha256,fetched_at FROM requests ORDER BY id')]
    from .quality import reconcile_and_refresh
    reconciliation = reconcile_and_refresh(db, client, left, right, progress)
    requests = [dict(zip(('timeframe','begin','end','source','rows','sha256','fetched_at'), row)) for row in db.execute(
        'SELECT timeframe,begin,end,source,rows,sha256,fetched_at FROM requests ORDER BY id')]
    counts = {str(tf): dict(zip(('rows', 'first_begin', 'last_end'), db.execute(
        'SELECT COUNT(*),MIN(begin),MAX(end) FROM candles WHERE timeframe=?', (tf,)).fetchone())) for tf in TIMEFRAMES}
    day_counts = [dict(zip(('utc_date','minute_rows'), row)) for row in db.execute(
        'SELECT substr(begin,1,10),COUNT(*) FROM candles WHERE timeframe=1 GROUP BY substr(begin,1,10)')]
    db.commit()
    db.close()
    staging.replace(target)
    quality = {'status': 'RESEARCH_DATA_NOT_CALENDAR_CERTIFIED', 'counts': counts,
               'minute_native_reconciliation': reconciliation,
               'daily_warmup_at_period_start': warmup_count, 'required_daily_warmup': 250,
               'day_counts': day_counts,
               'limitations': ['No historical exchange-calendar/halts validation: absence of bars is not proof of no trading.',
                              'Metadata is a current snapshot, not a historical tick/lot-size series.',
                              'Availability assumes native bar begin + timeframe; D1=24h, W1=7d (conservative).',
                              'USDRUBF prices are native; no synthetic roll/back-adjustment or funding P/L added.',
                              'Even M1 OHLC cannot always order multiple touches within one minute.']}
    write_json(destination / 'quality.json', quality)
    manifest = {'schema_version': 1, 'instrument': 'USDRUBF', 'instrument_metadata': instrument,
                'period_start': start, 'period_end_inclusive': end, 'history_start': history_start,
                'data_end': iso(right), 'timeframes': counts, 'requests': requests,
                'database_sha256': file_hash(target), 'quality_sha256': file_hash(destination / 'quality.json'),
                'created_at': iso(datetime.now(UTC)), 'quality_status': quality['status']}
    write_json(destination / 'manifest.json', manifest)
    return manifest


class Dataset:
    def __init__(self, path: Path, *, verify=True):
        self.path = path.resolve()
        self.manifest = json.loads((self.path / 'manifest.json').read_text())
        self.hash = file_hash(self.path / 'manifest.json')
        if verify and file_hash(self.path / 'candles.sqlite') != self.manifest['database_sha256']:
            raise ValueError('Dataset database hash mismatch')
        if verify and file_hash(self.path / 'quality.json') != self.manifest['quality_sha256']:
            raise ValueError('Dataset quality hash mismatch')
        self.db = sqlite3.connect((self.path / 'candles.sqlite').as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        quality = json.loads((self.path / 'quality.json').read_text())
        self.price_conflicts = [r for r in quality.get('minute_native_reconciliation', {}).get('conflicts', []) if r['price_conflict']]

    def conflicts_between(self, after, until):
        return [r for r in self.price_conflicts if timestamp(r['end']) > timestamp(after) and timestamp(r['begin']) < timestamp(until)]

    def close(self):
        self.db.close()

    def candles(self, tf, cutoff, *, after=None, limit=None):
        sql = 'SELECT * FROM candles WHERE timeframe=? AND end<=?'
        params = [tf, iso(cutoff)]
        if after is not None:
            sql += ' AND end>?'
            params.append(iso(after))
        sql += ' ORDER BY begin DESC'
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(limit)
        return [dict(row) for row in reversed(self.db.execute(sql, params).fetchall())]

    def execution(self, after, until):
        return self.candles(1, until, after=after)

    def entry_bar(self, start):
        row = self.db.execute('SELECT * FROM candles WHERE timeframe=15 AND begin=?', (iso(start),)).fetchone()
        return dict(row) if row else None

    def trading_dates(self):
        return [row[0] for row in self.db.execute(
            "SELECT DISTINCT date(begin,'+3 hours') FROM candles WHERE timeframe=1 ORDER BY 1")]
