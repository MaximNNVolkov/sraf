"""Loopback-only JSON backend; replay endpoints cannot trigger ingestion."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

from .backend import Replay, create_run
from .common import HOME, canonical
from .versioning import version_path


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, data):
        raw = canonical(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        replay = None
        try:
            request = {}
            if self.command == 'POST':
                if 'Content-Length' not in self.headers:
                    raise ValueError('Content-Length required')
                length = int(self.headers['Content-Length'])
                if not 0 <= length <= 1024 * 1024:
                    raise ValueError('Body exceeds 1 MiB')
                request = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(request,dict):
                    raise ValueError('JSON object required')
            if self.path == '/health' and self.command == 'GET':
                self._send(200,{'service':'retrotest','production_access':False})
                return
            if self.path == '/runs' and self.command == 'POST':
                dataset = request.get('dataset','USDRUBF_20220426_20260601')
                if not re.fullmatch(r'[A-Za-z0-9_-]+',dataset):
                    raise ValueError('Invalid dataset name')
                result = create_run(HOME / 'datasets' / dataset, request['origin'], request.get('version','v0.1001'), metadata=request.get('metadata'))
                self._send(201,result)
                return
            match = re.fullmatch(r'/runs/(v0\.\d{4})/([a-f0-9]{32})/(context|decision|advance|finish|results)',self.path)
            if not match:
                self._send(404,{'error':'Unknown route'})
                return
            version,run_id,action = match.groups()
            expected = 'GET' if action in {'context','results'} else 'POST'
            if self.command != expected:
                self._send(405,{'error':f'Use {expected}'})
                return
            path = HOME / 'results' / version / run_id
            if not path.is_dir():
                self._send(404,{'error':'Unknown run'})
                return
            replay = Replay(path)
            result = replay.submit(request) if action == 'decision' else getattr(replay,action)()
            self._send(200,result)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            self._send(400,{'error':str(exc)})
        finally:
            if replay:
                replay.close()

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *_):
        # Avoid logging full model requests or credentials.
        pass


def serve(port):
    print(f'Retrotest backend: http://127.0.0.1:{port}',flush=True)
    HTTPServer(('127.0.0.1',port),Handler).serve_forever()
