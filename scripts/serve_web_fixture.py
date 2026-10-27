"""Isolated browser-test server. All LLM/POI/routing responses are test fixtures.

Never use this launcher for real trips; run geoask_web.py instead.
"""
import argparse
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import geoask as g
import geoask_amap as amap
import geoask_credentials as credentials
from geoask_routing import RoadRoute, RouteLeg
from geoask_web import WebApp, WebServer


def serve(port):
    with tempfile.TemporaryDirectory(prefix='geoask-web-fixture-') as directory:
        credentials.connection_file = lambda: Path(directory) / 'connection.dat'
        g.resolve_api_key = lambda settings: 'web-test-llm'
        g.resolve_amap_credentials = lambda settings: ('web-test-map', '')
        g.OpenAI = lambda **kwargs: nullcontext(object())

        def parse(query, *args):
            if '错误' in query:
                raise g.GeoaskError('测试请求失败，请调整需求后重试。')
            intents = ['咖啡', '健身', '餐厅'] if '晚饭' in query else ['咖啡', '健身']
            if '鸟巢' in query:
                stops = [g.GeoIntent(intent='参观', modifiers=[], place_name='鸟巢'),
                         g.GeoIntent(intent='餐厅', modifiers=[])]
            else:
                stops = [g.GeoIntent(intent=i, modifiers=[]) for i in intents]
            return g.GeoPlan(stops=stops, travel_mode='cycling' if '骑' in query else 'walking')

        def search(query, center, radius, limit, *args, **kwargs):
            names = {'咖啡': ['测试咖啡馆 A', '测试咖啡馆 B', '<script>测试名称</script>'],
                     '健身': ['测试运动空间 A', '测试运动空间 B'],
                     '餐厅': ['测试餐厅 A', '测试餐厅 B'],
                     '鸟巢': ['国家体育场（测试候选）', '鸟巢（测试候选二）'],
                     '停车场': ['测试停车场 A', '测试停车场 B']}[query]
            return [dict(name=n,category=query,longitude=center[0]+.002*(i+1),
                         latitude=center[1]+.001*(i+1),dist_m=220*(i+1),name_match_count=len(names),
                         source='浏览器测试数据') for i,n in enumerate(names)]

        def route(points, mode, *args):
            line=[]
            for a,b in zip(points, points[1:]):
                line.extend([a,(a[0],b[1]),b])
            return RoadRoute(line,[RouteLeg(450,300,['沿测试道路向北行进', '右转，抵达测试地点']) for _ in points[1:]],
                             450*(len(points)-1),300*(len(points)-1),mode,provider='浏览器测试数据')
        g.parse_plan=parse
        amap.search_pois=search
        amap.plan_route=route
        app=WebApp()
        server=WebServer(app,port)
        print(server.origin,flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            app.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    serve(parser.parse_args().port)
