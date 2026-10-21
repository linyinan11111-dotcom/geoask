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
    ) SELECT DISTINCT name, category, dist_m, longitude, latitude FROM distances
      WHERE dist_m <= ? ORDER BY dist_m, name NULLS LAST, category LIMIT ?"""
    cursor = con.execute(sql, [*center, settings.parking.categories, radius, result.request.limit])
    rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    return ParkingOptions(result, radius, rows)


def parking_route_points(options: ParkingOptions, candidate_index: int):
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or not 0 <= candidate_index < len(options.candidates):
        raise g.GeoaskError("请先选择一个停车场。")
    points = route_points(options.result)
    candidate = options.candidates[candidate_index]
    parking = (candidate['longitude'], candidate['latitude'])
    g.validate_location(*parking)
    # Preserve earlier visits, replacing only the final driving endpoint with parking.
    return [*points[:-1], parking]
