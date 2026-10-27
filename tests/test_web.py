"""Real loopback HTTP and worker tests; providers are isolated, no paid APIs."""
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPConnection
import json
import threading
import time

import pytest

import geoask as g
import geoask_amap as amap
import geoask_credentials as credentials
from geoask_routing import RoadRoute, RouteLeg
from geoask_web import WebApp, WebServer, frontend_file


@pytest.fixture
def app(monkeypatch):
    settings = g.Settings(amap=g.AmapSettings(enabled=True, primary=True),
                          routing=g.RoutingSettings(provider='amap'))
    monkeypatch.setattr(g, 'read_settings', lambda path: settings.model_copy(deep=True))
    monkeypatch.setattr(g, 'resolve_api_key', lambda settings: 'private-llm-canary')
    monkeypatch.setattr(g, 'resolve_amap_credentials', lambda settings: ('private-map-canary', 'private-code-canary'))
    monkeypatch.setattr(g, 'parse_plan', lambda *args: g.GeoPlan(stops=[
        g.GeoIntent(intent='咖啡', modifiers=[]), g.GeoIntent(intent='健身', modifiers=[])], travel_mode='cycling'))
    monkeypatch.setattr(g, 'OpenAI', lambda **kwargs: __import__('contextlib').nullcontext(object()))

    def search(query, center, radius, limit, settings, key, code='', **kwargs):
        assert key == 'private-map-canary'
        return [dict(name=f'{query} {i}', category=query, longitude=center[0]+(i+1)*.001,
                     latitude=center[1], dist_m=(i+1)*100, name_match_count=2) for i in range(2)]
    monkeypatch.setattr(amap, 'search_pois', search)
    monkeypatch.setattr(amap, 'plan_route', lambda points, mode, *args: RoadRoute(
        points, [RouteLeg(300, 180, ['沿测试道路前进']) for _ in points[1:]],
        300*(len(points)-1), 180*(len(points)-1), mode, provider='test provider'))
    instance = WebApp()
    yield instance
    instance.close()


def run(app, action, data):
    job = app.submit(action, data)
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        with app.lock:
            response = app.jobs[job]
            if response['status'] != 'running':
                return response
        time.sleep(.01)
    pytest.fail('web worker timed out')


def query(app):
    response = run(app, 'query', dict(query='骑车先喝咖啡，再健身', center=[116.46, 39.92], radius=1000))
    assert response['status'] == 'done', response
    return response['state']['result']['id']


@contextmanager
def serving(app):
    server = WebServer(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, path, method='GET', data=None, headers=None):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    values = {'X-Geoask-Token': server.app.token, 'Content-Type': 'application/json'}
    values.update(headers or {})
    connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=values)
    response = connection.getresponse()
    content = response.read()
    status = response.status
    connection.close()
    return status, content


def test_http_static_token_origin_and_traversal(app):
    with serving(app) as server:
        status, html = request(server, '/')
        assert status == 200 and app.token.encode() in html and b'__GEOASK_TOKEN__' not in html
        assert request(server, '/app.js')[0] == 200
        assert request(server, '/../../config.yaml')[0] == 404
        assert request(server, '/api/config', headers={'X-Geoask-Token': ''})[0] == 403
        assert request(server, '/api/config', headers={'X-Geoask-Token': 'é'})[0] == 403
        assert request(server, '/api/config', headers={'Host': 'evil.example'})[0] == 403
        assert request(server, '/api/query', 'POST', {}, {'Origin': 'https://evil.example'})[0] == 403
        assert request(server, '/api/query', 'POST', [], {})[0] == 400
        status, content = request(server, '/api/config')
        assert status == 200 and json.loads(content)['has_amap_key']
        assert b'private-' not in content


def test_web_multistop_select_route_parking_and_no_secret_serialization(app):
    result_id = query(app)
    response = run(app, 'route', dict(result_id=result_id, mode='cycling'))
    assert response['status'] == 'done'
    assert response['state']['route']['travel_mode'] == 'cycling'
    first = app.result.stops[0].selected
    changed = run(app, 'select', dict(result_id=result_id, stop=0, index=1))
    assert changed['status'] == 'done' and changed['state']['route'] is None
    assert app.result.stops[0].selected != first
    assert app.result.stops[1].center[0] == app.result.stops[0].selected['longitude']
    assert run(app, 'route', dict(result_id=result_id, mode='walking'))['status'] == 'error'
    result_id = app.result_id
    parking = run(app, 'parking', dict(result_id=result_id, radius=500))
    assert len(parking['state']['parking']['candidates']) == 2
    route = run(app, 'route', dict(result_id=result_id, mode='walking', parking_index=1))
    assert route['state']['route']['travel_mode'] == 'driving'
    assert route['state']['route_parking']['name'] == '停车场 1'
    assert not any(key in json.dumps(route) for key in ['private-map-canary', 'private-llm-canary', 'private-code-canary'])


