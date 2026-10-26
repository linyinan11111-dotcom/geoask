"""Optional online POI lookup through the Gaode/Amap Web service."""
from __future__ import annotations

import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geoask as g


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


def _distance_m(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(h))


def search_places(query, center, radius, limit, settings: g.AmapSettings, api_key, opener=None):
    """Return Amap POIs in the same row shape as local name searches."""
    if not api_key or not settings.enabled:
        return []
    params = {
        "key": api_key,
        "keywords": query,
        "location": f"{center[0]:.8f},{center[1]:.8f}",
        "radius": str(max(1, min(50000, int(radius)))),
        "page_size": str(max(1, min(25, int(limit)))),
        "page_num": "1",
        "output": "json",
        "show_fields": "business",
    }
    if settings.region:
        params["region"] = settings.region
    request = Request(settings.endpoint + "?" + urlencode(params),
                      headers={"User-Agent": "geoask/0.2"})
    try:
        with (opener or urlopen)(request, timeout=settings.timeout_seconds) as response:
            payload = json.loads(response.read(2_000_001).decode("utf-8"))
    except Exception as exc:
        g.get_logger().warning("Amap POI search failed: %s", type(exc).__name__)
        return []
    if str(payload.get("status")) != "1":
        g.get_logger().warning("Amap POI search returned an error")
        return []
    pois = payload.get("pois") or []
    total = int(payload.get("count") or len(pois))
    rows = []
    for poi in pois:
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
