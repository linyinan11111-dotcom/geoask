import json
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import geoask as g
from geoask_amap import search_places


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_amap_poi_response_is_normalized_and_key_is_only_in_request():
    seen = {}

    def opener(request, timeout):
        seen.update(parse_qs(urlsplit(request.full_url).query))
        return Response(json.dumps({
            "status": "1", "count": "1", "pois": [{
                "name": "阳光洗浴中心", "type": "生活服务;洗浴推拿场所",
                "location": "116.481488,39.990464", "distance": "230"
            }]
        }).encode())

    rows = search_places("洗浴中心", (116.46, 39.92), 1000, 10,
                         g.AmapSettings(enabled=True), "test-amap-key", opener)
    assert rows and rows[0]["name"] == "阳光洗浴中心"
    assert rows[0]["source"] == "高德地图"
    assert rows[0]["name_match_count"] == 1
    assert seen["key"] == ["test-amap-key"]
    assert seen["keywords"] == ["洗浴中心"]


def test_amap_security_code_is_sent_as_jscode():
    seen = {}

    def opener(request, timeout):
        seen.update(parse_qs(urlsplit(request.full_url).query))
        return Response(json.dumps({"status": "1", "count": "0", "pois": []}).encode())

    search_places("洗浴中心", (116.46, 39.92), 1000, 10,
                  g.AmapSettings(enabled=True), "key", opener, "security-code")
    assert seen["jscode"] == ["security-code"]


def test_amap_disabled_does_not_make_request():
    called = False

    def opener(*args, **kwargs):
        nonlocal called
        called = True

    assert search_places("洗浴中心", (116.46, 39.92), 1000, 10,
                         g.AmapSettings(), "key", opener) == []
    assert not called
