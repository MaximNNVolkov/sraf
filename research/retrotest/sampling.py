"""Uniform origins sampled before outcomes; no filtering on future returns."""
from __future__ import annotations

import platform
import random

from .common import HOME, digest, iso, write_json
from .dataset import Dataset
from .versioning import model_snapshot


def sample_origins(dataset_path, version, seed, count=100, audit_seed=1001):
    if count <= 0:
        raise ValueError('Count must be positive')
    model = model_snapshot(version)
    config = model['config']
    ds = Dataset(dataset_path)
    try:
        eligible, excluded = [], []
        for day in ds.trading_dates():
            if not max(config['period_start'],ds.manifest['period_start']) <= day <= min(config['period_end_inclusive'],ds.manifest['period_end_inclusive']):
                continue
            cutoff = iso(day + 'T08:00:00+03:00')
            n = ds.db.execute('SELECT COUNT(*) FROM candles WHERE timeframe=1440 AND end<=?',(cutoff,)).fetchone()[0]
            if n < config['min_daily_warmup']:
                excluded.append({'origin':day,'reason':'INSUFFICIENT_D1_WARMUP','daily_bars':n})
            elif any(not ds.candles(tf,cutoff,limit=1) for tf in (60,240,1440,10080)):
                excluded.append({'origin':day,'reason':'MISSING_TIMEFRAME'})
            else:
                eligible.append(day)
        if count > len(eligible):
            raise ValueError(f'Only {len(eligible)} eligible origins, requested {count}')
        selected = random.Random(seed).sample(eligible,count)
        audit = random.Random(audit_seed).sample(selected,min(10,count))
        result = {'version':version,'model_sha256':model['sha256'],'dataset_sha256':ds.hash,
                  'master_seed':seed,'audit_seed':audit_seed,'python':platform.python_version(),
                  'algorithm':'Python random.Random(MT19937).sample without replacement',
                  'eligible_population':eligible,'population_sha256':digest(eligible),
                  'eligible_count':len(eligible),'excluded':excluded,'origins':selected,'audit_origins':audit,
                  'future_outcomes_used_for_sampling':False}
        dest = HOME / 'results' / version / 'samples'
        dest.mkdir(parents=True,exist_ok=True)
        path = dest / (digest(result) + '.json')
        if path.exists() and path.read_text():
            return {'path':str(path),**result}
        write_json(path,result)
        return {'path':str(path),**result}
    finally:
        ds.close()
