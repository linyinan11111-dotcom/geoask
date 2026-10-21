"""Itinerary order, road-routing profiles, device-location failures and map coordinates."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from urllib.error import URLError

import pytest

import geoask as g
from geoask_location import parse_device_location
from geoask_map import pixel_coordinate, world_pixel
from geoask_routing import decode_polyline, parse_route_response, plan_road_route
from geoask_service import QueryEngine, QueryRequest, route_points


def response(content=None, parsed=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
        content=content, parsed=parsed, refusal=None))])


def test_plan_json_boundary_preserves_order():
    client = MagicMock()
    payload = {"stops": [{"intent": "咖啡", "modifiers": []}, {"intent": "健身", "modifiers": []}], "travel_mode": "walking"}
    client.chat.completions.create.return_value = response(json.dumps(payload))
    plan = g.parse_plan("我想先喝咖啡然后去健身", g.LLMSettings(), client)
    assert [s.intent for s in plan.stops] == ["咖啡", "健身"]
    assert plan.travel_mode == "walking"
    assert client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}


def test_plan_structured_mode():
    client = MagicMock()
    plan = g.GeoPlan(stops=[g.GeoIntent(intent="餐厅", modifiers=["安静"])], travel_mode="driving")
    client.chat.completions.parse.return_value = response(parsed=plan)
    assert g.parse_plan("开车去安静餐厅", g.LLMSettings(response_format="structured"), client) == plan
    assert client.chat.completions.parse.call_args.kwargs["response_format"] is g.GeoPlan


@pytest.mark.parametrize("content", [
    "not json", '{}', '{"stops":[],"travel_mode":"walking"}',
    json.dumps({"stops": [{"intent":"咖啡","modifiers":[]}]*7, "travel_mode":"walking"}),
    '{"stops":[{"intent":"咖啡","modifiers":[]}],"travel_mode":"flying"}',
    '{"stops":[{"intent":"咖啡","modifiers":[],"sql":"DROP TABLE poi"}],"travel_mode":"walking"}',
])
def test_bad_plan_is_rejected(content):
    client = MagicMock()
    client.chat.completions.create.return_value = response(content)
    with pytest.raises(g.GeoaskError):
        g.parse_plan("anything", g.LLMSettings(), client)


@pytest.fixture
def engine(monkeypatch):
    matches = {"咖啡": "cafe", "健身": "fitness_centre"}
    monkeypatch.setattr(g, "SemanticMapper", lambda *args: SimpleNamespace(
        match=lambda text: g.CategoryMatch(matches.get(text), matches.get(text, "restaurant"), .9 if text in matches else .2, .4)))
    engine = QueryEngine()
    yield engine
    engine.close()


def trip_request(**kwargs):
    return QueryRequest("先喝咖啡然后去健身", (116.46,39.92), 1000, 10, demo=True, planning=True, demo_trip=True, **kwargs)


def test_itinerary_sequential_search_and_reselection(engine, monkeypatch):
    monkeypatch.setattr(g, "parse_plan", lambda *a, **kw: pytest.fail("demo called LLM"))
    result = engine.run(trip_request())
    assert len(result.stops) == 2
    first, second = result.stops
    assert first.selected["category"] == "cafe" and second.selected["category"] == "fitness_centre"
    assert second.center == (first.selected["longitude"], first.selected["latitude"])
    assert len(route_points(result)) == 3
    changed = engine.reselect(result, 0, 2)
    assert changed.stops[0].selected == result.stops[0].candidates[2]
    assert changed.stops[1].center == (changed.stops[0].selected["longitude"], changed.stops[0].selected["latitude"])
    assert result.stops[0].selected_index == 0  # Old result remains immutable.


def test_multi_stop_preview_never_searches_or_chooses_fake_following_origin(engine, monkeypatch):
    monkeypatch.setattr(g, "search", lambda *a, **kw: pytest.fail("preview queried data"))
    result = engine.run(trip_request(dry_run=True))
    assert result.stops[0].sql and not result.stops[0].selected
    assert result.stops[1].center is None and not result.stops[1].sql
    with pytest.raises(g.GeoaskError):
        route_points(result)


def test_missing_stop_blocks_later_stops_and_complete_route(engine, monkeypatch):
    plan = g.GeoPlan(stops=[g.GeoIntent(intent="火锅", modifiers=[]), g.GeoIntent(intent="健身", modifiers=[])], travel_mode="walking")
    monkeypatch.setattr(g, "parse_plan", lambda *a, **kw: plan)
    result = engine.run(replace(trip_request(), demo=False))
    assert not result.stops[0].selected
    assert result.stops[1].center is None and not result.stops[1].candidates
    with pytest.raises(g.GeoaskError):
        route_points(result)


def test_changed_start_invalidates_all_origins(engine):
    req = replace(trip_request(), center=(116.452,39.913))
    result = engine.run(req)
    assert result.stops[0].center == req.center
    assert route_points(result)[0] == req.center


def encode(points):
    output, previous = [], [0,0]
    for lon, lat in points:
        for i, value in enumerate((lat,lon)):
            number = round(value*1e6)
            delta = number-previous[i]
            previous[i] = number
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 32:
                output.append(chr((32 | (delta & 31))+63))
                delta >>= 5
            output.append(chr(delta+63))
    return ''.join(output)


def test_polyline_known_reference_and_truncation():
    # Standard Google reference is precision 5; Valhalla uses precision 6.
    assert decode_polyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@', 5) == [(-120.2,38.5),(-120.95,40.7),(-126.453,43.252)]
    with pytest.raises(ValueError):
        decode_polyline('_')


@pytest.mark.parametrize("mode,costing", [("walking","pedestrian"),("cycling","bicycle"),("driving","auto")])
def test_route_api_uses_real_mode_and_preserves_waypoints(mode, costing):
    points = [(116.46,39.92), (116.454,39.914), (116.452,39.912)]
    data = {"trip": {"status":0,"legs":[
        {"shape":encode(points[:2]), "summary":{"length":1.1,"time":800}, "maneuvers":[{"instruction":"向南步行"}]},
        {"shape":encode(points[1:]), "summary":{"length":.3,"time":200}}]}}
    seen = []
    def opener(request, timeout):
        seen.append(json.loads(parse_qs(urlsplit(request.full_url).query)["json"][0]))
        return BytesIO(json.dumps(data).encode())
    route = plan_road_route(points, mode, g.RoutingSettings(), opener)
    assert seen[0]["costing"] == costing
    assert [(p["lon"],p["lat"]) for p in seen[0]["locations"]] == points
    assert route.coordinates == points and len(route.legs) == 2
    assert route.distance_m == 1400 and route.duration_s == 1000


def test_route_failure_does_not_fabricate_straight_line():
    def unavailable(*a, **kw):
        raise URLError("offline")
    with pytest.raises(g.GeoaskError, match="地点选择已保留"):
        plan_road_route([(116.46,39.92),(116.45,39.91)], "walking", g.RoutingSettings(), unavailable)
    with pytest.raises(g.GeoaskError):
        parse_route_response({"trip":{"legs":[]}}, "walking", 2)
    with pytest.raises(g.GeoaskError):
        parse_route_response({"trip":{"legs":[{"shape":"_", "summary":{"length":1,"time":1}}]}}, "walking", 1)


def test_location_validates_freshness_accuracy_and_failure_states():
    now = datetime.now(timezone.utc)
    payload = {"status":"ok", "longitude":0, "latitude":0, "accuracy_m":35, "timestamp":now.isoformat()}
    location = parse_device_location(payload)
    assert location.longitude == 0 and location.latitude == 0 and location.accuracy_m == 35
    for bad in ({"status":"denied"}, {"status":"unavailable"}, {**payload,"latitude":100},
                {**payload,"timestamp":(now-timedelta(minutes=6)).isoformat()}, {**payload,"longitude":float('nan')}):
        with pytest.raises(g.GeoaskError):
            parse_device_location(bad)


@pytest.mark.parametrize("point", [(116.46,39.92),(0,0),(-77.3,-35),(179.99,80)])
def test_map_projection_roundtrip(point):
    lon, lat = pixel_coordinate(*world_pixel(*point,16),16)
    assert lon == pytest.approx(point[0]) and lat == pytest.approx(point[1])


def test_tile_url_config_rejects_unknown_placeholders_and_bad_protocol():
    for value in ('file:///{z}/{x}/{y}', 'https://example.com/{z}/{x}/{y}/{unknown}', 'https://example.com/no-xyz'):
        with pytest.raises(ValueError):
            g.MapSettings(tile_url=value)
