"""Amap parking choices replace only the final driving endpoint."""
import pytest

import geoask as g
import geoask_amap as amap
from geoask_parking import ParkingOptions, parking_route_points
from geoask_service import PlannedStop, QueryEngine, QueryRequest, QueryResult, route_points


def sample_result():
    intent = g.GeoIntent(intent="咖啡", modifiers=[])
    rows = [{"name": "咖啡馆", "category": "咖啡", "longitude": 116.47, "latitude": 39.92,
             "dist_m": 10, "name_rank": 0, "name_match_count": 1}]
    stop = PlannedStop(intent, g.CategoryMatch("咖啡", "咖啡", 1, 1), rows, (116.46, 39.92), selected_index=0)
    request = QueryRequest("咖啡", (116.46, 39.92), 1000, 10, planning=True, amap=True,
                           amap_key="key", amap_security_code="code")
    return QueryResult(request, intent, stop.match, ["咖啡"], rows, "", [], 1, [stop], "walking")


def test_parking_route_replaces_only_the_last_point():
    result = sample_result()
    options = ParkingOptions(result, 500, [
        {"name": "P1", "longitude": 116.471, "latitude": 39.921, "dist_m": 20, "category": "停车场"},
        {"name": "P2", "longitude": 116.472, "latitude": 39.922, "dist_m": 40, "category": "停车场"},
    ], 2)
    before = route_points(result)
    points = parking_route_points(options, 1)
    assert points[:-1] == before[:-1] and points[-1] == (116.472, 39.922)
    assert route_points(result) == before
    assert "2" in options.summary
    for bad in (-1, 2, True, None):
        with pytest.raises(g.GeoaskError):
            parking_route_points(options, bad)


def test_engine_parking_uses_amap_keyword(monkeypatch):
    settings = g.Settings(amap=g.AmapSettings(enabled=True, primary=True),
                          routing=g.RoutingSettings(provider="amap"))
    monkeypatch.setattr(g, "read_settings", lambda path: settings)
    seen = {}

    def search(query, center, radius, limit, settings, key, code, **kwargs):
        seen.update(query=query, radius=radius, key=key)
        return [{"name": "地下停车场", "category": "停车场", "longitude": center[0], "latitude": center[1],
                 "dist_m": 30, "name_match_count": 4, "source": "高德地图"}]

    monkeypatch.setattr(amap, "search_pois", search)
    options = QueryEngine().find_parking(sample_result(), 800)
    assert seen == {"query": "停车场", "radius": 800, "key": "key"}
    assert options.total_count == 4 and options.candidates[0]["name"] == "地下停车场"
    assert options.destination["name"] == "咖啡馆"


def test_unresolved_trip_cannot_search_parking():
    result = sample_result()
    blocked = result.stops[0]
    result = QueryResult(result.request, result.intent, result.match, [], [], "", [], 0,
                         [PlannedStop(blocked.intent, blocked.match, blocked.candidates, blocked.center)], "walking")
    with pytest.raises(g.GeoaskError):
        QueryEngine().find_parking(result, 500)
    for radius in (0, -1, 5001, float("nan")):
        with pytest.raises(g.GeoaskError):
            QueryEngine().find_parking(sample_result(), radius)
