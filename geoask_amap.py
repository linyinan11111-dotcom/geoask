"""Gaode (Amap) Web services: POI search and real road routing.

All public functions take WGS84 coordinates (the application's native CRS) and
convert at the HTTP boundary: Amap uses GCJ-02 for ``location``/``origin`` and
returns GCJ-02 polylines.  The response is converted back to WGS84 before it is
used for distances, markers or route geometry.
"""
from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geoask as g

POI_ENDPOINT = "https://restapi.amap.com/v5/place/text"
DRIVING_ENDPOINT = "https://restapi.amap.com/v3/direction/driving"
WALKING_ENDPOINT = "https://restapi.amap.com/v3/direction/walking"
BICYCLING_ENDPOINT = "https://restapi.amap.com/v4/direction/bicycling"


def _out_of_china(lon, lat):
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x, y):
    value = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    value += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi))*2.0/3.0
    value += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi))*2.0/3.0
    value += (160.0*math.sin(y/12.0*math.pi) + 320*math.sin(y*math.pi/30.0))*2.0/3.0
    return value


def _transform_lon(x, y):
    value = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    value += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi))*2.0/3.0
    value += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi))*2.0/3.0
    value += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi))*2.0/3.0
    return value


def gcj02_to_wgs84(lon, lat):
    """Convert Amap's GCJ-02 coordinates to the app's WGS84 coordinates."""
    if _out_of_china(lon, lat):
        return lon, lat
    a, ee = 6378245.0, 0.00669342162296594323
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = math.sin(rad)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = dlat * 180.0 / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    dlon = dlon * 180.0 / (a / sqrt_magic * math.cos(rad) * math.pi)
    return lon - dlon, lat - dlat


def wgs84_to_gcj02(lon, lat):
    """Convert WGS84 coordinates to the GCJ-02 grid used by Amap."""
    if _out_of_china(lon, lat):
        return lon, lat
    a, ee = 6378245.0, 0.00669342162296594323
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = math.sin(rad)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = dlat * 180.0 / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    dlon = dlon * 180.0 / (a / sqrt_magic * math.cos(rad) * math.pi)
    return lon + dlon, lat + dlat


def _distance_m(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(h))


def _error_message(payload):
    info = str(payload.get("info") or payload.get("errmsg") or "未知错误")
    code = payload.get("infocode") if payload.get("infocode") is not None else payload.get("errcode", "")
    return f"高德地图接口返回错误：{info}（{code}）"


