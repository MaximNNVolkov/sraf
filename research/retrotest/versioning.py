from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .common import ROOT, VERSIONS, digest, file_hash, write_json


def version_path(version):
    if not re.fullmatch(r'v0\.\d{4}', version):
        raise ValueError('Version must have form v0.1001')
    path = VERSIONS / version
    if not path.is_dir():
        raise ValueError('Unknown model version')
    return path


def model_snapshot(version):
    path = version_path(version)
    files = {p.name: p.read_text() for p in sorted(path.iterdir()) if p.is_file()}
    config = json.loads(files['config.json'])
    if config['version'] != version:
        raise ValueError('Model version/config mismatch')
    fixed = {'entry_lag_minutes':15, 'tp1_fraction':0.5, 'stop_after_tp1':'entry',
             'execution_timeframe_minutes':1, 'commission':None, 'slippage':None, 'funding':None}
    if any(config.get(k) != v for k,v in fixed.items()):
        raise ValueError('This engine supports fixed 15m lag, 50% TP1, BE stop and gross-only results; execution changes require a new implementation')
    return {'version': version, 'config': config, 'files': files, 'sha256': digest(files)}


def code_snapshot():
    paths = sorted((ROOT / 'research' / 'retrotest').glob('*.py'))
    paths += [ROOT / 'usdrubf' / name for name in ('analysis.py','process.py','moex.py','trading.py')]
    hashes = {str(p.relative_to(ROOT)): file_hash(p) for p in paths}
    revision = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    return {'revision': revision, 'files': hashes, 'sha256': digest(hashes)}


def bump(version):
    old = version_path(version)
    number = int(version.split('.')[1]) + 1
    if number > 9999:
        raise ValueError('Version range exhausted')
    new_version = f'v0.{number:04d}'
    new = VERSIONS / new_version
    if new.exists():
        raise ValueError('Next version already exists; never overwrite model versions')
    shutil.copytree(old, new)
    config = json.loads((new / 'config.json').read_text())
    config.update(version=new_version, parent=version, parent_sha256=model_snapshot(version)['sha256'])
    write_json(new / 'config.json', config)
    replay = new / 'replay.md'
    if replay.exists():
        replay.write_text(replay.read_text().replace(version, new_version))
    return new_version
