"""Road-route values shared by Amap routing and the desktop API."""
from __future__ import annotations

from dataclasses import dataclass


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
    provider: str = "高德地图"
    language: str = ""
    instruction_language: str = "zh-CN"
    instruction_source: str = "高德地图"
