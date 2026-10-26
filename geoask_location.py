"""On-demand Windows device location. No IP-location substitution."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import re
import subprocess

import geoask as g


@dataclass(frozen=True)
class DeviceLocation:
    longitude: float
    latitude: float
    accuracy_m: float | None
    timestamp: str


def parse_device_location(data: dict) -> DeviceLocation:
    status = data.get("status")
    if status == "denied":
        raise g.GeoaskError("Windows 未允许定位。请在“设置 → 隐私和安全性 → 位置”中启用定位及桌面应用访问，或手动输入坐标。")
    if status != "ok":
        raise g.GeoaskError("设备暂时无法提供位置，可能未启用定位、没有定位硬件或信号不足。请手动输入 WGS84 坐标。")
    try:
        lon, lat = float(data["longitude"]), float(data["latitude"])
        g.validate_location(lon, lat)
        timestamp = str(data["timestamp"])
        # .NET round-trip format has seven fractional digits; accept older Python too.
        normalized = re.sub(r"(\.\d{6})\d+(?=Z|[+-]|$)", r"\1", timestamp)
        captured = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if captured.tzinfo is None or abs((datetime.now(timezone.utc) - captured).total_seconds()) > 300:
            raise ValueError("stale location")
        accuracy = float(data["accuracy_m"]) if data.get("accuracy_m") is not None else None
        if accuracy is not None and (not math.isfinite(accuracy) or accuracy < 0):
            accuracy = None
        return DeviceLocation(lon, lat, accuracy, timestamp)
    except (KeyError, TypeError, ValueError) as exc:
        raise g.GeoaskError("设备返回的位置无效或超过五分钟，未替换当前坐标。请重试或手动输入。") from exc


def get_device_location(runner=None) -> DeviceLocation:
    if os.name != "nt":
        raise g.GeoaskError("当前设备定位实现支持 Windows；请在经纬度框中手动输入坐标。")
    try:
        result = (runner or subprocess.run)(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(g.location_script())],
            capture_output=True, text=True, encoding="utf-8-sig", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return parse_device_location(json.loads(result.stdout.strip()))
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise g.GeoaskError("系统定位没有返回有效结果，当前坐标未改变。请检查 Windows 定位设置，或手动输入经纬度。") from exc
