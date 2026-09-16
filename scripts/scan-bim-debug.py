#!/usr/bin/env python3
"""Local Scan-vs-BIM algorithm workbench. Reads a pinned input snapshot only.

Run via scripts/cloudbim-dev.sh scan-bim-start; no production reports are written.
"""
import argparse
import gzip
import hashlib
from importlib.metadata import version
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/mesh-service'))


def trusted_host(value, allowed=()):
    if not value or any(c in value for c in '/\\@'):
        return False
    try:
        parsed = urlsplit('//' + value)
        parsed.port
        return parsed.hostname in {'localhost', '127.0.0.1', '::1', *allowed}
    except ValueError:
        return False


def accepts_gzip(header):
    weights = {}
    for item in header.split(','):
        coding, *parameters = item.lower().split(';')
        weight = 1.0
        for parameter in parameters:
            key, _, value = parameter.partition('=')
            if key.strip() == 'q':
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0
        weights[coding.strip()] = weight if 0 <= weight <= 1 else 0
    return weights.get('gzip', weights.get('*', 0)) > 0


def compressed_result(file):
    """Persist a lossless transport variant of an immutable completed run."""
    packed = file.with_suffix('.json.gz')
    if packed.is_file() and not packed.is_symlink() and packed.stat().st_mtime_ns >= file.stat().st_mtime_ns:
        return packed
    temporary = file.with_name(f'result-{uuid4().hex}.gz.tmp')
    try:
        temporary.write_bytes(gzip.compress(file.read_bytes(), compresslevel=1, mtime=0))
        temporary.replace(packed)
    finally:
        temporary.unlink(missing_ok=True)
    return packed


def numerical_sources(sources):
    """Follow local imports, including package initializers and lazy imports.

    Unrelated denoise experiments must not expire this fixed-input comparison.
    Dynamic imports fall back to the conservative whole-tree identity.
    """
    import ast
    pending = [sources / 'rebar_workbench.py']
    found = set()

    def include(parts):
        for length in range(1, len(parts) + 1):
            path = sources.joinpath(*parts[:length])
            for candidate in (path.with_suffix('.py'), path / '__init__.py'):
                if candidate.is_file() and candidate not in found:
                    pending.append(candidate)

    while pending:
        path = pending.pop()
        if path in found:
            continue
        found.add(path)
        # This package initializer only populates the independent denoise
        # registry. The workbench calls comparison functions directly and never
        # runs that registry. Hash the initializer itself, but follow numerical
        # submodules through their explicit imports, not registry registrations.
        if path == sources / 'algorithms/__init__.py':
            continue
        package = list(path.relative_to(sources).parts[:-1])
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == '__import__'
                or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'
                or isinstance(node.func, ast.Name) and node.func.id == 'import_module'
            ):
                return {p for p in sources.rglob('*.py') if not p.name.startswith(('test_', 'benchmark_'))}
            if isinstance(node, ast.Import):
                for alias in node.names:
                    include(alias.name.split('.'))
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package) - node.level + 1] if node.level else []
                parts = prefix + (node.module.split('.') if node.module else [])
                include(parts)
                for alias in node.names:
                    include(parts + alias.name.split('.'))
    return found


def input_fingerprint(loaded):
    """Bind cached diagnostics to input bytes and numerical code, not a process."""
    digest = hashlib.sha256(b'scan-bim-cache-v3\0')
    digest.update(json.dumps(loaded, sort_keys=True, separators=(',', ':')).encode())
    digest.update(str(sys.version_info[:2]).encode())
    for package in ('numpy', 'scipy', 'trimesh', 'laspy', 'open3d'):
        digest.update(f'{package}:{version(package)}\0'.encode())
    mesh = Path(loaded['analysis_mesh_path'])
    sources = ROOT / 'services/mesh-service'
    files = {Path(loaded['scan_path']), Path(loaded['instance_map_path']), sources/'requirements.txt'}
    files.update(path for path in (mesh.rglob('*') if mesh.is_dir() else [mesh]) if path.is_file())
    files.update(numerical_sources(sources))
    for path in sorted(files):
        digest.update(f'{path}\0{path.stat().st_size}:'.encode())
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


