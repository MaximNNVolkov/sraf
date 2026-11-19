"""python -m research.retrotest: ingestion, versioning, replay CLI and local HTTP."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import HOME, canonical

DEFAULT_DATASET = HOME / 'datasets' / 'USDRUBF_20220426_20260601'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    dl = sub.add_parser('download', help='Read-only T-Invest download; writes only research dataset')
    dl.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    dl.add_argument('--start', default='2023-01-01')
    dl.add_argument('--end', default='2026-06-01')
    dl.add_argument('--history-start', default='2022-04-26')
    check = sub.add_parser('verify')
    check.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    sample = sub.add_parser('sample')
    sample.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    sample.add_argument('--version', default='v0.1001')
    sample.add_argument('--seed', required=True, type=int)
    sample.add_argument('--audit-seed', default=1001, type=int)
    sample.add_argument('--count', default=100, type=int)
    new = sub.add_parser('create')
    new.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    new.add_argument('--origin', required=True, help='YYYY-MM-DD, at 08:00 MSK')
    new.add_argument('--version', default='v0.1001')
    for name in ('context','advance','finish','results'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--run', required=True, type=Path)
    submit = sub.add_parser('submit')
    submit.add_argument('--run', required=True, type=Path)
    submit.add_argument('--file', default='-', help='Decision JSON or - for stdin')
    bump = sub.add_parser('bump')
    bump.add_argument('--from-version', default='v0.1001')
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == 'download':
            from .dataset import build_dataset
            result = build_dataset(args.dataset,start=args.start,end=args.end,history_start=args.history_start,
                                   progress=lambda *a,**kw: print(*a,file=sys.stderr,**kw))
        elif args.command == 'verify':
            from .dataset import Dataset
            ds = Dataset(args.dataset)
            result = {'status':'HASHES_VERIFIED','manifest_sha256':ds.hash,'timeframes':ds.manifest['timeframes'],
                      'quality':json.loads((ds.path / 'quality.json').read_text())}
            ds.close()
        elif args.command == 'sample':
            from .sampling import sample_origins
            result = sample_origins(args.dataset,args.version,args.seed,args.count,args.audit_seed)
        elif args.command == 'create':
            from .backend import create_run
            result = create_run(args.dataset,args.origin,args.version)
        elif args.command == 'bump':
            from .versioning import bump
            result = {'version':bump(args.from_version)}
        elif args.command == 'serve':
            from .server import serve
            serve(args.port)
            return 0
        else:
            from .backend import Replay
            replay = Replay(args.run)
            try:
                if args.command == 'submit':
                    raw = sys.stdin.read(1024 * 1024 + 1) if args.file == '-' else Path(args.file).read_text()
                    if len(raw) > 1024 * 1024:
                        raise ValueError('Decision exceeds 1 MiB')
                    result = replay.submit(json.loads(raw))
                else:
                    result = getattr(replay,args.command)()
            finally:
                replay.close()
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
