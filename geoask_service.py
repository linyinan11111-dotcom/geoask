"""GUI query service. Keep this engine and its DuckDB connection on one worker."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable
import hashlib
from geoask_runtime import atomic_output

import geoask as g
import geoask_places as places


def _amap_search(query, center, radius, limit, settings):
    if not settings.amap.enabled:
        return []
    from geoask_amap import search_places
    api_key, security_code = g.resolve_amap_credentials(settings.amap)
    return search_places(query, center, radius, limit, settings.amap,
                         api_key, security_code=security_code)


@dataclass(frozen=True)
class QueryRequest:
    query: str
    center: tuple[float, float]
    radius: float
    limit: int
    config_path: Path = g.DEFAULT_CONFIG
    api_key: str = field(default="", repr=False)
    llm: g.LLMSettings | None = None
    demo: bool = False
    dry_run: bool = False
    planning: bool = False
    demo_trip: bool = False


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
        notes = []
        if self.request.demo:
            notes.append("示例体验：意图使用固定测试桩，嵌入映射和地点查询真实执行。")
        if self.stops:
            for i, stop in enumerate(self.stops, 1):
                if stop.intent.modifiers:
                    notes.append(f"第{i}站未应用描述条件：" + "、".join(stop.intent.modifiers))
                if stop.blocked_reason:
                    notes.append(f"第{i}站：{stop.blocked_reason}")
                if stop.resolution == "name-fallback":
                    notes.append(f"第{i}站未达到类别匹配阈值，已按名称/关键词“{stop.intent.intent}”搜索；名称候选仍需确认。")
                if stop.intent.place_name:
                    notes.append(f"第{i}站按名称/已核实别名查找“{stop.intent.place_name}”，不受附近半径限制；仅确认地点，不确认演出或营业安排。")
            if self.request.dry_run:
                notes.append("仅生成查询预览；后续站依赖前站地点，尚未执行选点或道路规划。")
            elif all(stop.selected for stop in self.stops):
                notes.append("已按顺序选定地点；类别查询默认选择最近候选，可更换地点后规划道路路线。")
            return "\n".join(notes)
        if self.intent.modifiers:
            notes.append("数据无法验证，未应用的描述条件：" + "、".join(self.intent.modifiers))
        if self.match.category is None:
            notes.append("没有足够可信的类别匹配，请换一个地点类别。可用类别：" + "、".join(self.categories))
        elif self.request.dry_run:
            notes.append("查询预览已生成，未执行邻近查询。")
        elif not self.rows:
            notes.append("当前半径内没有匹配地点，可尝试增大查询半径。")
        return "\n".join(notes)


class QueryEngine:
    def __init__(self):
        self.con = None
        self.mapper = None
        self.signature = None
        self.categories = []
        self.data_count = 0

    def close(self):
        if self.con is not None:
            self.con.close()
        self.con = self.mapper = self.signature = None
        self.categories, self.data_count = [], 0

    def _prepare(self, settings: g.Settings, progress: Callable[[str], None], need_mapper=True):
        if not settings.geojson_path.is_file():
            raise g.GeoaskError(f"数据文件不存在：{settings.geojson_path}")
        catalog = settings.place_catalog_path
        # A same-size replacement can preserve timestamps. Hash the small regional
        # files; map/routing/default-input changes do not invalidate spatial data.
        def fingerprint(path):
            if path is None or not path.is_file():
                return None
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
        signature = (settings.model_dump_json(exclude={"llm", "map", "routing", "parking",
                       "default_location", "default_radius", "limit"}),
                     fingerprint(settings.geojson_path), fingerprint(catalog))
        if signature == self.signature:
            if need_mapper and self.mapper is None:
                if not self.categories:
                    return
                progress("正在加载本地语义模型，首次使用可能需要下载…")
                self.mapper = g.SemanticMapper(self.categories, settings)
            return
        self.close()
        progress("正在载入地点数据…")
        con = g.load_data(settings)
        try:
            categories = [r[0] for r in con.execute("SELECT DISTINCT category FROM poi ORDER BY category").fetchall()]
            count = con.execute("SELECT count(*) FROM poi").fetchone()[0]
            mapper = None
            if need_mapper and categories:
                progress("正在加载本地语义模型，首次使用可能需要下载…")
                mapper = g.SemanticMapper(categories, settings)
        except Exception:
            con.close()
            raise
        self.con, self.mapper, self.categories = con, mapper, categories
        self.data_count, self.signature = count, signature

    def run(self, request: QueryRequest, progress: Callable[[str], None] = lambda _: None) -> QueryResult:
        # Validate before requesting a paid API or loading/downloading any model.
        if not request.query.strip() or len(request.query) > 4000:
            raise g.GeoaskError("请输入 1–4000 个字符的查询。")
        g.build_query("validation", request.center, request.radius, request.limit)
        settings = g.read_settings(request.config_path)
        if request.planning:
            return self._run_plan(request, settings, progress)
        llm = request.llm or settings.llm
        progress("正在理解你的需求…")
        if request.demo:
            intent = g.GeoIntent(intent="咖啡", modifiers=[])
        elif request.api_key:
            with g.OpenAI(api_key=request.api_key, base_url=llm.base_url,
                          timeout=llm.timeout_seconds, max_retries=1) as client:
                intent = g.parse_intent(request.query, llm, client)
        else:
            intent = g.parse_intent(request.query, llm)
        g.validate_resolved_intent(intent, request.query)
        self._prepare(settings, progress, need_mapper=g.needs_mapper(intent))
        if intent.place_name:
            stop = self._choose_stops([intent], request, progress, settings=settings)[0]
            return QueryResult(request, intent, stop.match, list(self.categories), stop.candidates,
                               stop.sql, stop.params, self.data_count, [stop])
        progress("正在匹配地点类别…")
        # A dataset without a category column can still answer a name query.
        # Keep the same name fallback used by the CLI instead of failing before
        # the user's phrase is checked against imported place names.
        match = (g.match_intent(intent, self.categories, settings, self.mapper)
                 if self.categories else g.CategoryMatch(None, "当前数据没有类别信息", 0, 0))
        sql, params, rows = "", [], []
        if match.category is not None:
            sql, params = g.build_query(match.query_category, request.center, request.radius, request.limit,
                                        include_coordinates=True)
            if not request.dry_run:
                progress("正在查找附近地点…")
                rows = g.search(self.con, match.query_category, request.center, request.radius, request.limit,
                                include_coordinates=True)
                if not rows:
                    rows = _amap_search(intent.intent, request.center, request.radius, request.limit, settings)
        else:
            stop = self._name_fallback_stop(intent, match, request, progress, settings)
            return QueryResult(request, intent, stop.match, list(self.categories), stop.candidates,
                               stop.sql, stop.params, self.data_count, [stop])
        return QueryResult(request, intent, match, list(self.categories), rows, sql, params, self.data_count)

    def _name_fallback_stop(self, intent, category_match, request, progress, settings, center=None):
        """Use a low-confidence intent as a name search instead of guessing a category."""
        center = center or request.center
        sql, params = places.build_place_query(intent.intent, center, request.limit, settings)
        candidates = []
        reason = (f"类别匹配不足（{category_match.best_candidate}，相似度 {category_match.similarity:.3f}），"
                  f"已改用名称搜索“{intent.intent}”；请确认具体地点。")
        if not request.dry_run:
            progress(f"类别匹配不足，正在按名称搜索“{intent.intent}”…")
            candidates = places.search_place_with_variants(
                self.con, intent.intent, center, request.limit, settings)
            if not candidates:
                candidates = _amap_search(intent.intent, center, request.radius, request.limit, settings)
            if candidates and candidates[0].get("source"):
                reason = f"本地数据未找到，已从{candidates[0]['source']}找到名称候选；请确认具体地点。"
            if not candidates:
                reason = (f"类别匹配不足且名称中没有找到“{intent.intent}”，请补充完整名称、增大范围或切换地区数据。")
        return PlannedStop(intent, category_match, candidates, center, sql, params,
                           None, reason, "name-fallback")

    def _run_plan(self, request, settings, progress):
        llm = request.llm or settings.llm
        progress("正在理解行程顺序与出行方式…")
        if request.demo:
            intents = [g.GeoIntent(intent="咖啡", modifiers=[])]
            if request.demo_trip:
                intents.append(g.GeoIntent(intent="健身", modifiers=[]))
            plan = g.GeoPlan(stops=intents, travel_mode="walking")
        elif request.api_key:
            with g.OpenAI(api_key=request.api_key, base_url=llm.base_url,
                          timeout=llm.timeout_seconds, max_retries=1) as client:
                plan = g.parse_plan(request.query, llm, client)
        else:
            plan = g.parse_plan(request.query, llm)
        for intent in plan.stops:
            g.validate_resolved_intent(intent, request.query if len(plan.stops) == 1 else "")
        self._prepare(settings, progress, need_mapper=any(g.needs_mapper(i) for i in plan.stops))
        stops = self._choose_stops(plan.stops, request, progress, settings=settings)
        first = stops[0]
        return QueryResult(request, first.intent, first.match, list(self.categories), first.candidates,
                           first.sql, first.params, self.data_count, stops, plan.travel_mode)

    def _choose_stops(self, intents, request, progress, prefix=None, settings=None):
        settings = settings or g.read_settings(request.config_path)
        stops = list(prefix or [])
        center = request.center
        if stops:
            previous = stops[-1].selected
            center = (previous["longitude"], previous["latitude"]) if previous else None
        for intent in intents:
            progress(f"正在为第 {len(stops) + 1} 站“{intent.place_name or intent.intent}”查找地点…")
            match = (g.CategoryMatch(None, "按名称查找", 0, 0) if intent.place_name
                     else g.match_intent(intent, self.categories, settings, self.mapper))
            sql, params, candidates, reason = "", [], [], ""
            selected_index = None
            if center is None:
                reason = "请先为前一站选定地点，再查找这一站。"
            elif intent.place_name:
                sql, params = places.build_place_query(intent.place_name, center, request.limit, settings, intent.place_candidates)
                if not request.dry_run:
                    candidates = places.search_place_with_variants(
                        self.con, intent.place_name, center, request.limit, settings, intent.place_candidates)
                    if not candidates:
                        candidates = _amap_search(intent.place_name, center, request.radius, request.limit, settings)
                    if candidates and candidates[0].get("source"):
                        reason = f"本地数据未找到，已从{candidates[0]['source']}找到名称候选；请确认具体地点。"
                    if not candidates:
                        reason = "当前数据和地名目录中没有这个具体地点，请补充城市/完整名称或切换数据；不会替换成其他场馆。"
                    elif candidates[0]['name_match_count'] > 1:
                        reason = f"找到 {candidates[0]['name_match_count']} 个名称候选，请选择具体地点后规划路线；可补充完整名称或增大候选数量。"
                    elif candidates[0]['name_rank'] == 1:
                        reason = "按语言模型提出的正式名称在当前数据中找到了候选，请确认该地点后规划路线。"
                    elif candidates[0]['name_rank'] == 2:
                        reason = "仅找到名称包含该词的候选，请确认具体地点后规划路线。"
                    else:
                        selected_index = 0
            elif match.category is None:
                fallback = self._name_fallback_stop(intent, match, request, progress, settings, center)
                stops.append(fallback)
                center = (fallback.selected["longitude"], fallback.selected["latitude"]) if fallback.selected else None
                continue
            else:
                sql, params = g.build_query(match.query_category, center, request.radius, request.limit, include_coordinates=True)
                if not request.dry_run:
                    candidates = g.search(self.con, match.query_category, center, request.radius, request.limit, include_coordinates=True)
                    if not candidates:
                        reason = "前一站附近没有匹配地点，请增大半径或更换前一站地点。"
                    else:
                        selected_index = 0
            stop = PlannedStop(intent, match, candidates, center, sql, params, selected_index, reason,
                               "name" if intent.place_name else "category")
            stops.append(stop)
            center = (stop.selected["longitude"], stop.selected["latitude"]) if stop.selected else None
        return stops

    def reselect(self, result: QueryResult, stop_index: int, candidate_index: int, progress=lambda _: None):
        if result.request.dry_run or not 0 <= stop_index < len(result.stops):
            raise g.GeoaskError("当前没有可更换的行程地点。")
        stop = result.stops[stop_index]
        if not 0 <= candidate_index < len(stop.candidates):
            raise g.GeoaskError("请选择有效的候选地点。")
        settings = g.read_settings(result.request.config_path)
        self._prepare(settings, progress, need_mapper=any(g.needs_mapper(s.intent) for s in result.stops))
        prefix = [*result.stops[:stop_index], replace(stop, selected_index=candidate_index, blocked_reason="")]
        remaining = [s.intent for s in result.stops[stop_index+1:]]
        stops = self._choose_stops(remaining, result.request, progress, prefix, settings=settings)
        first = stops[0]
        return replace(result, stops=stops, intent=first.intent, match=first.match,
                       rows=first.candidates, sql=first.sql, params=first.params)

    def find_parking(self, result, radius, progress=lambda _: None):
        from geoask_parking import find_parking
        route_points(result)
        settings = g.read_settings(result.request.config_path)
        self._prepare(settings, progress, need_mapper=False)
        progress("正在查找最后一站附近的停车场…")
        return find_parking(self.con, result, radius, settings)


def route_points(result: QueryResult):
    if result.request.dry_run or not result.stops or not all(stop.selected for stop in result.stops):
        raise g.GeoaskError("请先为每一站选定地点，所有站点就绪后才能规划完整路线。")
    return [result.request.center, *[(stop.selected["longitude"], stop.selected["latitude"]) for stop in result.stops]]


def export_csv(path: Path, result: QueryResult):
    """Excel-friendly UTF-8 CSV. Escape formula-like names from untrusted datasets."""
    def text(value):
        value = str(value or "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value

    with atomic_output(path, encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["名称", "类别", "距离（米）", "WGS84 经度", "WGS84 纬度"])
        rows = [s.selected for s in result.stops if s.selected] if result.stops else result.rows
        for row in rows:
            writer.writerow([text(row["name"] or "未命名地点"), text(row["category"]),
                             round(row["dist_m"], 2), row["longitude"], row["latitude"]])
