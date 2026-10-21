"""Exercise the real SDK against a local OpenAI-compatible HTTP endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from openai import OpenAI

from geoask import LLMSettings, parse_intent, parse_plan


def test_deepseek_compatible_http_interface():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, body))
            content = '{"intent":"咖啡","modifiers":[]}'
            if 'stops' in body['messages'][0]['content']:
                content = '{"stops":[{"intent":"咖啡","modifiers":[]},{"intent":"健身","modifiers":[]}],"travel_mode":"walking"}'
            if '鸟巢' in body['messages'][-1]['content']:
                stop = {"intent":"演唱会", "modifiers":[], "place_name":"鸟巢"}
                content = json.dumps({"stops":[stop],"travel_mode":"walking"}
                                     if 'stops' in body['messages'][0]['content'] else stop)
            if '小蛮腰' in body['messages'][-1]['content']:
                stop = {"intent":"观光", "modifiers":[], "place_name":"小蛮腰", "place_candidates":["广州塔"]}
                content = json.dumps({"stops":[stop],"travel_mode":"walking"}
                                     if 'stops' in body['messages'][0]['content'] else stop)
            response = json.dumps({
                'id': 'local-test', 'object': 'chat.completion', 'created': 0, 'model': 'deepseek-chat',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                    'role': 'assistant', 'content': content}}],
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with OpenAI(api_key='local-test-key', base_url=f'http://127.0.0.1:{server.server_port}/v1',
                    max_retries=0, timeout=5) as client:
            result = parse_intent('我想喝咖啡', LLMSettings(), client)
            trip = parse_plan('先喝咖啡然后健身', LLMSettings(), client)
            bird = parse_plan('我想去鸟巢看演唱会', LLMSettings(), client)
            bird_single = parse_intent('我想去鸟巢看演唱会', LLMSettings(), client)
            guangzhou = parse_plan('去小蛮腰观光', LLMSettings(), client)
            guangzhou_single = parse_intent('去小蛮腰观光', LLMSettings(), client)
        assert [stop.intent for stop in trip.stops] == ['咖啡', '健身']
        assert result.intent == '咖啡'
        assert len(bird.stops) == 1 and bird.stops[0].place_name == '鸟巢'
        assert bird_single.place_name == '鸟巢'
        assert guangzhou.stops[0].place_name == '小蛮腰' and guangzhou.stops[0].place_candidates == ['广州塔']
        assert guangzhou_single.place_name == '小蛮腰' and guangzhou_single.place_candidates == ['广州塔']
        path, body = received[0]
        assert path == '/v1/chat/completions'
        assert body['model'] == 'deepseek-chat'
        assert body['response_format'] == {'type': 'json_object'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
