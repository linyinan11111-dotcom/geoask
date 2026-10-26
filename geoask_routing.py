"""Real road routing through a configurable Valhalla HTTP API (WGS84)."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geoask as g

MODES = {"walking": "pedestrian", "cycling": "bicycle", "driving": "auto"}
MODE_NAMES = {"walking": "步行", "cycling": "骑行", "driving": "驾车"}


@dataclass(frozen=True)
class RouteLeg:
    distance_m: float
    duration_s: float
    instructions: list[str]


@dataclass(frozen=True)
class RoadRoute:
    coordinates: list[tuple[float, float]]
    legs: list[RouteLeg]
    distance_m: float
    duration_s: float
    travel_mode: str
    provider: str = "Valhalla / OpenStreetMap"
    language: str = ""
    instruction_language: str = "zh-CN"
    instruction_source: str = "Valhalla"


def chinese_instruction(maneuver: dict) -> str:
    """Render Valhalla maneuver types, without translating or guessing road names.

    Public Valhalla builds currently have no zh-CN locale. Use their structured
    turn/road/distance fields rather than displaying an English fallback.
    """
    actions = {0: "请沿地图路线行进", 1: "出发", 2: "从右侧出发", 3: "从左侧出发",
        4: "到达目的地", 5: "到达目的地（右侧）", 6: "到达目的地（左侧）",
        7: "道路名称变更，继续前行", 8: "继续前行", 9: "稍向右转", 10: "右转", 11: "向右急转",
        12: "向右掉头", 13: "向左掉头", 14: "向左急转", 15: "左转", 16: "稍向左转",
        17: "直行进入匝道", 18: "向右进入匝道", 19: "向左进入匝道",
        20: "从右侧出口驶出", 21: "从左侧出口驶出", 22: "保持直行", 23: "靠右行进", 24: "靠左行进",
        25: "汇入道路", 26: "进入环岛", 27: "驶出环岛", 28: "进入轮渡", 29: "离开轮渡",
        30: "乘坐公共交通", 31: "换乘公共交通", 32: "继续乘坐公共交通",
        33: "前往公共交通站点", 34: "前往换乘点", 35: "离开公共交通站点",
        36: "继续前往目的地", 37: "从右侧汇入", 38: "从左侧汇入"}
    kind = maneuver.get("type")
    original = str(maneuver.get("instruction", ""))
    if kind is None and original and not re.search(r"[a-zA-Z]{2,}", original):
        return original
    action = actions.get(kind, "请沿地图路线行进")
    roads = maneuver.get("street_names") or maneuver.get("begin_street_names") or []
    road = " / ".join(str(name) for name in roads if name) if isinstance(roads, list) else ""
    if kind in (1, 2, 3):
        directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
        direction = maneuver.get("begin_cardinal_direction")
        bearing = maneuver.get("bearing_after")
        if isinstance(bearing, (int, float)) and math.isfinite(bearing):
            direction = int((bearing % 360 + 22.5) // 45) % 8
        if isinstance(direction, int) and 0 <= direction < len(directions):
            action += f"，向{directions[direction]}行进"
    if kind == 27:
        exit_number = maneuver.get("roundabout_exit_count")
        if isinstance(exit_number, int) and exit_number > 0:
            action = f"从环岛第 {exit_number} 个出口驶出"
    if road and kind not in (4, 5, 6):
        action += f"（{road}）"
    length = maneuver.get("length")
    if isinstance(length, (int, float)) and math.isfinite(length) and length > 0 and kind not in (4, 5, 6):
        action += f"，行进约 {length*1000:.0f} 米" if length < 1 else f"，行进约 {length:.2f} 公里"
    return action


def decode_polyline(shape: str, precision: int = 6) -> list[tuple[float, float]]:
    """Decode Valhalla's six-decimal polyline into longitude/latitude pairs."""
    cursor, lat, lon, points = 0, 0, 0, []
    while cursor < len(shape):
        deltas = []
        for _ in range(2):
            shift, result = 0, 0
            while True:
                if cursor >= len(shape) or shift > 30:
                    raise ValueError("Invalid encoded route geometry")
                byte = ord(shape[cursor]) - 63
                cursor += 1
                if not 0 <= byte <= 63:
                    raise ValueError("Invalid encoded route geometry")
                result |= (byte & 31) << shift
                shift += 5
                if byte < 32:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        point = lon / 10**precision, lat / 10**precision
        g.validate_location(*point)
        points.append(point)
    return points


def parse_route_response(payload: dict, travel_mode: str, expected_legs: int) -> RoadRoute:
    try:
        trip = payload["trip"]
        language = str(trip.get("language", ""))
        units = trip.get("units", "kilometers")
        if units not in ("kilometers", "miles"):
            raise ValueError("Unknown route distance unit")
        to_km = 1.609344 if units == "miles" else 1.
        local_instructions = not language.lower().startswith("zh")
        if trip.get("status", 0) != 0 or len(trip["legs"]) != expected_legs:
            raise ValueError("Incomplete route")
        coordinates, legs = [], []
        for leg in trip["legs"]:
            shape = decode_polyline(leg["shape"])
            if len(shape) < 2:
                raise ValueError("Empty route geometry")
            coordinates.extend(shape[1:] if coordinates and coordinates[-1] == shape[0] else shape)
            distance = float(leg["summary"]["length"]) * to_km * 1000
            duration = float(leg["summary"]["time"])
            if not all(math.isfinite(x) and x >= 0 for x in (distance, duration)):
                raise ValueError("Invalid route summary")
            instructions = [chinese_instruction(m | {"length": float(m.get("length", 0))*to_km}) if local_instructions else str(m["instruction"])
                            for m in leg.get("maneuvers", []) if m.get("instruction") or "type" in m]
            legs.append(RouteLeg(distance, duration, instructions))
        return RoadRoute(coordinates, legs, sum(l.distance_m for l in legs),
                         sum(l.duration_s for l in legs), travel_mode, language=language,
                         instruction_source="本地中文模板（基于道路转向数据）" if local_instructions else "Valhalla 中文")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise g.GeoaskError("路线接口未返回完整、有效的道路路线，请更换地点或出行方式重试。") from exc


def plan_road_route(points: list[tuple[float, float]], travel_mode: str,
                    settings: g.RoutingSettings, opener=None) -> RoadRoute:
    if travel_mode not in MODES or not 2 <= len(points) <= 7:
        raise g.GeoaskError("路线需要起点和 1–6 个目的地，出行方式为步行、骑行或驾车。")
    for point in points:
        g.validate_location(*point)
    payload = {"locations": [{"lon": lon, "lat": lat, "type": "break"} for lon, lat in points],
               "costing": MODES[travel_mode],
               "directions_options": {"language": "zh-CN", "units": "kilometers"}}
    # Public and self-hosted Valhalla endpoints support the documented JSON query parameter.
    joiner = "&" if "?" in settings.endpoint else "?"
    url = settings.endpoint + joiner + urlencode({"json": json.dumps(payload, separators=(",", ":"))})
    request = Request(url, headers={"User-Agent": f"geoask/{g.__version__} (desktop routing)", "Accept": "application/json"})
    try:
        with (opener or urlopen)(request, timeout=settings.timeout_seconds) as response:
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("Response too large")
        data = json.loads(raw)
    except HTTPError as exc:
        raise g.GeoaskError(f"路线服务返回 HTTP {exc.code}，请稍后重试或更换路线接口。地点选择已保留。") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise g.GeoaskError("无法连接路线服务或响应无效。地点选择已保留，可稍后重新规划。") from exc
    return parse_route_response(data, travel_mode, len(points) - 1)
