"""Strict Amap provider tests: no real network, no local OSM."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from click.testing import CliRunner

import geoask as g
import geoask_amap as amap
from geoask_service import QueryEngine, QueryRequest


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self, limit=None):
        return self.payload if limit is None else self.payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def strict_settings():
    return g.Settings(amap=g.AmapSettings(enabled=True, primary=True),
                      routing=g.RoutingSettings(provider="amap"))


def test_amap_search_converts_center_and_results_to_wgs84(monkeypatch):
    calls = []

    def opener(request, timeout):
        calls.append(urlsplit(request.full_url))
        return FakeResponse({"status": "1", "info": "OK", "infocode": "10000", "count": "2",
                             "pois": [{"name": "测试洗浴", "type": "生活服务",
                                       "location": "116.46615055,39.92133303"}]})

    settings = strict_settings()
    rows = amap.search_pois("洗浴中心", (116.46, 39.92), 1000, 10, settings.amap,
                            "test-key", "test-code", strict=True, opener=opener)
    query = parse_qs(calls[0].query)
    assert query["keywords"] == ["洗浴中心"]
    assert query["jscode"] == ["test-code"]
    assert query["location"] != ["116.46000000,39.92000000"]
    assert rows[0]["name"] == "测试洗浴"
    assert rows[0]["source"] == "高德地图"
    assert rows[0]["name_match_count"] == 2
    assert rows[0]["longitude"] != 116.46615055


def test_amap_route_parses_driving_steps_and_polyline(monkeypatch):
    calls = []

    def opener(request, timeout):
        calls.append(urlsplit(request.full_url))
        return FakeResponse({"status": "1", "info": "OK", "infocode": "10000",
                             "route": {"paths": [{"distance": "1200", "duration": "600",
                                                  "steps": [{"instruction": "向东步行",
                                                             "polyline": "116.46615055,39.92133303;"
                                                                         "116.46715055,39.92133303"}]}]}})

    settings = strict_settings()
    route = amap.plan_route([(116.46, 39.92), (116.47, 39.92)], "walking",
                            settings.amap, "test-key", "test-code", opener=opener)
    assert calls and "/direction/walking" in calls[0].path
    assert route.provider == "高德地图"
    assert route.travel_mode == "walking"
    assert route.distance_m == 1200 and route.duration_s == 600
    assert route.legs[0].instructions == ["向东步行"]
    assert len(route.coordinates) == 2
    assert route.coordinates[0][0] != 116.46615055


def test_amap_engine_is_strict_without_key(monkeypatch):
    settings = strict_settings()
    monkeypatch.setattr(g, "read_settings", lambda path: settings)
    monkeypatch.setattr(g, "resolve_amap_credentials", lambda amap_settings: ("", ""))
    called = False

    def parse(*args, **kwargs):
        nonlocal called
        called = True
        return g.GeoIntent(intent="洗浴中心", modifiers=[])

    monkeypatch.setattr(g, "parse_intent", parse)
    engine = QueryEngine()
    try:
        with pytest.raises(g.GeoaskError, match="高德"):
            engine.run(QueryRequest("洗浴中心", (116.46, 39.92), 1000, 10,
                                    Path("config.yaml"), amap=True))
        assert not called
    finally:
        engine.close()


def test_amap_engine_uses_online_pois_and_auto_selects_category(monkeypatch):
    settings = strict_settings()
    monkeypatch.setattr(g, "read_settings", lambda path: settings)
    monkeypatch.setattr(g, "resolve_amap_credentials", lambda amap_settings: ("key", "code"))
    monkeypatch.setattr(g, "parse_intent",
                        lambda *args, **kwargs: g.GeoIntent(intent="洗浴中心", modifiers=[]))

    def fake_search(query, center, radius, limit, settings, key, code, **kwargs):
        return [{"name": "最近洗浴中心", "category": "生活服务", "name_rank": 0,
                 "name_match_count": 1, "longitude": 116.46, "latitude": 39.92,
                 "dist_m": 20.0, "source": "高德地图"}]

    monkeypatch.setattr(amap, "search_pois", fake_search)
    engine = QueryEngine()
    try:
        result = engine.run(QueryRequest("洗浴中心", (116.46, 39.92), 1000, 10,
                                         Path("config.yaml"), amap=True))
        assert result.request.amap
        assert result.stops[0].selected_index == 0
        assert result.rows[0]["name"] == "最近洗浴中心"
        assert "高德地图" in result.note
    finally:
        engine.close()


def test_cli_amap_dry_run_does_not_load_local_data(monkeypatch):
    settings = strict_settings()
    monkeypatch.setattr(g, "read_settings", lambda path: settings)
    monkeypatch.setattr(g, "parse_intent",
                        lambda *args, **kwargs: g.GeoIntent(intent="洗浴中心", modifiers=[]))
    called = False

    def load(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("local data must not load in strict Amap mode")

    monkeypatch.setattr(g, "load_data", load)
    result = CliRunner().invoke(g.cli, ["洗浴中心", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "高德 place/text" in result.output
    assert not called
