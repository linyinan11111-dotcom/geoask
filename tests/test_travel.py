"""Trip parsing, device-location checks and map tile configuration."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import geoask as g
from geoask_location import parse_device_location


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


def test_location_validates_freshness_accuracy_and_failure_states():
    now = datetime.now(timezone.utc)
    payload = {"status":"ok", "longitude":0, "latitude":0, "accuracy_m":35, "timestamp":now.isoformat()}
    location = parse_device_location(payload)
    assert location.longitude == 0 and location.latitude == 0 and location.accuracy_m == 35
    for bad in ({"status":"denied"}, {"status":"unavailable"}, {**payload,"latitude":100},
                {**payload,"timestamp":(now-timedelta(minutes=6)).isoformat()}, {**payload,"longitude":float('nan')}):
        with pytest.raises(g.GeoaskError):
            parse_device_location(bad)


def test_tile_url_config_rejects_unknown_placeholders_and_bad_protocol():
    for value in ('file:///{z}/{x}/{y}', 'https://example.com/{z}/{x}/{y}/{unknown}', 'https://example.com/no-xyz'):
        with pytest.raises(ValueError):
            g.MapSettings(tile_url=value)
