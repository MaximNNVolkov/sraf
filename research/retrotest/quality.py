"""Compare native M15 to minute archive without changing either source."""
from __future__ import annotations


def reconcile_minutes(db):
    db.row_factory = __import__('sqlite3').Row
    sql = '''
    SELECT c.begin,c.end,c.open,c.high,c.low,c.close,c.volume,
     COUNT(m.begin) AS minute_rows,MAX(m.high) AS minute_high,MIN(m.low) AS minute_low,
     SUM(m.volume) AS minute_volume,
     (SELECT open FROM candles x WHERE x.timeframe=1 AND x.begin>=c.begin AND x.begin<c.end ORDER BY x.begin LIMIT 1) AS minute_open,
     (SELECT close FROM candles x WHERE x.timeframe=1 AND x.begin>=c.begin AND x.begin<c.end ORDER BY x.begin DESC LIMIT 1) AS minute_close
    FROM candles c LEFT JOIN candles m
     ON m.timeframe=1 AND m.begin>=c.begin AND m.begin<c.end
    WHERE c.timeframe=15 GROUP BY c.begin
    '''
    conflicts = []
    count = 0
    for record in db.execute(sql):
        row = dict(record)
        count += 1
        price_conflict = not row['minute_rows'] or any(abs(row[k] - row['minute_' + k]) > 1e-7 for k in ('open','high','low','close'))
        volume_conflict = row['minute_volume'] is None or abs(row['volume'] - row['minute_volume']) > 1e-7
        if price_conflict or volume_conflict:
            row.update(price_conflict=price_conflict, volume_conflict=volume_conflict)
            conflicts.append(row)
    return {'compared_15m_bars':count, 'conflicts':conflicts,
            'price_conflict_count':sum(r['price_conflict'] for r in conflicts),
            'volume_conflict_count':sum(r['volume_conflict'] for r in conflicts),
            'policy':'Preserve sources. Price-conflict intervals cannot produce resolved trade outcomes.'}


def reconcile_and_refresh(db, client, left, right, progress=print):
    """Resolve archive revisions against the same exchange API as native bars."""
    from datetime import datetime, timedelta
    from .common import UTC, canonical, digest, iso, timestamp
    from .dataset import insert
    original = reconcile_minutes(db)
    days = sorted({row['begin'][:10] for row in original['conflicts']})
    refreshed = []
    for day in days:
        start = max(timestamp(day + 'T00:00:00+00:00'), timestamp(left))
        end = min(start.replace(hour=0,minute=0,second=0) + timedelta(days=1), timestamp(right))
        if start >= end:
            continue
        rows = [r for r in client.get_candles(1,start,end) if r.is_complete and start <= r.begin < end]
        if not rows:
            raise ValueError(f'Empty M1 exchange refresh for {day}')
        old = [dict(r) for r in db.execute('SELECT * FROM candles WHERE timeframe=1 AND begin>=? AND begin<? ORDER BY begin',(iso(start),iso(end)))]
        with db:
            db.execute('DELETE FROM candles WHERE timeframe=1 AND begin>=? AND begin<?',(iso(start),iso(end)))
            for c in rows:
                insert(db,1,c.begin,c.open,c.high,c.low,c.close,c.volume,'T-Invest GetCandles exchange revision')
            db.execute('INSERT INTO requests(timeframe,begin,end,source,rows,sha256,fetched_at) VALUES(?,?,?,?,?,?,?)',
                       (1,iso(start),iso(end),'T-Invest GetCandles exchange revision',len(rows),digest([r.to_dict() for r in rows]),iso(datetime.now(UTC))))
        refreshed.append({'utc_date':day,'archive_rows':len(old),'exchange_rows':len(rows),
                          'archive_sha256':digest(old),'exchange_sha256':digest([r.to_dict() for r in rows])})
        progress(f'M1 exchange reconciliation {day}: {len(rows)} rows',flush=True)
    result = reconcile_minutes(db)
    result['original_archive_price_conflicts'] = original['price_conflict_count']
    result['original_archive_volume_conflicts'] = original['volume_conflict_count']
    result['refreshed_days'] = refreshed
    result['source_policy'] = 'Native exchange GetCandles takes precedence over archive on conflicting UTC dates; original ZIP archives retained.'
    return result