class Workbench:
    def __init__(self, config, output):
        from rebar_workbench import load_inputs, catalog
        self.loaded = load_inputs(config)
        self.catalog = catalog(self.loaded)
        # Keep the existing API field name, but allow valid runs to survive a
        # restart. Input, transform, dependency or numerical-code changes expire it.
        self.catalog['sessionId'] = input_fingerprint(self.loaded)
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state = {'status': 'idle', 'latest': None, 'error': None, 'requestId': None}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def runs(self):
        records = []
        for path in sorted(self.output.glob('*/summary.json'), reverse=True):
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        records.sort(key=lambda row: row.get('createdAt', ''), reverse=True)
        # Retain the latest current-input result for every parameter combination
        # even when experiments push it outside the history's 100 recent runs.
        selected = records[:100]
        included = {row['runId'] for row in selected}
        seen = set()
        for row in records:
            if row.get('sessionId') != self.catalog['sessionId']:
                continue
            key = (row['ifcGlobalId'], tuple(sorted(row['parameters'].items())))
            if key in seen:
                continue
            seen.add(key)
            if row['runId'] not in included:
                selected.append(row)
        return selected

    def prepare_defaults(self):
        """Offline preparation; does not occupy the HTTP server's foreground job."""
        defaults = self.catalog['defaults']
        ready = {row['ifcGlobalId'] for row in self.runs()
                 if row.get('sessionId') == self.catalog['sessionId'] and row['parameters'] == defaults}
        total = len(self.catalog['bars'])
        for index, bar in enumerate(self.catalog['bars'], 1):
            if bar['ifcGlobalId'] not in ready:
                self.start(bar['ifcGlobalId'], defaults)
                while self.snapshot()['status'] == 'running':
                    time.sleep(.05)
                if self.snapshot()['status'] != 'complete':
                    raise RuntimeError(self.snapshot()['error'])
            if index % 10 == 0 or index == total:
                print(f'Prepared {index}/{total} bars', flush=True)
        if input_fingerprint(self.loaded) != self.catalog['sessionId']:
            raise RuntimeError('Inputs or numerical code changed during preparation; rerun preparation')

    def start(self, bar_id, parameters):
        ids = {bar['ifcGlobalId'] for bar in self.catalog['bars']}
        if not isinstance(bar_id, str) or bar_id not in ids:
            raise ValueError('请选择当前输入中的钢筋')
        if not isinstance(parameters, dict):
            raise ValueError('计算参数必须为对象')
        limits = {'normalMaxAngleDeg': (1, 90), 'maxSearchDistanceMm': (.1, 200),
                  'knnK': (1, 64), 'maxSamples': (4, 128), 'windowScale': (.25, 4), 'minArcCoverageDeg': (30, 300)}
        if set(parameters) - limits.keys():
            raise ValueError('包含不支持的计算参数')
        effective = {key: self.catalog['defaults'][key] for key in limits}
        effective.update(parameters)
        for key, (low, high) in limits.items():
            value = effective[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f'{key} 必须在 {low}–{high} 之间')
            if key in {'knnK', 'maxSamples'} and int(value) != value:
                raise ValueError(f'{key} 必须为整数')
        with self.lock:
            if self.state['status'] == 'running':
                return False
            request_id = uuid4().hex
            self.state.update(status='running', error=None, barId=bar_id, requestId=request_id)
        def work():
            try:
                from rebar_workbench import run_bar
                result = run_bar(self.loaded, bar_id, effective)
                run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
                directory = self.output / run_id
                directory.mkdir()
                result['runId'] = run_id
                result['sessionId'] = self.catalog['sessionId']
                record = {'runId': run_id, 'createdAt': datetime.now(timezone.utc).isoformat(),
                          'sessionId': self.catalog['sessionId'],
                          'requestId': request_id,
                          'ifcGlobalId': bar_id, 'name': result.get('name', bar_id),
                          'parameters': result['parameters'], 'stats': result['stats'],
                          'resultUrl': f'/runs/{run_id}/result.json'}
                # Publish only a complete, JSON-safe immutable run.
                temporary = directory / 'result.tmp'
                temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
                temporary.replace(directory / 'result.json')
                compressed_result(directory / 'result.json')
                (directory / 'summary.json').write_text(json.dumps(record, ensure_ascii=False, allow_nan=False))
                with self.lock:
                    self.state.update(status='complete', latest=record, error=None)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                with self.lock:
                    self.state.update(status='error', error=str(exc))
        threading.Thread(target=work, daemon=True).start()
        return request_id


