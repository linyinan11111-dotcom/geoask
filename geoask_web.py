"""Local HTML/CSS/JavaScript interface, backed by the existing query engine.

Only loopback connections are accepted. All mutations require a per-process
token and same-origin requests. Credentials stay in Python, never in HTML or
browser storage. One worker owns the engine and its database connection.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import asdict
import io
import json
import mimetypes
from pathlib import Path
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import webbrowser

import geoask as g
import geoask_credentials as credentials
from geoask_location import get_device_location
from geoask_parking import parking_route_points
from geoask_routing import plan_road_route
from geoask_service import QueryEngine, QueryRequest, route_points


def frontend_file(name):
    source = g.ROOT / 'web' / name
    return source if source.is_file() else g.resource_file('web', name)


def integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise g.GeoaskError(f'{label}应在 {low}–{high} 之间。')
    return value


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise g.GeoaskError(f'{label}应在 {low}–{high} 之间。')
    return float(value)


class WebApp:
    def __init__(self, config_path=g.DEFAULT_CONFIG):
        self.config_path = Path(config_path)
        self.settings = g.read_settings(self.config_path)
        self.llm = self.settings.llm.model_copy(deep=True)
        self.warning = ''
        self.api_key = self.amap_key = self.security_code = ''
        self.remember = False
        self.desktop = False
        try:
            profile = credentials.load_connection()
            if profile and self.config_path.resolve() == g.DEFAULT_CONFIG.resolve():
                self.llm = g.LLMSettings.model_validate(profile['llm'])
            self.api_key = g.resolve_api_key(self.llm)
            self.amap_key, self.security_code = g.resolve_amap_credentials(self.settings.amap)
            self.remember = profile is not None
        except (credentials.LocalSettingsError, g.GeoaskError, ValueError):
            self.warning = '请在接口设置中填写密钥，或清除无法读取的本地设置。'
        self.token = secrets.token_urlsafe(32)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='geoask-web')
        self.lock = threading.RLock()
        self.engine = None
        self.result = self.route = self.parking = self.route_parking = None
        self.result_id = ''
        self.jobs = {}
        self.active_job = None

    def config(self):
        return dict(version=g.__version__, desktop=self.desktop, center=self.settings.default_location,
                    radius=self.settings.default_radius, limit=self.settings.limit,
                    map=self.settings.map.model_dump(), llm=self.llm.model_dump(),
                    has_llm_key=bool(self.api_key), has_amap_key=bool(self.amap_key),
                    has_security_code=bool(self.security_code), remember=self.remember,
                    warning=self.warning, online=self.settings.amap.primary)

    def clear(self):
        self.result = self.route = self.parking = self.route_parking = None
        self.result_id = ''

    def state(self):
        result = None
        if self.result:
            r = self.result
            result = dict(id=self.result_id, query=r.request.query, center=r.request.center,
                          dry_run=r.request.dry_run, travel_mode=r.travel_mode, note=r.note,
                          stops=[dict(intent=s.intent.model_dump(), candidates=s.candidates,
                                      selected_index=s.selected_index, blocked_reason=s.blocked_reason)
                                 for s in r.stops])
        return dict(result=result, route=asdict(self.route) if self.route else None,
                    route_parking=self.route_parking,
                    parking=dict(candidates=self.parking.candidates, summary=self.parking.summary,
                                 radius=self.parking.radius) if self.parking else None)

    def require_result(self, data):
        if not self.result or data.get('result_id') != self.result_id:
            raise g.GeoaskError('行程已变化，请重新查询或刷新页面后再试。')

    def progress(self, text):
        with self.lock:
            if self.active_job:
                self.jobs[self.active_job]['message'] = text

    def submit(self, action, data):
        with self.lock:
            if self.active_job:
                raise g.GeoaskError('当前操作尚未完成，请稍候。')
            job_id = secrets.token_hex(12)
            self.jobs = {job_id: dict(status='running', message='正在处理…')}
            self.active_job = job_id
            self.pool.submit(self._work, job_id, action, data)
            return job_id

    def _work(self, job_id, action, data):
        try:
            extra = self.perform(action, data) or {}
            with self.lock:
                self.jobs[job_id] = dict(status='done', state=self.state(), **extra)
        except Exception as exc:
            safe = isinstance(exc, (g.GeoaskError, credentials.LocalSettingsError))
            if not safe:
                g.get_logger().error('Web operation failed (%s)', type(exc).__name__)
            with self.lock:
                self.jobs[job_id] = dict(status='error', error=str(exc) if safe else
                                        '操作未完成，请检查输入或稍后重试。', state=self.state())
        finally:
            with self.lock:
                self.active_job = None

    def perform(self, action, data):
        if self.engine is None:
            self.engine = QueryEngine()
        if action == 'query':
            query = data.get('query', '')
            center = data.get('center')
            if not isinstance(query, str) or not query.strip() or len(query) > 4000:
                raise g.GeoaskError('请输入 1–4000 个字符的出行需求。')
            if not isinstance(center, list) or len(center) != 2:
                raise g.GeoaskError('请设置有效的查询起点。')
            center = (number(center[0], '经度', -180, 180), number(center[1], '纬度', -90, 90))
            radius = number(data.get('radius', 1000), '查询半径', 1, 100000)
            limit = integer(data.get('limit', 10), '候选数量', 1, 100)
            self.clear()
            self.result = self.engine.run(QueryRequest(query=query, center=center, radius=radius,
                limit=limit, config_path=self.config_path, api_key=self.api_key, llm=self.llm,
                planning=True, dry_run=data.get('dry_run') is True,
                amap=self.settings.amap.primary, amap_key=self.amap_key,
                amap_security_code=self.security_code), self.progress)
            self.result_id = secrets.token_hex(12)
        elif action == 'select':
            self.require_result(data)
            stop = integer(data.get('stop'), '站点', 0, len(self.result.stops) - 1)
            index = integer(data.get('index'), '候选地点', 0, len(self.result.stops[stop].candidates) - 1)
            self.route = self.parking = self.route_parking = None
            self.result = self.engine.reselect(self.result, stop, index, self.progress)
            self.result_id = secrets.token_hex(12)
        elif action == 'parking':
            self.require_result(data)
            self.route = self.parking = self.route_parking = None
            self.parking = self.engine.find_parking(self.result,
                number(data.get('radius', 1000), '停车半径', 1, 5000), self.progress)
        elif action == 'route':
            self.require_result(data)
            points = route_points(self.result)
            mode = data.get('mode', self.result.travel_mode)
            if mode not in ('walking', 'cycling', 'driving'):
                raise g.GeoaskError('请选择步行、骑行或驾车。')
            parking = data.get('parking_index')
            chosen = None
            self.route = self.route_parking = None
            if parking is not None:
                if self.parking is None:
                    raise g.GeoaskError('请先查找并选择停车场。')
                integer(parking, '停车场', 0, len(self.parking.candidates) - 1)
                points = parking_route_points(self.parking, parking)
                chosen = self.parking.candidates[parking]
                mode = 'driving'
            self.progress('正在沿真实道路规划路线…')
            if self.settings.routing.provider == 'amap':
                from geoask_amap import plan_route
                route = plan_route(points, mode, self.settings.amap, self.amap_key, self.security_code)
            else:
                route = plan_road_route(points, mode, self.settings.routing)
            self.route, self.route_parking = route, chosen
        elif action == 'locate':
            location = get_device_location()
            self.clear()
            return dict(location=asdict(location))
        elif action == 'settings':
            return self.save_settings(data)
        elif action == 'clear-settings':
            credentials.clear_connection()
            self.api_key = self.amap_key = self.security_code = ''
            self.remember = False
            self.warning = ''
            self.clear()
            return dict(config=self.config())
        elif action == 'reset':
            self.clear()
        else:
            raise g.GeoaskError('不支持的操作。')

    def save_settings(self, data):
        raw_llm = data.get('llm')
        try:
            llm = g.LLMSettings.model_validate(raw_llm)
            parsed = urlsplit(llm.base_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('invalid endpoint')
        except (ValueError, TypeError):
            raise g.GeoaskError('请检查接口地址、模型名称与返回格式；接口地址中不要填写密钥。') from None
        # Compare the request layer's canonical host; never reuse a hidden key
        # when the endpoint changes, including Unicode/IDNA host changes.
        import httpx
        same = (httpx.URL(llm.base_url) == httpx.URL(self.llm.base_url)
                and llm.api_key_env == self.llm.api_key_env)
        def key_field(name, current):
            value = data.get(name)
            if value is None:
                return current
            if not isinstance(value, str) or len(value) > 4096:
                raise g.GeoaskError('密钥格式无效。')
            return value.strip()
        key = key_field('api_key', self.api_key if same else '')
        amap = key_field('amap_key', self.amap_key)
        code = key_field('security_code', self.security_code)
        remember = data.get('remember') is True
        if remember:
            credentials.save_connection(llm.model_dump(), self.settings.map.model_dump(),
                self.settings.routing.model_dump(), key, amap, code)
        else:
            credentials.clear_connection()
        self.llm, self.api_key, self.amap_key, self.security_code = llm, key, amap, code
        self.remember, self.warning = remember, ''
        self.clear()
        return dict(config=self.config())

    def csv(self, result_id):
        with self.lock:
            if self.active_job:
                raise g.GeoaskError('请等待当前操作完成后导出。')
            self.require_result(dict(result_id=result_id))
            rows = [s.selected for s in self.result.stops if s.selected]
            if not rows:
                raise g.GeoaskError('请先选定至少一个地点后导出。')
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(['名称', '类别', '距离（米）', 'WGS84 经度', 'WGS84 纬度'])
            for row in rows:
                cells = [str(row.get(k) or '') for k in ('name', 'category')]
                cells = ["'" + v if v.lstrip().startswith(('=', '+', '-', '@')) else v for v in cells]
                writer.writerow([*cells, round(row['dist_m'], 2), row['longitude'], row['latitude']])
            return output.getvalue().encode('utf-8-sig')

    def close(self):
        def cleanup():
            if self.engine:
                self.engine.close()
        self.pool.submit(cleanup).result(timeout=120)
        self.pool.shutdown(wait=True)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app, port=0):
        self.app = app
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.desktop_window = None


class Handler(BaseHTTPRequestHandler):
    server_version = 'geoask'

    def log_message(self, *args):
        pass  # Do not log user queries, tokens or URLs.

    def send(self, status, body, content_type='application/json; charset=utf-8', **headers):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: http: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in headers.items():
            self.send_header(key.replace('_', '-'), value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, api=False):
        if self.headers.get('Host') != urlsplit(self.server.origin).netloc:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return False
        return not api or secrets.compare_digest(self.headers.get('X-Geoask-Token', '').encode('utf-8'),
                                                self.server.app.token.encode('ascii'))

    def do_GET(self):
        parsed = urlsplit(self.path)
        api = parsed.path.startswith('/api/')
        if not self.allowed(api):
            return self.send(403, dict(error='请求来源无效，请从本地 geoask 窗口访问。'))
        app = self.server.app
        if parsed.path == '/api/config':
            return self.send(200, app.config())
        if parsed.path == '/api/state':
            with app.lock:
                # An engine operation may be mutating state; read only the last
                # complete snapshot in the job response while it is active.
                return self.send(200, dict(busy=bool(app.active_job), job=app.active_job,
                                          state=None if app.active_job else app.state()))
        if parsed.path.startswith('/api/jobs/'):
            with app.lock:
                job = app.jobs.get(parsed.path.rsplit('/', 1)[-1])
                return self.send(200 if job else 404, job or dict(error='操作记录已过期，请刷新。'))
        if parsed.path == '/api/export':
            try:
                content = app.csv(parse_qs(parsed.query).get('result_id', [''])[0])
                return self.send(200, content, 'text/csv; charset=utf-8',
                                 Content_Disposition='attachment; filename="geoask-trip.csv"')
            except g.GeoaskError as exc:
                return self.send(409, dict(error=str(exc)))
        files = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css',
                 '/glass.css': 'glass.css', '/glass.js': 'glass.js',
                 '/map.js': 'map.js', '/favicon.svg': 'favicon.svg'}
        for asset in ('tokens.js', 'internal/renderer.js', 'internal/optics.js',
                      'internal/context-budget.js', 'internal/jelly.js', 'internal/jelly-physics.js'):
            files['/vendor/lens/' + asset] = 'vendor/lens/' + asset
        name = files.get(parsed.path)
        if not name:
            return self.send(404, dict(error='页面不存在。'))
        content = frontend_file(name).read_bytes()
        if name == 'index.html':
            content = content.replace(b'__GEOASK_TOKEN__', app.token.encode('ascii'))
        return self.send(200, content, mimetypes.guess_type(name)[0] + '; charset=utf-8')

    def do_POST(self):
        if not self.allowed(api=True):
            return self.send(403, dict(error='请求来源无效，请刷新本地页面。'))
        path = urlsplit(self.path).path
        if path == '/api/shutdown':
            self.send(200, dict(stopped=True))
            def shutdown():
                self.server.shutdown()
                if self.server.desktop_window:
                    self.server.desktop_window.destroy()
            threading.Thread(target=shutdown, daemon=True).start()
            return
        if path not in {f'/api/{p}' for p in ('query', 'select', 'parking', 'route', 'locate', 'settings', 'clear-settings', 'reset')}:
            return self.send(404, dict(error='接口不存在。'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536 or self.headers.get_content_type() != 'application/json':
                raise ValueError('invalid body')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('invalid body')
        except (ValueError, UnicodeError, RecursionError):
            return self.send(400, dict(error='请求内容无效或过大。'))
        try:
            job_id = self.server.app.submit(path.removeprefix('/api/'), data)
            self.send(202, dict(job=job_id))
        except g.GeoaskError as exc:
            self.send(409, dict(error=str(exc)))


def run_desktop(server, on_loaded=None, *, hidden=False):
    """Host the HTML interface in an OS window, without opening a browser.

    The native event loop must own the main thread. Closing the last window
    returns from start(), then stops and joins the local HTTP service.
    """
    try:
        import webview
    except ImportError as exc:
        raise g.GeoaskError('缺少桌面窗口组件，请运行 python -m pip install -r requirements.txt -e .。') from exc
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    webview.settings['SHOW_DEFAULT_MENUS'] = False
    server.app.desktop = True
    window = webview.create_window('geoask · 自然语言出行规划', server.origin,
                                   width=1440, height=960, min_size=(960, 680),
                                   background_color='#faf9f5', hidden=hidden)
    server.desktop_window = window
    if on_loaded:
        window.events.loaded += lambda: on_loaded(window)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1},
                              name='geoask-http', daemon=True)
    thread.start()
    try:
        webview.start(gui='edgechromium' if sys.platform == 'win32' else None,
                      private_mode=True, debug=False)
    except Exception as exc:
        raise g.GeoaskError('桌面窗口无法启动。Windows 请确认已安装 Microsoft Edge WebView2 Runtime。') from exc
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.desktop_window = None


def main():
    parser = argparse.ArgumentParser(description='geoask · 自然语言地点查询与路径规划')
    parser.add_argument('--config', type=Path, default=g.DEFAULT_CONFIG)
    parser.add_argument('--port', type=int, default=0, help='本地端口，默认自动分配')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--no-browser', action='store_true', help='仅启动本地服务，不打开窗口')
    modes.add_argument('--browser', action='store_true', help='使用外部浏览器（默认使用独立桌面窗口）')
    args = parser.parse_args()
    app = WebApp(args.config)
    server = WebServer(app, args.port)
    try:
        if args.no_browser or args.browser:
            if args.browser:
                webbrowser.open(server.origin)
            if sys.stdout:
                print(f'geoask: {server.origin}\n按 Ctrl+C 停止本地服务。', flush=True)
            server.serve_forever(poll_interval=0.25)
        else:
            run_desktop(server)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
