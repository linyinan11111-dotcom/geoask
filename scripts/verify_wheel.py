"""Build/install smoke: run entry points outside the source checkout, no paid API.

Pass a prebuilt wheel. Dependencies are shared with this script's interpreter;
the application itself is installed normally into a fresh virtual environment.
CI installs dependencies afresh before invoking this script.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
from threading import Thread
import venv


def main():
    wheel = Path(sys.argv[1]).resolve()
    assert wheel.is_file()
    with tempfile.TemporaryDirectory(prefix="geoask-wheel-") as folder:
        directory = Path(folder)
        environment = directory / "venv"
        venv.create(environment, system_site_packages=True, with_pip=True)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        # A nested venv shares the base interpreter's packages, not its parent's.
        # Expose only dependency modules; the installed wheel takes precedence.
        site = environment / ("Lib/site-packages" if os.name == "nt" else
                              f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        (site / "geoask-smoke-dependencies.pth").write_text(sysconfig.get_paths()["purelib"], encoding="utf-8")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONUTF8"] = "1"
        def run(args, **kwargs):
            try:
                return subprocess.run([str(a) for a in args], cwd=directory, env=env,
                                      check=True, capture_output=True, text=True, encoding="utf-8", **kwargs)
            except subprocess.CalledProcessError as exc:
                print(exc.stdout, exc.stderr, file=sys.stderr)
                raise
        run([python, "-m", "pip", "install", "--no-index", "--no-deps", "--force-reinstall", wheel])
        probe = r'''
import json, os
from pathlib import Path
import geoask as g
import geoask_gui
import geoask_web
import geoask_credentials
geoask_credentials.connection_file = lambda: Path('smoke-connection.dat').resolve()
from importlib.metadata import metadata
from importlib.resources import files
package = metadata('geoask')
assert package['Version'] == g.__version__
assert package['Description-Content-Type'] == 'text/markdown' and package['License-Expression'] == 'MIT'
assert Path(g.__file__).is_relative_to(Path(__import__('sys').prefix))
assert g.DEFAULT_CONFIG.is_file() and g.location_script().is_file()
assert geoask_web.frontend_file('index.html').is_file()
assert geoask_web.frontend_file('app.js').is_file()
for asset in ('glass.js', 'glass.css', 'vendor/lens/LICENSE.txt', 'vendor/lens/tokens.js',
              'vendor/lens/internal/renderer.js', 'vendor/lens/internal/jelly.js',
              'vendor/lens/internal/jelly-physics.js', 'vendor/lens/internal/optics.js',
              'vendor/lens/internal/context-budget.js'):
    assert geoask_web.frontend_file(asset).is_file(), asset
import threading
from urllib.request import urlopen, Request
webapp = geoask_web.WebApp()
webserver = geoask_web.WebServer(webapp)
webthread = threading.Thread(target=webserver.serve_forever, daemon=True)
webthread.start()
try:
    with urlopen(webserver.origin) as response:
        html = response.read()
        assert b'__GEOASK_TOKEN__' not in html and b'/app.js' in html
    for asset in ('glass.js', 'glass.css', 'vendor/lens/tokens.js', 'vendor/lens/internal/renderer.js',
                  'vendor/lens/internal/jelly.js', 'vendor/lens/internal/jelly-physics.js',
                  'vendor/lens/internal/optics.js', 'vendor/lens/internal/context-budget.js'):
        with urlopen(webserver.origin + '/' + asset) as response:
            assert response.read() == geoask_web.frontend_file(asset).read_bytes(), asset
    request = Request(webserver.origin + '/api/config', headers={'X-Geoask-Token': webapp.token})
    with urlopen(request) as response:
        assert json.load(response)['version'] == g.__version__
finally:
    webserver.shutdown()
    webserver.server_close()
    webthread.join(timeout=5)
    webapp.close()
settings = g.read_settings(g.DEFAULT_CONFIG)
local_llm = settings.llm.model_copy(update={'api_key_env': 'GEOASK_WHEEL_SMOKE_ABSENT'})
os.environ.pop(local_llm.api_key_env, None)
geoask_credentials.save_connection(local_llm.model_dump(), settings.map.model_dump(), settings.routing.model_dump(), 'installed-profile-test-key')
assert g.resolve_api_key(local_llm) == 'installed-profile-test-key'
geoask_credentials.clear_connection()
assert not settings.cache_dir.is_relative_to(g.ROOT)
manifest = json.loads(files('geoask_resources').joinpath('data/manifest.json').read_text())
with g.load_data(settings) as con:
    assert con.execute('SELECT count(*) FROM poi').fetchone()[0] == manifest['rows']
config = json.loads(settings.model_dump_json())
config['llm']['base_url'] = __import__('sys').argv[1]
# Exercise bundled local resources without contacting the production Amap API.
config['amap']['primary'] = False
config['amap']['enabled'] = False
config['routing']['provider'] = 'valhalla'
Path('installed-config.yaml').write_text(__import__('yaml').safe_dump(config), encoding='utf-8')
if os.name == 'nt' or os.getenv('DISPLAY'):
    app = geoask_gui.GeoaskApp()
    app.withdraw()
    app.update()
    app.close()
print('installed resources, spatial data, GUI initialization: OK')
'''
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert request["response_format"] == {"type": "json_object"}
                response = {"id": "wheel-smoke", "object": "chat.completion", "created": 0,
                    "model": "test", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps({"intent": "参观", "modifiers": [],
                                                                "place_name": "国家体育场"}, ensure_ascii=False)}}]}
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        try:
            print(run([python, "-I", "-c", probe, f"http://127.0.0.1:{server.server_port}/v1"]).stdout.strip())
            cli = scripts / ("geoask.exe" if os.name == "nt" else "geoask")
            env["DEEPSEEK_API_KEY"] = "local-smoke-test"
            run([cli, "--help"])
            run([scripts / ("geoask-gui.exe" if os.name == "nt" else "geoask-gui"), "--help"])
            output = run([cli, "去国家体育场", "--config", directory / "installed-config.yaml", "--explain"])
            assert "国家体育场" in output.stdout and "结果 (1 条)" in output.stdout
            print("installed geoask entry point + local compatible HTTP API + named search: OK")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