def handler_for(state, allowed_hosts=()):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if args and str(args[0]).startswith('GET /api/status'):
                return
            super().log_message(fmt, *args)

        def respond(self, status, data):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)

        def trusted(self, mutation=False):
            host = self.headers.get('Host', '')
            if not trusted_host(host, allowed_hosts):
                self.respond(403, {'error': '不允许的访问主机'})
                return False
            origin = self.headers.get('Origin')
            if mutation and ((origin and origin != 'http://' + host) or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                self.respond(403, {'error': '不接受跨来源计算请求'})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            path = unquote(urlsplit(self.path).path)
            if path == '/api/catalog':
                return self.respond(200, state.catalog)
            if path == '/api/status':
                return self.respond(200, state.snapshot())
            if path == '/api/runs':
                return self.respond(200, state.runs())
            if path in ('/', '/index.html', '/viewer.js', '/style.css'):
                base = ROOT / 'scripts/scan-bim-debug'
                file = base / ('index.html' if path == '/' else path[1:])
            elif path in ('/vendor/three.module.js', '/vendor/three.core.js'):
                base = ROOT / 'node_modules/three/build'
                file = base / Path(path).name
            elif path.startswith('/vendor/addons/'):
                base = ROOT / 'node_modules/three/examples/jsm'
                file = base / path.removeprefix('/vendor/addons/')
            elif re.fullmatch(r'/runs/\d{8}T\d{6}-[a-f0-9]{8}/result\.json', path):
                base = state.output
                file = base / path.removeprefix('/runs/')
            else:
                return self.respond(404, {'error': '资源不存在'})
            file = file.resolve()
            if not file.is_relative_to(base.resolve()) or not file.is_file():
                return self.respond(404, {'error': '资源不存在'})
            content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            immutable = path.startswith('/runs/')
            compressed = immutable and accepts_gzip(self.headers.get('Accept-Encoding', ''))
            if compressed:
                file = compressed_result(file)
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Cache-Control', 'private, max-age=31536000, immutable' if immutable else 'no-store')
            if immutable:
                self.send_header('Vary', 'Accept-Encoding')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                with file.open('rb') as stream:
                    import shutil
                    shutil.copyfileobj(stream, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not self.trusted(mutation=True):
                return
            if self.path != '/api/run':
                return self.respond(404, {'error': '资源不存在'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('请提交小于 4 KB 的 JSON 参数')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or set(body) - {'ifcGlobalId', 'parameters'}:
                    raise ValueError('无效的计算请求')
                started = state.start(body.get('ifcGlobalId'), body.get('parameters', {}))
                return self.respond(202 if started else 409, {'status': 'running', 'requestId': started or None,
                                                             'error': None if started else '已有计算正在运行'})
            except (ValueError, TypeError) as exc:
                return self.respond(400, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.cloudbim/scan-bim-workbench/runs')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--allow-host', action='append', default=[])
    parser.add_argument('--prepare-defaults', action='store_true', help='Prepare the pinned snapshot without starting an HTTP service')
    args = parser.parse_args()
    state = Workbench(args.inputs.resolve(), args.output)
    if args.prepare_defaults:
        state.prepare_defaults()
        return
    server = ThreadingHTTPServer((args.host, args.port), handler_for(state, args.allow_host))
    print(f'Scan-vs-BIM workbench http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
