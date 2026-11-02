"""Parking choice around an already confirmed destination."""
from dataclasses import dataclass, field

import geoask as g
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


def parking_route_points(options: ParkingOptions, candidate_index: int):
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or not 0 <= candidate_index < len(options.candidates):
        raise g.GeoaskError("请先选择一个停车场。")
    points = route_points(options.result)
    candidate = options.candidates[candidate_index]
    parking = (candidate['longitude'], candidate['latitude'])
    g.validate_location(*parking)
    # Preserve earlier visits, replacing only the final driving endpoint with parking.
    return [*points[:-1], parking]