def _request_json(endpoint, params, timeout, opener=None, *, strict=True):
    """Call an Amap endpoint; never include the key or URL in user-facing errors."""
    request = Request(endpoint + "?" + urlencode(params),
                      headers={"User-Agent": "geoask/0.2 (Amap Web service)"})
    try:
        with (opener or urlopen)(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response too large")
        return json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        if not strict:
            return None
        raise g.GeoaskError("无法连接高德地图服务或响应无效，请检查网络、额度与接口 Key。") from exc


def _amap_params(api_key, security_code):
    params = {"key": api_key}
    if security_code:
        params["jscode"] = security_code
    return params


def search_pois(keywords, center, radius, limit, settings: g.AmapSettings, api_key,
                security_code="", opener=None, *, strict=False):
    """Search Amap POIs and return the same row shape as local named searches.

    ``center`` is WGS84.  Amap receives a GCJ-02 location and every returned
    coordinate is converted back to WGS84 before distances are computed.
    """
    if not settings.enabled:
        if strict:
            raise g.GeoaskError("高德数据源未启用，请在配置中设置 amap.enabled: true。")
        return []
    if not api_key:
        if strict:
            raise g.GeoaskError("尚未配置高德 Web 服务 Key。请在环境变量或接口设置中保存 AMAP_WEB_KEY。")
        return []
    gcj_lon, gcj_lat = wgs84_to_gcj02(*center)
    page_size = max(1, min(25, int(limit)))
    params = _amap_params(api_key, security_code)
    params.update({
        "keywords": str(keywords),
        "location": f"{gcj_lon:.8f},{gcj_lat:.8f}",
        "radius": str(max(1, min(50000, int(radius)))),
        "page_size": str(page_size),
        "page_num": "1",
        "output": "json",
        "show_fields": "business",
    })
    if settings.region:
        params["region"] = settings.region
    payload = _request_json(settings.endpoint, params, settings.timeout_seconds, opener, strict=strict)
    if payload is None:
        return []
    if str(payload.get("status")) != "1":
        if strict:
            raise g.GeoaskError(_error_message(payload))
        return []
    try:
        total = int(payload.get("count") or 0)
    except (TypeError, ValueError):
        total = 0
    rows = []
    for poi in payload.get("pois") or []:
        try:
            lon, lat = (float(value) for value in str(poi["location"]).split(",", 1))
            lon, lat = gcj02_to_wgs84(lon, lat)
            g.validate_location(lon, lat)
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({
            "name": str(poi.get("name") or "未命名地点"),
            "category": str(poi.get("type") or poi.get("typecode") or "unknown"),
            "name_rank": 2,
            "name_match_count": total,
            "longitude": lon,
            "latitude": lat,
            "dist_m": _distance_m(center, (lon, lat)),
            "source": "高德地图",
        })
    return sorted(rows, key=lambda row: (row["dist_m"], row["name"]))


def search_places(query, center, radius, limit, settings: g.AmapSettings, api_key,
                  opener=None, security_code=""):
    """Backward-compatible soft search used only by optional fallback paths."""
    return search_pois(query, center, radius, limit, settings, api_key,
                       security_code=security_code, opener=opener, strict=False)


def _parse_polyline(value):
    points = []
    for pair in str(value or "").split(";"):
        if not pair.strip():
            continue
        try:
            lon, lat = (float(v) for v in pair.split(",", 1))
        except (TypeError, ValueError):
            continue
        lon, lat = gcj02_to_wgs84(lon, lat)
        try:
            g.validate_location(lon, lat)
        except ValueError:
            continue
        if not points or points[-1] != (lon, lat):
            points.append((lon, lat))
    return points


def _route_endpoint(travel_mode):
    return {"driving": DRIVING_ENDPOINT, "walking": WALKING_ENDPOINT,
            "cycling": BICYCLING_ENDPOINT}.get(travel_mode)


def _route_segment(start, end, travel_mode, settings, api_key, security_code, opener):
    from geoask_routing import RouteLeg
    endpoint = _route_endpoint(travel_mode)
    if endpoint is None:
        raise g.GeoaskError("高德路线仅支持步行、骑行和驾车。")
    origin = wgs84_to_gcj02(*start)
    destination = wgs84_to_gcj02(*end)
    params = _amap_params(api_key, security_code)
    params.update({"origin": f"{origin[0]:.8f},{origin[1]:.8f}",
                   "destination": f"{destination[0]:.8f},{destination[1]:.8f}"})
    if travel_mode != "cycling":
        params["extensions"] = "all"
    payload = _request_json(endpoint, params, settings.timeout_seconds, opener, strict=True)
    if payload is None:
        raise g.GeoaskError("高德路线服务没有返回数据。")
    if travel_mode == "cycling":
        if str(payload.get("errcode", "0")) != "0":
            raise g.GeoaskError(_error_message(payload))
        paths = (payload.get("data") or {}).get("paths") or []
    else:
        if str(payload.get("status")) != "1":
            raise g.GeoaskError(_error_message(payload))
        paths = (payload.get("route") or {}).get("paths") or []
    if not paths:
        raise g.GeoaskError("高德路线服务没有返回可行路线，请更换地点或出行方式。")
    path = paths[0]
    try:
        distance = float(path.get("distance") or 0)
        duration = float(path.get("duration") or 0)
    except (TypeError, ValueError) as exc:
        raise g.GeoaskError("高德路线返回了无效的距离或时间。") from exc
    if not (math.isfinite(distance) and math.isfinite(duration) and distance >= 0 and duration >= 0):
        raise g.GeoaskError("高德路线返回了无效的距离或时间。")
    coordinates, instructions = [], []
    for step in path.get("steps") or []:
        instruction = str(step.get("instruction") or "").strip()
        if instruction:
            instructions.append(instruction)
        coordinates.extend(_parse_polyline(step.get("polyline")))
    if len(coordinates) < 2:
        raise g.GeoaskError("高德路线返回的几何数据不完整，请更换地点或出行方式。")
    return RouteLeg(distance, duration, instructions), coordinates


def plan_route(points, travel_mode, settings: g.AmapSettings, api_key,
               security_code="", opener=None):
    """Plan a WGS84 road route through Amap, one request per itinerary leg."""
    from geoask_routing import MODE_NAMES, RoadRoute
    if travel_mode not in MODE_NAMES:
        raise g.GeoaskError("出行方式必须是步行、骑行或驾车。")
    if not 2 <= len(points) <= 7:
        raise g.GeoaskError("路线需要起点和 1–6 个目的地。")
    if not api_key:
        raise g.GeoaskError("尚未配置高德 Web 服务 Key，无法规划路线。")
    for point in points:
        g.validate_location(*point)
    legs, all_coordinates = [], []
    for start, end in zip(points, points[1:]):
        leg, coordinates = _route_segment(start, end, travel_mode, settings, api_key,
                                          security_code, opener)
        legs.append(leg)
        all_coordinates.extend(coordinates)
    return RoadRoute(all_coordinates, legs, sum(leg.distance_m for leg in legs),
                     sum(leg.duration_s for leg in legs), travel_mode,
                     provider="高德地图", language="zh-CN",
                     instruction_language="zh-CN", instruction_source="高德地图")
