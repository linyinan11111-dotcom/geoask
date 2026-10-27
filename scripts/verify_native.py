"""Verify the real Windows WebView2 window without paid API calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geoask as g
import geoask_credentials as credentials
from geoask_web import WebApp, WebServer, run_desktop


def main():
    report = {'passed': False, 'renderer': 'Edge WebView2', 'external_browser': False}
    with tempfile.TemporaryDirectory(prefix='geoask-native-') as folder:
        credentials.connection_file = lambda: Path(folder) / 'connection.dat'
        g.resolve_api_key = lambda settings: ''
        g.resolve_amap_credentials = lambda settings: ('', '')
        app = WebApp()
        server = WebServer(app)
        port = server.server_port

        def loaded(window):
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    data = window.evaluate_js("({title:document.title, ready:!document.body.classList.contains('is-busy'), desktop:document.querySelector('#service-badge').textContent, markers:document.querySelectorAll('.origin-marker').length})")
                    if data and data['ready'] and data['markers'] == 1:
                        break
                    time.sleep(.1)
                assert data['title'].startswith('geoask') and data['ready'] and data['markers'] == 1, data
                assert data['desktop'] == '桌面应用已就绪', data
                window.evaluate_js("document.querySelector('#settings-button').click()")
                assert window.evaluate_js("document.querySelector('#settings-dialog').open")
                assert window.evaluate_js("document.querySelector('.shutdown-button').textContent") == '退出应用'
                report.update(html_loaded=True, native_window=True, settings_dialog=True, origin_marker=True)
            except Exception as exc:
                report['error'] = str(exc)
            finally:
                window.destroy()

        try:
            run_desktop(server, loaded, hidden=True)
        finally:
            server.server_close()
            app.close()
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.3):
                raise AssertionError('server remained open after the window closed')
        except OSError:
            report['server_stopped_on_close'] = True
        report['passed'] = bool(report.get('html_loaded') and report.get('server_stopped_on_close') and not report.get('error'))
        report['verified_at'] = datetime.now(timezone.utc).isoformat()
        destination = ROOT / '.cache/native-verification.json'
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
