"""Credential-adjacent regressions that do not need a display or a paid API."""
from datetime import datetime, timezone
import json
import os
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

import geoask as g
import geoask_location as location


def test_dotnet_seven_digit_timestamp_normalized_for_older_python(monkeypatch):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".1234567+00:00"
    real = datetime
    class OldDatetime:
        now = staticmethod(real.now)
        @staticmethod
        def fromisoformat(value):
            assert ".123456+00:00" in value
            return real.fromisoformat(value)
    monkeypatch.setattr(location, "datetime", OldDatetime)
    point = location.parse_device_location(dict(status="ok", longitude=116.46, latitude=39.92, timestamp=stamp))
    assert point.longitude == 116.46


def test_location_subprocess_helper_and_errors():
    if os.name != "nt":
        pytest.skip("Windows device helper")
    runner = MagicMock(return_value=NS(stdout=json.dumps(dict(status="ok", longitude=116.46,
        latitude=39.92, timestamp=datetime.now(timezone.utc).isoformat()))))
    assert location.get_device_location(runner).latitude == 39.92
    assert g.location_script().is_file()
    assert runner.call_args.args[0][-1] == str(g.location_script())
    for failure in (subprocess.TimeoutExpired("powershell", 30), OSError("unavailable")):
        runner.side_effect = failure
        with pytest.raises(g.GeoaskError, match="当前坐标未改变"):
            location.get_device_location(runner)
    runner.side_effect = None
    runner.return_value.stdout = "invalid JSON"
    with pytest.raises(g.GeoaskError):
        location.get_device_location(runner)
