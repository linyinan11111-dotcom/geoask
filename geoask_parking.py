"""Optional parking search around a confirmed destination, using the active regional data."""
from dataclasses import dataclass, field

import geoask as g
import geoask_places as places
from geoask_service import QueryResult, route_points


@dataclass(frozen=True)
class ParkingOptions:
    result: QueryResult = field(repr=False)
    radius: float
    candidates: list[dict]
    total_count: int = 0

    @property
    def summary(self):
        shown = len(self.candidates)
        total = self.total_count or shown
        return (f"找到 {total} 个停车场，显示最近 {shown} 个；可缩小半径或增大“最多结果”。"
                if total > shown else f"找到 {shown} 个停车场。")

    @property
    def destination(self):
        return self.result.stops[-1].selected


def find_parking(con, result: QueryResult, radius: float, settings: g.Settings):
    points = route_points(result)  # Blocks previews and unresolved destinations before any search.
    if not 0 < radius <= 5000:
        raise g.GeoaskError("停车场搜索半径应在 (0,5000] 米内。")
    center = points[-1]
    places.prepare_places(con, settings)
    # Includes unnamed parking areas, rather than requiring a destination-name match.
    sql = """WITH distances AS (
        SELECT name, category, ST_X(geometry) AS longitude, ST_Y(geometry) AS latitude,
               ST_Distance_Spheroid(geometry, ST_Point(?, ?)) AS dist_m
        FROM named_places WHERE category IN (SELECT unnest(?::VARCHAR[]))
    ), candidates AS (
        SELECT DISTINCT name, category, dist_m, longitude, latitude FROM distances WHERE dist_m <= ?
    ) SELECT *, count(*) OVER () AS total_count FROM candidates
      ORDER BY dist_m, name NULLS LAST, category, longitude, latitude LIMIT ?"""
    cursor = con.execute(sql, [*center, settings.parking.categories, radius, result.request.limit])
    rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    total = rows[0]['total_count'] if rows else 0
    for row in rows:
        row.pop('total_count')
    return ParkingOptions(result, radius, rows, total)


def parking_route_points(options: ParkingOptions, candidate_index: int):
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or not 0 <= candidate_index < len(options.candidates):
        raise g.GeoaskError("请先选择一个停车场。")
    points = route_points(options.result)
    candidate = options.candidates[candidate_index]
    parking = (candidate['longitude'], candidate['latitude'])
    g.validate_location(*parking)
    # Preserve earlier visits, replacing only the final driving endpoint with parking.
    return [*points[:-1], parking]
