"""Query service for the desktop and CLI. Place search and routing use Amap."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import geoask as g


def name_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def _amap_credentials(settings, request=None):
    """Resolve strict Amap Web-service credentials."""
    if not settings.amap.enabled or not settings.amap.primary:
        raise g.GeoaskError("当前版本只使用高德在线查询。请在配置中设置 amap.enabled 与 amap.primary 为 true。")
    if request is not None and request.amap_key is not None:
        key, security_code = request.amap_key, request.amap_security_code or ""
    else:
        key, security_code = g.resolve_amap_credentials(settings.amap)
    if not key:
        raise g.GeoaskError("尚未配置高德 Web 服务 Key。请保存 AMAP_WEB_KEY 或在接口设置中填写高德 Key。")
    return key, security_code


def _amap_name_rank(query, name):
    key = name_key(query)
    candidate = name_key(name)
    if not key or not candidate:
        return 1
    if key == candidate:
        return 0
    if key in candidate or candidate in key:
        return 2
    return 1


def _amap_categories(stops):
    seen = []
    for stop in stops:
        for row in stop.candidates:
            category = str(row.get("category") or "")
            if category and category not in seen:
                seen.append(category)
    return seen


def _amap_data_count(stops):
    for stop in stops:
        if stop.candidates:
            try:
                return int(stop.candidates[0].get("name_match_count") or len(stop.candidates))
            except (TypeError, ValueError):
                return len(stop.candidates)
    return 0


@dataclass(frozen=True)
class QueryRequest:
    query: str
    center: tuple[float, float]
    radius: float
    limit: int
    config_path: Path = g.DEFAULT_CONFIG
    api_key: str = field(default="", repr=False)
    llm: g.LLMSettings | None = None
    dry_run: bool = False
    planning: bool = False
    # Kept so callers can state that this request uses Amap Web services.
    amap: bool = True
    amap_key: str | None = field(default=None, repr=False)
    amap_security_code: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PlannedStop:
    intent: g.GeoIntent
    match: g.CategoryMatch
    candidates: list[dict]
    center: tuple[float, float] | None
    sql: str = ""
    params: list = field(default_factory=list)
    selected_index: int | None = None
    blocked_reason: str = ""
    resolution: str = "category"

    @property
    def selected(self):
        return self.candidates[self.selected_index] if self.selected_index is not None else None


@dataclass(frozen=True)
class QueryResult:
    request: QueryRequest = field(repr=False)
    intent: g.GeoIntent
    match: g.CategoryMatch
    categories: list[str]
    rows: list[dict]
    sql: str
    params: list
    data_count: int
    stops: list[PlannedStop] = field(default_factory=list)
    travel_mode: str = "walking"

    @property
    def note(self) -> str:
        notes = ["数据来源：高德地图在线服务；坐标已转换为 WGS84 后展示和计算距离。"]
        if self.stops:
            for i, stop in enumerate(self.stops, 1):
                if stop.intent.modifiers:
                    notes.append(f"第{i}站未应用描述条件：" + "、".join(stop.intent.modifiers))
                if stop.blocked_reason:
                    notes.append(f"第{i}站：{stop.blocked_reason}")
                if stop.intent.place_name:
                    notes.append(f"第{i}站按名称查找“{stop.intent.place_name}”；仅确认地点，不确认演出或营业安排。")
            if self.request.dry_run:
                notes.append("仅生成查询预览；后续站依赖前站地点，尚未执行选点或道路规划。")
            elif all(stop.selected for stop in self.stops):
                notes.append("已按顺序选定地点；类别查询默认选择最近候选，可更换地点后规划道路路线。")
            return "\n".join(notes)
        if self.intent.modifiers:
            notes.append("数据无法验证，未应用的描述条件：" + "、".join(self.intent.modifiers))
        if self.match.category is None:
            notes.append("没有足够可信的类别匹配，请换一个地点类别。")
        elif self.request.dry_run:
            notes.append("查询预览已生成，未执行邻近查询。")
        elif not self.rows:
            notes.append("当前半径内没有匹配地点，可尝试增大查询半径。")
        return "\n".join(notes)


class QueryEngine:
    def close(self):
        return None

    def run(self, request: QueryRequest, progress: Callable[[str], None] = lambda _: None) -> QueryResult:
        # Validate before requesting a paid API.
        if not request.query.strip() or len(request.query) > 4000:
            raise g.GeoaskError("请输入 1–4000 个字符的查询。")
        g.validate_search_bounds(request.center, request.radius, request.limit)
        settings = g.read_settings(request.config_path)
        return self._run_amap(request, settings, progress)

    def _parse_amap_intents(self, request, settings, progress):
        llm = request.llm or settings.llm
        if request.planning:
            progress("正在理解行程顺序与出行方式…")
            if request.api_key:
                with g.OpenAI(api_key=request.api_key, base_url=llm.base_url,
                              timeout=llm.timeout_seconds, max_retries=1) as client:
                    plan = g.parse_plan(request.query, llm, client)
            else:
                plan = g.parse_plan(request.query, llm)
            for intent in plan.stops:
                g.validate_resolved_intent(intent, request.query if len(plan.stops) == 1 else "")
            return list(plan.stops), plan.travel_mode
        progress("正在理解你的需求…")
        if request.api_key:
            with g.OpenAI(api_key=request.api_key, base_url=llm.base_url,
                          timeout=llm.timeout_seconds, max_retries=1) as client:
                intent = g.parse_intent(request.query, llm, client)
        else:
            intent = g.parse_intent(request.query, llm)
        g.validate_resolved_intent(intent, request.query)
        return [intent], "walking"

    def _run_amap(self, request, settings, progress):
        key, security_code = _amap_credentials(settings, request)
        intents, travel_mode = self._parse_amap_intents(request, settings, progress)
        stops = self._amap_choose_stops(intents, request, settings, progress, key, security_code)
        first = stops[0]
        if not request.dry_run and not first.candidates:
            raise g.GeoaskError(first.blocked_reason or "高德地图没有找到匹配地点。")
        return QueryResult(request, first.intent, first.match, _amap_categories(stops),
                           first.candidates, first.sql, first.params, _amap_data_count(stops),
                           stops, travel_mode)

    def _amap_choose_stops(self, intents, request, settings, progress, key, security_code, prefix=None):
        stops = list(prefix or [])
        center = request.center
        if stops:
            previous = stops[-1].selected
            center = (previous["longitude"], previous["latitude"]) if previous else None
        for intent in intents:
            progress(f"正在为第 {len(stops) + 1} 站“{intent.place_name or intent.intent}”查找高德地点…")
            if center is None:
                stops.append(PlannedStop(intent, g.CategoryMatch(None, "无中心", 0, 0), [], None,
                                         blocked_reason="请先为前一站选定地点，再查找这一站。",
                                         resolution="amap"))
                continue
            stop = self._amap_stop(intent, center, request, settings, key, security_code)
            stops.append(stop)
            center = (stop.selected["longitude"], stop.selected["latitude"]) if stop.selected else None
        return stops

    def _amap_stop(self, intent, center, request, settings, key, security_code):
        query = intent.place_name or intent.intent
        # A named destination must be found beyond the nearby radius; keywords
        # search uses Amap's maximum useful radius.  Category queries keep the
        # user's radius.
        radius = 50000 if intent.place_name else request.radius
        resolution = "amap-name" if intent.place_name else "amap-category"
        sql = (f"高德 place/text: keywords={query}, location={center[0]:.7f},{center[1]:.7f}, "
               f"radius={radius:g}, limit={request.limit}")
        if request.dry_run:
            return PlannedStop(intent, g.CategoryMatch(None, query, 0, 0), [], center, sql, [],
                               None, "预览：未执行高德查询。", resolution)
        from geoask_amap import search_pois
        rows = search_pois(query, center, radius, request.limit, settings.amap, key,
                           security_code, strict=True)
        for row in rows:
            row["name_rank"] = _amap_name_rank(query, row["name"]) if intent.place_name else 0
        selected, reason = None, ""
        if not rows:
            reason = f"高德地图未找到“{query}”，请换一个名称、关键词或增大查询半径。"
        elif intent.place_name:
            total = int(rows[0].get("name_match_count") or len(rows))
            if total > 1 or rows[0]["name_rank"] > 0:
                reason = f"高德地图找到 {total} 个名称候选，请选择具体地点后规划路线。"
            else:
                selected = 0
        else:
            selected = 0
        match = g.CategoryMatch(rows[0].get("category") if rows else None, query,
                                1.0 if rows else 0.0, 2.0 if rows else 0.0)
        return PlannedStop(intent, match, rows, center, sql, [], selected, reason, resolution)

    def reselect(self, result: QueryResult, stop_index: int, candidate_index: int, progress=lambda _: None):
        if result.request.dry_run or not 0 <= stop_index < len(result.stops):
            raise g.GeoaskError("当前没有可更换的行程地点。")
        stop = result.stops[stop_index]
        if not 0 <= candidate_index < len(stop.candidates):
            raise g.GeoaskError("请选择有效的候选地点。")
        settings = g.read_settings(result.request.config_path)
        key, security_code = _amap_credentials(settings, result.request)
        prefix = [*result.stops[:stop_index], replace(stop, selected_index=candidate_index, blocked_reason="")]
        remaining = [s.intent for s in result.stops[stop_index+1:]]
        stops = self._amap_choose_stops(remaining, result.request, settings, progress,
                                        key, security_code, prefix)
        first = stops[0]
        return replace(result, stops=stops, intent=first.intent, match=first.match,
                       rows=first.candidates, sql=first.sql, params=first.params)

    def find_parking(self, result, radius, progress=lambda _: None):
        from geoask_amap import search_pois
        from geoask_parking import ParkingOptions
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or not 0 < radius <= 5000:
            raise g.GeoaskError("停车搜索半径必须在 0–5000 米之间。")
        route_points(result)
        settings = g.read_settings(result.request.config_path)
        key, security_code = _amap_credentials(settings, result.request)
        progress("正在查找最后一站附近的高德停车场…")
        destination = result.stops[-1].selected
        center = (destination["longitude"], destination["latitude"])
        rows = search_pois("停车场", center, radius, result.request.limit,
                           settings.amap, key, security_code, strict=True)
        total = 0
        if rows:
            try:
                total = int(rows[0].get("name_match_count") or len(rows))
            except (TypeError, ValueError):
                total = len(rows)
        return ParkingOptions(result, radius, rows, total)


def route_points(result: QueryResult):
    if result.request.dry_run or not result.stops or not all(stop.selected for stop in result.stops):
        raise g.GeoaskError("请先为每一站选定地点，所有站点就绪后才能规划完整路线。")
    return [result.request.center, *[(stop.selected["longitude"], stop.selected["latitude"]) for stop in result.stops]]