def test_route_failure_preserves_selected_places_and_clears_old_geometry(app, monkeypatch):
    result_id = query(app)
    assert run(app, 'route', dict(result_id=result_id, mode='walking'))['status'] == 'done'
    def fail(*args):
        raise g.GeoaskError('没有可行路线')
    monkeypatch.setattr(amap, 'plan_route', fail)
    response = run(app, 'route', dict(result_id=result_id, mode='cycling'))
    assert response['status'] == 'error' and response['state']['route'] is None
    assert all(stop['selected_index'] == 0 for stop in response['state']['result']['stops'])


def test_web_rejects_concurrent_mutation(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = app.perform
    def blocked(action, data):
        entered.set()
        release.wait(5)
        return original(action, data)
    monkeypatch.setattr(app, 'perform', blocked)
    app.submit('reset', {})
    assert entered.wait(2)
    try:
        with pytest.raises(g.GeoaskError, match='尚未完成'):
            app.submit('reset', {})
    finally:
        release.set()


@pytest.mark.parametrize('endpoint', ['https://another.example/v1', 'https://straße.de/v1'])
def test_changed_endpoint_does_not_reuse_hidden_key(app, endpoint):
    llm = app.llm.model_copy(update={'base_url': endpoint}).model_dump()
    response = run(app, 'settings', dict(llm=llm, remember=False))
    assert response['status'] == 'done'
    assert app.api_key == '' and not response['config']['has_llm_key']
    assert app.amap_key == 'private-map-canary'


def test_settings_keep_unchanged_hidden_keys_and_do_not_expose_them(app):
    response = run(app, 'settings', dict(llm=app.llm.model_dump(), remember=True))
    assert response['status'] == 'done' and response['config']['remember']
    assert app.api_key == 'private-llm-canary'
    assert credentials.load_connection()['api_key'] == app.api_key
    assert 'private-llm-canary' not in json.dumps(response)


def test_invalid_coordinate_and_preview_do_not_search(app, monkeypatch):
    assert run(app, 'query', dict(query='咖啡', center=[float('nan'), 39]))['status'] == 'error'
    monkeypatch.setattr(amap, 'search_pois', lambda *args, **kw: pytest.fail('preview executed POI request'))
    response = run(app, 'query', dict(query='咖啡', center=[116.46,39.92], dry_run=True))
    assert response['status'] == 'done' and response['state']['result']['dry_run']
    assert run(app, 'route', dict(result_id=app.result_id, mode='walking'))['status'] == 'error'


def test_csv_formula_escape_and_invalidated_result(app):
    result_id = query(app)
    app.result.stops[0].selected['name'] = '=HYPERLINK("unsafe")'
    csv = app.csv(result_id).decode('utf-8-sig')
    assert "'=HYPERLINK" in csv and 'WGS84' in csv
    run(app, 'reset', {})
    with pytest.raises(g.GeoaskError):
        app.csv(result_id)


def test_frontend_assets_and_default_online_configuration():
    for name in ('index.html', 'app.js', 'map.js', 'style.css', 'favicon.svg'):
        assert frontend_file(name).is_file()
    # Read the YAML directly: the legacy test fixture intentionally changes read_settings.
    import yaml
    config = yaml.safe_load(g.DEFAULT_CONFIG.read_text(encoding='utf-8'))
    assert config['amap']['primary'] and config['routing']['provider'] == 'amap'


def test_native_shell_uses_desktop_window_and_stops_server(app, monkeypatch):
    """The real OS renderer is separately exercised by verify_native.py."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import sys
    import webbrowser
    from geoask_web import run_desktop
    window = MagicMock()
    fake = SimpleNamespace(settings={}, create_window=MagicMock(return_value=window))
    server = WebServer(app)
    def start(**kwargs):
        assert request(server, '/api/config')[0] == 200
        assert json.loads(request(server, '/api/config')[1])['desktop']
        assert kwargs['private_mode'] and not kwargs['debug']
    fake.start = start
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.setattr(webbrowser, 'open', lambda *args: pytest.fail('opened external browser'))
    try:
        run_desktop(server)
        assert fake.create_window.call_args.kwargs['min_size'] == (960, 680)
        assert fake.settings['ALLOW_DOWNLOADS'] and not fake.settings['ALLOW_FILE_URLS']
        assert server.desktop_window is None
    finally:
        server.server_close()
