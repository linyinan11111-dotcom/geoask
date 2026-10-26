"""Native Chinese desktop interface. No web server or additional UI dependency."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import geoask as g
from geoask_service import QueryEngine, QueryRequest, QueryResult, export_csv, route_points
from geoask_map import SlippyMap
from geoask_location import get_device_location
from geoask_routing import MODE_NAMES, plan_road_route
from geoask_parking import parking_route_points
from geoask_runtime import drain_events, get_logger
import geoask_credentials as credentials

BG = "#f3f6f5"
WHITE = "#ffffff"
INK = "#172d2a"
MUTED = "#71817c"
ACCENT = "#087f6a"
PALE = "#e4f3ed"
LINE = "#e1e8e4"
FONT = "Microsoft YaHei UI"
CATEGORY_NAMES = {"cafe": "咖啡馆", "restaurant": "餐厅", "park": "公园",
                  "railway_station": "轨道站点", "supermarket": "超市", "fitness_centre": "健身场所",
                  "stadium": "体育场", "unknown": "未提供类别"}
UI_MODES = {"步行": "walking", "骑行": "cycling", "驾车": "driving"}


def distance_label(value: float) -> str:
    return f"{value:,.0f} 米" if value < 1000 else f"{value / 1000:.2f} 公里"


def result_status(result):
    if result.request.dry_run:
        return "查询预览已生成，尚未选择地点。"
    if any(s.candidates and s.selected is None for s in result.stops):
        return "请选择具体地点，确认后再规划路线。"
    if result.stops and all(s.selected for s in result.stops):
        return "地点选择已完成，支持在行程中更换候选地点。"
    return "尚未选定全部地点，请查看提示并调整查询。"


def result_map_points(result, active_stop):
    points = [result.request.center]
    points.extend((s.selected["longitude"], s.selected["latitude"]) for s in result.stops if s.selected)
    if 0 <= active_stop < len(result.stops):
        points.extend((p["longitude"], p["latitude"]) for p in result.stops[active_stop].candidates)
    return points


def validate_settings_form(fields, response_format, map_fields, llm_timeout, routing_timeout):
    from urllib.parse import urlsplit
    values = {k: v for k, v in fields.items() if k != "api_key"}
    if any(not value for value in values.values()):
        raise ValueError("请填写接口地址、模型名称和密钥环境变量。")
    parsed = urlsplit(values["base_url"])
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("接口地址需要是有效的 HTTP(S) URL，请勿在 URL 中填写密钥。")
    llm = g.LLMSettings(**values, response_format=response_format, timeout_seconds=llm_timeout)
    maps = g.MapSettings(**{k: v for k, v in map_fields.items() if k != "endpoint"})
    routing = g.RoutingSettings(endpoint=map_fields["endpoint"], timeout_seconds=routing_timeout)
    return llm, maps, routing, fields["api_key"]


def settings_error_message(exc):
    if isinstance(exc, g.ValidationError):
        labels = {"tile_url": "底图地址", "endpoint": "路径接口", "model": "模型名称",
                  "response_format": "返回格式", "timeout_seconds": "超时时间"}
        error = exc.errors(include_url=False)[0]
        label = labels.get(error["loc"][-1] if error["loc"] else "", "接口配置")
        # Custom validators provide actionable Chinese messages; numeric/type
        # errors are summarized without Pydantic's English internals or inputs.
        cause = error.get("ctx", {}).get("error")
        return str(cause) if cause else f"{label}无效，请检查填写的格式和取值范围。"
    return str(exc)


class GeoaskApp(tk.Tk):
    def __init__(self, config_path: Path = g.DEFAULT_CONFIG):
        super().__init__()
        self.title("geoask · 发现附近")
        self.geometry("1420x940")
        self.minsize(1120, 800)
        self.configure(bg=BG)
        self.config_path = Path(config_path)
        self.result: QueryResult | None = None
        self.busy = False
        self.generation = 0
        self.closed = False
        self.road_route = None
        self.parking_options = None
        self.parking_index = None
        self.route_parking = None
        self.mode_before_parking = None
        self.active_stop = 0
        self.locating = False
        self.stop_combos = []
        self.settings_window = None
        self.import_window = None
        self.jobs, self.events = Queue(), Queue()
        self.query = tk.StringVar(value="我想喝咖啡")
        self.longitude, self.latitude = tk.StringVar(), tk.StringVar()
        self.radius, self.limit = tk.StringVar(), tk.StringVar()
        self.api_key = tk.StringVar()
        self.api_key_is_saved = False
        self.preview_only = tk.BooleanVar(value=False)
        self.auto_route = tk.BooleanVar(value=True)
        self.parking_enabled = tk.BooleanVar(value=False)
        self.parking_radius = tk.StringVar(value="1000")
        self.parking_message = tk.StringVar(value="可选：驾车到最后一站附近的停车场")
        self.travel_mode = tk.StringVar(value="自动（默认步行）")
        self.position_source = tk.StringVar(value="WGS84 坐标 · 可手动输入或使用设备定位")
        self.route_summary = tk.StringVar(value="先输入需求，选择地点，再规划真实道路路线。")
        self.status = tk.StringVar(value="准备好了，说说你想去哪里。")
        self.connection = tk.StringVar()
        self._style()
        self._layout()
        self._load_config(self.config_path)
        if self.config_path.resolve() == g.DEFAULT_CONFIG.resolve():
            self._restore_connection(restore_profile=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-Return>", lambda _: self.submit())
        self.worker = Thread(target=self._work, name="geoask-query", daemon=True)
        self.worker.start()
        self.after(80, self._poll)

    def _style(self):
        self.option_add("*Font", (FONT, 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TEntry", fieldbackground=WHITE, foreground=INK, padding=9,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map("TEntry", bordercolor=[("focus", ACCENT)])
        style.configure("TCombobox", padding=8, fieldbackground=WHITE)
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK,
                        rowheight=43, borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading", background="#f5f8f6", foreground=MUTED,
                        padding=(10, 12), relief="flat", font=(FONT, 9))
        style.map("Treeview", background=[("selected", PALE)], foreground=[("selected", ACCENT)])
        style.map("Treeview.Heading", background=[("active", "#edf3ef")])
        style.configure("TNotebook", background=WHITE, borderwidth=0)
        style.configure("TNotebook.Tab", background="#f4f7f5", padding=(16, 9), foreground=MUTED)
        style.map("TNotebook.Tab", background=[("selected", WHITE)], foreground=[("selected", ACCENT)])
        style.configure("TCheckbutton", background=WHITE, foreground=MUTED, font=(FONT, 9))
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=LINE,
                        borderwidth=0, thickness=3)

    @staticmethod
    def _label(parent, text="", *, size=10, color=INK, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    @staticmethod
    def _button(parent, text, command, primary=False, **kwargs):
        return tk.Button(parent, text=text, command=command, bg=ACCENT if primary else WHITE,
                         fg=WHITE if primary else ACCENT, activebackground="#066a59" if primary else PALE,
                         activeforeground=WHITE if primary else ACCENT, relief="flat", bd=0,
                         padx=16, pady=10, cursor="hand2", font=(FONT, 10, "bold"),
                         disabledforeground="#a5b7b0", **kwargs)

    @staticmethod
    def _card(parent):
        return tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)

    def _layout(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(25, 19))
        self._label(header, "●", size=22, color=ACCENT).pack(side="left", padx=(0, 9))
        self._label(header, "geoask", size=25, bold=True).pack(side="left")
        self._label(header, "  /  发现附近", size=12, color=MUTED).pack(side="left", pady=(6, 0))
        self._label(header, "自然语言 · 地图 · 多站行程", size=10, color=MUTED).pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.grid(row=1, column=0, sticky="nsew", padx=30)
        body.columnconfigure(0, weight=0, minsize=330)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        sidebar = tk.Frame(body, bg=BG, width=330)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(0, weight=1)
        left_canvas = tk.Canvas(sidebar, width=310, bg=BG, highlightthickness=0)
        left_canvas.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(sidebar, command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left = tk.Frame(left_canvas, bg=BG)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def resize_sidebar(_=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            if left.winfo_reqheight() > left_canvas.winfo_height():
                left_scroll.grid(row=0, column=1, sticky="ns")
            else:
                left_scroll.grid_remove()

        def resize_view(event):
            left_canvas.itemconfigure(left_window, width=event.width)
            resize_sidebar()

        left.bind("<Configure>", resize_sidebar)
        left_canvas.bind("<Configure>", resize_view)

        def scroll_sidebar(event):
            widget = event.widget
            while widget is not None:
                if widget == sidebar:
                    left_canvas.yview_scroll(-int(event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)

        self.bind_all("<MouseWheel>", scroll_sidebar, add=True)
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        form = self._card(left)
        form.pack(fill="x")
        inner = tk.Frame(form, bg=WHITE)
        inner.pack(fill="x", padx=21, pady=16)
        self._label(inner, "这次想去哪里？", size=16, bold=True).pack(anchor="w")
        self._label(inner, "例如：先喝咖啡，然后去健身。", size=9, color=MUTED).pack(anchor="w", pady=(6, 14))
        self._label(inner, "你的需求", size=9, bold=True).pack(anchor="w", pady=(0, 6))
        self.query_entry = ttk.Entry(inner, textvariable=self.query, width=27, font=(FONT, 11))
        self.query_entry.pack(fill="x")
        chips = tk.Frame(inner, bg=WHITE)
        chips.pack(fill="x", pady=(6, 12))
        for text, query in (("咖啡", "我想喝咖啡"), ("咖啡→健身", "我想先去喝咖啡然后去健身"), ("公园", "我想去公园散步")):
            button = tk.Button(chips, text=text, bg="#f0f6f2", fg=ACCENT, relief="flat",
                               padx=8, pady=5, cursor="hand2", font=(FONT, 9),
                               command=lambda q=query: self.query.set(q))
            button.pack(side="left", padx=(0, 6))

        self._label(inner, "查询中心", size=9, bold=True).pack(anchor="w", pady=(0, 6))
        position = tk.Frame(inner, bg=WHITE)
        position.pack(fill="x")
        position.columnconfigure((0, 1), weight=1, uniform="coordinate")
        for i, (name, variable) in enumerate((("经度", self.longitude), ("纬度", self.latitude))):
            slot = tk.Frame(position, bg=WHITE)
            slot.grid(row=0, column=i, sticky="ew", padx=(0, 8) if i == 0 else (0, 0))
            self._label(slot, name, size=9, color=MUTED).pack(anchor="w", pady=(0, 4))
            ttk.Entry(slot, textvariable=variable, width=12).pack(fill="x")
        self._label(inner, textvariable=self.position_source, size=8, color=MUTED, wraplength=260,
                    justify="left").pack(anchor="w", pady=(5, 5))
        origin_actions = tk.Frame(inner, bg=WHITE)
        origin_actions.pack(fill="x", pady=(0, 10))
        self.locate_button = self._button(origin_actions, "获取设备位置", self.locate)
        self.locate_button.pack(side="left")
        self.center_button = self._button(origin_actions, "地图定位", self.center_map)
        self.center_button.pack(side="right")

        options = tk.Frame(inner, bg=WHITE)
        options.pack(fill="x")
        options.columnconfigure((0, 1), weight=1, uniform="options")
        for i, (name, var) in enumerate((("查询半径 / 米", self.radius), ("最多结果 / 条", self.limit))):
            slot = tk.Frame(options, bg=WHITE)
            slot.grid(row=0, column=i, sticky="ew", padx=(0, 8) if i == 0 else (0, 0))
            self._label(slot, name, size=9, bold=True).pack(anchor="w", pady=(0, 6))
            ttk.Entry(slot, textvariable=var, width=12).pack(fill="x")
        self.preview = ttk.Checkbutton(inner, text="仅预览查询，不搜索地点", variable=self.preview_only)
        self.preview.pack(anchor="w", pady=(10, 9))
        self.search_button = self._button(inner, "开始查询   →", self.submit, primary=True)
        self.search_button.pack(fill="x")
        self.demo_button = self._button(inner, "试试咖啡示例 · 无需密钥", self.start_demo)
        self.demo_button.pack(fill="x", pady=(5, 0))
        self.trip_demo_button = self._button(inner, "体验：咖啡 → 健身", self.start_trip_demo)
        self.trip_demo_button.pack(fill="x")

        connection = self._card(left)
        connection.pack(fill="x", pady=(16, 0))
        self._label(connection, "意图解析接口", size=10, bold=True).pack(anchor="w", padx=20, pady=(17, 4))
        self.connection_label = self._label(connection, textvariable=self.connection, size=9, color=MUTED,
                                              wraplength=280, justify="left")
        self.connection_label.pack(anchor="w", padx=20)
        actions = tk.Frame(connection, bg=WHITE)
        actions.pack(fill="x", padx=7, pady=(5, 8))
        self.settings_button = self._button(actions, "接口设置", self.open_settings)
        self.settings_button.pack(side="left")
        self.config_button = self._button(actions, "更换配置", self.choose_config)
        self.config_button.pack(side="right")
        self.import_button = self._button(connection, "导入地区数据", self.import_data)
        self.import_button.pack(fill="x", padx=12, pady=(0, 8))

        top = tk.Frame(right, bg=BG)
        top.pack(fill="x", pady=(0, 12))
        self._label(top, "发现附近，串联你的行程", size=17, bold=True).pack(side="left")
        self.export_button = self._button(top, "导出行程 CSV", self.export)
        self.export_button.pack(side="right")
        self.export_button.configure(state="disabled")

        self.main_tabs = ttk.Notebook(right)
        self.main_tabs.pack(fill="both", expand=True)
        map_page, candidates_page, details_page = (tk.Frame(self.main_tabs, bg=WHITE) for _ in range(3))
        for page, title in ((map_page, "地图与行程"), (candidates_page, "候选地点"), (details_page, "理解与导航")):
            self.main_tabs.add(page, text=title)
        self.stop_controls = tk.Frame(map_page, bg=WHITE)
        self.stop_controls.pack(fill="x", padx=16, pady=(12, 4))
        self._label(self.stop_controls, "行程会按你说的顺序排列，最多支持 6 个目的地。", size=10, color=MUTED).pack(anchor="w")
        route_controls = tk.Frame(map_page, bg=WHITE)
        route_controls.pack(fill="x", padx=16, pady=7)
        self.mode_combo = ttk.Combobox(route_controls, textvariable=self.travel_mode,
                                       values=["自动（默认步行）", *UI_MODES], state="readonly", width=18)
        self.mode_combo.pack(side="left")
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _: self.mode_changed())
        self.auto_route_toggle = ttk.Checkbutton(route_controls, text="选好地点后自动规划", variable=self.auto_route)
        self.auto_route_toggle.pack(side="left", padx=12)
        self.route_button = self._button(route_controls, "规划路线", self.plan_route, primary=True)
        self.route_button.pack(side="right")
        self.route_button.configure(state="disabled")
        parking_controls = tk.Frame(map_page, bg=WHITE)
        parking_controls.pack(fill="x", padx=16, pady=(0, 3))
        self.parking_toggle = ttk.Checkbutton(parking_controls, text="驾车到最后一站附近停车场", variable=self.parking_enabled,
                                             command=self.parking_changed)
        self.parking_toggle.pack(side="left")
        self._label(parking_controls, "半径/米", size=9, color=MUTED).pack(side="left", padx=(10, 4))
        self.parking_radius_entry = ttk.Entry(parking_controls, textvariable=self.parking_radius, width=7)
        self.parking_radius_entry.pack(side="left")
        self.parking_radius_entry.bind("<Return>", lambda _: self.find_parking())
        self.parking_button = self._button(parking_controls, "查找停车场", self.find_parking)
        self.parking_button.pack(side="right")
        parking_choices = tk.Frame(map_page, bg=WHITE)
        self.parking_choices = parking_choices
        parking_choices.pack(fill="x", padx=16, pady=(0, 6))
        self.parking_combo = ttk.Combobox(parking_choices, state="disabled")
        self.parking_combo.pack(side="left", fill="x", expand=True)
        self.parking_combo.bind("<<ComboboxSelected>>", self.select_parking)
        self._label(parking_choices, textvariable=self.parking_message, size=8, color=MUTED,
                    wraplength=340, justify="left").pack(side="right", padx=(10,0))
        self.route_summary_label = self._label(map_page, textvariable=self.route_summary, size=10, color=ACCENT,
                                               wraplength=780, justify="left")
        self.route_summary_label.pack(fill="x", padx=16, pady=(3, 10))
        self.map_view = SlippyMap(map_page, on_marker=self.select_map_marker, on_position=self.map_origin, height=400)
        self.map_view.pack(fill="both", expand=True)
        self.map_canvas = self.map_view

        results = self._card(candidates_page)
        results.pack(fill="both", expand=True)
        summary = tk.Frame(results, bg=WHITE)
        summary.pack(fill="x", padx=20, pady=(17, 8))
        self.result_title = self._label(summary, "等待你的第一次探索", size=12, bold=True)
        self.result_title.pack(side="left")
        self.result_subtitle = self._label(summary, "按距离由近到远", size=9, color=MUTED)
        self.result_subtitle.pack(side="right")
        table_frame = tk.Frame(results, bg=WHITE)
        table_frame.pack(fill="both", expand=True, padx=10)
        self.table = ttk.Treeview(table_frame, columns=("number", "name", "category", "distance"),
                                  show="headings", selectmode="browse", height=6)
        for key, title, width, anchor in (("number", "#", 44, "center"), ("name", "地点", 260, "w"),
                                           ("category", "类别", 110, "w"), ("distance", "距离", 100, "e")):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=40, anchor=anchor, stretch=(key == "name"))
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, command=self.table.yview)
        scrollbar.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.tag_configure("alternate", background="#f7faf8")
        self.table.bind("<<TreeviewSelect>>", lambda _: self.draw_map())
        self.choose_button = self._button(results, "将所选地点用于当前站点", self.choose_candidate, primary=True)
        self.choose_button.pack(anchor="e", padx=20, pady=(8, 0))
        self.choose_button.configure(state="disabled")
        self.notice = self._label(results, "先体验咖啡示例，或配置 API 密钥开始自然语言查询。", size=9,
                                   color=MUTED, justify="left", anchor="w", wraplength=650)
        self.notice.pack(fill="x", padx=20, pady=13)

        bottom = tk.Frame(details_page, bg=WHITE)
        bottom.pack(fill="both", expand=True)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)
        details = self._card(bottom)
        details.grid(row=0, column=0, sticky="nsew")
        self.notebook = ttk.Notebook(details)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        self.explanation = self._text_tab("理解过程")
        self.sql_view = self._text_tab("查询预览")
        self.navigation = self._text_tab("分段导航")
        self._set_text(self.navigation, "选好每站地点并规划路线后，这里会显示每段的距离、预计用时和道路指引。")
        self._set_text(self.explanation, "01  理解需求\n提取你想去的地点类型。\n\n02  匹配类别\n在数据中的真实类别里找到语义匹配。\n\n03  查找附近\n按真实经纬度计算距离，返回附近地点。")
        self._set_text(self.sql_view, "查询后可在这里查看 SQL 和独立绑定参数。")

        footer = tk.Frame(self, bg=BG)
        footer.grid(row=2, column=0, sticky="ew", padx=30, pady=(14, 17))
        self.status_label = self._label(footer, textvariable=self.status, size=9, color=MUTED)
        self.status_label.pack(side="left")
        self._label(footer, "© OpenStreetMap contributors · ODbL", size=8, color=MUTED).pack(side="right")
        self.progress_container = tk.Frame(self, bg=BG, height=3)
        self.progress_container.grid(row=3, column=0, sticky="ew")
        self.progress = ttk.Progressbar(self.progress_container, mode="indeterminate")

    def _text_tab(self, title):
        frame = tk.Frame(self.notebook, bg=WHITE)
        self.notebook.add(frame, text=title)
        text = tk.Text(frame, bg=WHITE, fg=INK, relief="flat", font=(FONT, 9), wrap="word",
                       height=10, width=32, padx=13, pady=12, cursor="arrow")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.configure(yscrollcommand=scroll.set, state="disabled")
        return text

    @staticmethod
    def _set_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _load_config(self, path):
        previous_llm = getattr(self, 'llm', None)
        session_key = self.api_key.get().strip() if not self.api_key_is_saved else ''
        self.generation += 1
        try:
            self.settings = g.read_settings(path)
        except g.GeoaskError as exc:
            self.settings = g.Settings()
            self.status.set(str(exc))
        self.config_path = Path(path)
        self.longitude.set(f"{self.settings.default_location[0]:.8f}")
        self.latitude.set(f"{self.settings.default_location[1]:.8f}")
        self.radius.set(f"{self.settings.default_radius:g}")
        self.limit.set(str(self.settings.limit))
        self.parking_radius.set(f"{self.settings.parking.radius:g}")
        self.llm = self.settings.llm.model_copy(deep=True)
        self.routing_settings = self.settings.routing.model_copy(deep=True)
        self.map_settings = self.settings.map.model_copy(deep=True)
        self._restore_connection()
        if session_key and previous_llm and credentials.same_connection(previous_llm.model_dump(), self.llm.model_dump()):
            self.api_key.set(session_key)
            self.api_key_is_saved = False
        self.map_view.configure_map(self.map_settings, self.settings.cache_dir / "tiles")
        self.map_view.set_center(*self.settings.default_location, zoom=15)
        self._connection_status()
        self.position_source.set("WGS84 坐标 · 可手动输入或使用设备定位")
        self._clear_trip()

    def _clear_trip(self):
        self._clear_parking()
        self.result = self.road_route = None
        self.active_stop = 0
        for widget in self.stop_controls.winfo_children():
            widget.destroy()
        self.stop_combos = []
        self.table.delete(*self.table.get_children())
        self.result_title.configure(text="数据已就绪，请输入需求")
        self.result_subtitle.configure(text="")
        self.notice.configure(text=f"当前数据：{self.settings.geojson_path.name}")
        self.route_summary.set("请重新查询，行程将使用当前地区的数据。")
        self._set_text(self.explanation, "尚未查询。")
        self._set_text(self.sql_view, "尚未生成查询。")
        self._set_text(self.navigation, "尚未规划路线。")
        self.draw_map()
        self._set_busy(False)

    def _restore_connection(self, restore_profile=False):
        self.api_key.set("")
        self.api_key_is_saved = False
        try:
            profile = credentials.load_connection()
            if profile is None:
                return
            llm = g.LLMSettings.model_validate(profile['llm'])
            maps = g.MapSettings.model_validate(profile['map'])
            routing = g.RoutingSettings.model_validate(profile['routing'])
            if restore_profile:
                self.llm, self.map_settings, self.routing_settings = llm, maps, routing
                self.map_view.configure_map(maps, self.settings.cache_dir / 'tiles')
            self.api_key.set(credentials.saved_api_key(self.llm.model_dump()))
            self.api_key_is_saved = True
        except (credentials.LocalSettingsError, ValueError):
            self.status.set("本地接口设置未载入，请打开接口设置清除或重新保存。")
        self._connection_status()

    def _connection_status(self):
        ready = bool(self.api_key.get().strip() or os.getenv(self.llm.api_key_env))
        self.connection.set(f"{self.llm.model}\n" + ("密钥已就绪 · 仅用于意图解析" if ready else "尚未设置密钥，可先体验示例"))

    def choose_config(self):
        path = filedialog.askopenfilename(parent=self, title="选择 geoask 配置", filetypes=[("YAML 配置", "*.yaml *.yml")])
        if path:
            try:
                g.read_settings(Path(path))
            except g.GeoaskError as exc:
                messagebox.showerror("配置无法读取", str(exc), parent=self)
                return
            self._load_config(Path(path))
            self.status.set(f"已切换配置：{Path(path).name}。重新查询后将使用新的数据。")

    def import_data(self):
        if self.busy:
            return
        if self.import_window and self.import_window.winfo_exists():
            self.import_window.lift()
            return
        source = filedialog.askopenfilename(parent=self, title="选择其他地区的地点数据",
            filetypes=[("空间数据", "*.gpkg *.geojson *.json *.shp"), ("所有文件", "*.*")])
        if not source:
            return
        dialog = tk.Toplevel(self)
        self.import_window = dialog
        dialog.title("导入地区数据")
        dialog.geometry("570x740")
        dialog.configure(bg=WHITE)
        dialog.transient(self)
        dialog.grab_set()
        panel = tk.Frame(dialog, bg=WHITE, padx=24, pady=18)
        panel.pack(fill="both", expand=True)
        self._label(panel, Path(source).name, size=14, bold=True).pack(anchor="w", pady=(0,8))
        self._label(panel, "按实际列名填写。常见别名和 name:语言 字段会自动读取。\n"
                    "无类别字段可留空，仍支持具体目的地查询。", size=9, color=MUTED).pack(anchor="w")
        values = {}
        self.import_fields = values
        for label, key, default in (("图层（留空使用文件默认图层）", "layer", ""),
                                   ("名称字段", "name", "name"), ("类别字段（可空）", "category", "fclass"),
                                   ("几何字段", "geometry", "geom"), ("其他别名字段（用逗号分隔，可空）", "aliases", ""),
                                   ("源坐标系（例如 EPSG:4326 / EPSG:3857）", "source_crs", "EPSG:4326")):
            self._label(panel, label, size=9, bold=True).pack(anchor="w", pady=(10,3))
            values[key] = tk.StringVar(value=default)
            ttk.Entry(panel, textvariable=values[key]).pack(fill="x")
        self._label(panel, "保存独立地区配置；旧地区补充目录、别名和行程会清除。\n"
                    "验证成功后起点移至文件内第一个有效地点，可重新定位。", size=9, color=MUTED).pack(anchor="w", pady=12)

        def start():
            options = {k: v.get().strip() for k, v in values.items()}
            options['aliases'] = [c.strip() for c in options['aliases'].replace('，', ',').split(',') if c.strip()]
            if not options['name'] or not options['geometry'] or not options['source_crs']:
                messagebox.showerror("字段未填写", "请填写名称、几何字段和源坐标系。", parent=dialog)
                return
            destination = filedialog.asksaveasfilename(parent=dialog, title="保存地区配置（使用新的文件名）",
                initialfile=Path(source).stem + '.geoask.yaml', defaultextension='.yaml', filetypes=[("地区配置", "*.yaml")])
            if not destination:
                return
            if Path(destination).exists():
                messagebox.showerror("文件已存在", "请选择新的配置文件名。", parent=dialog)
                return
            # Region YAML carries endpoint choices; keys stay in the user profile/session.
            base = self.settings.model_copy(deep=True)
            base.llm, base.map, base.routing = self.llm, self.map_settings, self.routing_settings
            dialog.destroy()
            self._set_busy(True)
            self.status.set("正在验证地区数据和字段映射…")
            self._queue_job(("import", (Path(source), Path(destination), base, options)))

        self.import_apply_button = self._button(panel, "验证并导入", start, primary=True)
        self.import_apply_button.pack(fill="x")

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        dialog = tk.Toplevel(self)
        self.settings_window = dialog
        dialog.title("模型、地图与路线接口")
        dialog.geometry("630x840")
        dialog.resizable(False, False)
        dialog.configure(bg=WHITE)
        dialog.transient(self)
        dialog.grab_set()
        tabs = ttk.Notebook(dialog)
        tabs.pack(fill="both", expand=True, padx=16, pady=16)
        panel = tk.Frame(tabs, bg=WHITE, padx=20, pady=16)
        map_panel = tk.Frame(tabs, bg=WHITE, padx=20, pady=16)
        tabs.add(panel, text="DeepSeek / 模型")
        tabs.add(map_panel, text="地图与路径 API")
        self._label(panel, "连接你的模型", size=18, bold=True).pack(anchor="w")
        self._label(panel, "兼容 DeepSeek、OpenAI 和本地服务。", size=10, color=MUTED).pack(anchor="w", pady=(6, 14))
        fields = {}
        for label, name, initial in (("接口地址", "base_url", self.llm.base_url),
                                      ("模型名称", "model", self.llm.model),
                                      ("密钥环境变量", "api_key_env", self.llm.api_key_env),
                                      ("API Key（可选）", "api_key", self.api_key.get())):
            self._label(panel, label, size=9, bold=True).pack(anchor="w", pady=(8, 5))
            var = tk.StringVar(value=initial)
            fields[name] = var
            entry = ttk.Entry(panel, textvariable=var, show="●" if name == "api_key" else "")
            entry.pack(fill="x")
        self._label(panel, "返回格式", size=9, bold=True).pack(anchor="w", pady=(12, 5))
        response_format = tk.StringVar(value=self.llm.response_format)
        ttk.Combobox(panel, textvariable=response_format, values=["json_object", "structured"], state="readonly").pack(fill="x")
        self.remember_connection = tk.BooleanVar(value=True)
        ttk.Checkbutton(panel, text="保存接口和密钥到本机，下次自动读取", variable=self.remember_connection).pack(anchor="w", pady=(12, 4))
        protection = "Windows 当前用户加密保存" if credentials._WINDOWS else "用户目录保存，密钥为明文，权限仅当前用户"
        self._label(panel, "DeepSeek 选 json_object。优先级：本次输入 > 环境变量 > 本地保存。\n" + protection,
                    size=8, color=MUTED, justify="left", wraplength=540).pack(anchor="w", pady=(4, 6))

        def apply():
            try:
                llm, map_settings, routing_settings, key = validate_settings_form(
                    {name: var.get().strip() for name, var in fields.items()}, response_format.get(),
                    {name: var.get().strip() for name, var in map_fields.items()},
                    self.llm.timeout_seconds, self.routing_settings.timeout_seconds)
                if self.remember_connection.get():
                    credentials.save_connection(llm.model_dump(), map_settings.model_dump(), routing_settings.model_dump(), key)
                else:
                    credentials.clear_connection()
            except (ValueError, credentials.LocalSettingsError) as exc:
                messagebox.showerror("请检查接口设置", settings_error_message(exc), parent=dialog)
                return
            map_changed = self.map_settings != map_settings
            route_changed = self.routing_settings != routing_settings
            self.llm, self.map_settings, self.routing_settings = llm, map_settings, routing_settings
            self.api_key.set(key)
            self.api_key_is_saved = False  # Applying the form is an explicit session choice.
            self._connection_status()
            if map_changed:
                self.map_view.configure_map(self.map_settings, self.settings.cache_dir / "tiles")
            if route_changed:
                self.generation += 1
                self.road_route = None
                self.draw_map()
                self.route_summary.set("路径接口已更新，请重新规划路线。")
            self.status.set("接口已保存到本机，下次启动自动读取。" if self.remember_connection.get() else
                            "接口仅在本次使用，已移除本地保存。")
            dialog.destroy()

        def clear_saved():
            try:
                credentials.clear_connection()
            except credentials.LocalSettingsError as exc:
                messagebox.showerror("无法清除", str(exc), parent=dialog)
                return
            fields['api_key'].set('')
            self.api_key.set('')
            self.api_key_is_saved = False
            self.remember_connection.set(False)
            self._connection_status()
            self.status.set("已清除本地保存的接口和密钥；环境变量不受影响。")

        self.clear_saved_button = self._button(panel, "清除本地保存的接口和密钥", clear_saved)
        self.clear_saved_button.pack(fill="x", pady=(0, 4))
        self.apply_settings_button = self._button(panel, "应用所有设置", apply, primary=True)
        self.apply_settings_button.pack(fill="x", pady=(4, 0))

        self._label(map_panel, "地图与道路路线", size=18, bold=True).pack(anchor="w", pady=(0, 14))
        map_fields = {}
        for label, name, value in (("底图 XYZ URL（Web Mercator）", "tile_url", self.map_settings.tile_url),
                                    ("底图署名", "attribution", self.map_settings.attribution),
                                    ("User-Agent", "user_agent", self.map_settings.user_agent),
                                    ("底图坐标系（wgs84 / gcj02）", "coordinate_system", self.map_settings.coordinate_system),
                                    ("Valhalla 路径 API 地址", "endpoint", self.routing_settings.endpoint)):
            self._label(map_panel, label, size=9, bold=True).pack(anchor="w", pady=(12, 5))
            var = tk.StringVar(value=value)
            map_fields[name] = var
            if name == "coordinate_system":
                ttk.Combobox(map_panel, textvariable=var, values=["wgs84", "gcj02"], state="readonly").pack(fill="x")
            else:
                ttk.Entry(map_panel, textvariable=var).pack(fill="x")
        self._label(map_panel, "默认配置使用高德底图，需要联网。底图 URL 必须包含 {z}/{x}/{y}。\n"
                    "高德底图使用 gcj02，其他 WGS84 底图请选择 wgs84；不要混用坐标系。\n"
                    "路径按真实道路计算，支持步行、骑行和驾车。\n\n"
                    "点击“获取设备位置”会读取 Windows 定位服务，来源可能是 GPS\n"
                    "或 Wi-Fi。不可用时仍可手动输入 WGS84 经纬度。",
                    size=9, color=MUTED, justify="left", wraplength=490).pack(anchor="w", pady=18)

        self._button(map_panel, "应用所有设置", apply, primary=True).pack(fill="x", pady=8)

    def start_demo(self):
        if self.busy:
            return
        self.query.set("我想喝咖啡")
        self.submit(demo=True)

    def start_trip_demo(self):
        if not self.busy:
            self.query.set("我想先去喝咖啡然后去健身")
            self.submit(demo=True, demo_trip=True)

    def submit(self, demo=False, demo_trip=False):
        if self.busy:
            return
        try:
            center = (float(self.longitude.get()), float(self.latitude.get()))
            radius, limit = float(self.radius.get()), int(self.limit.get())
            g.build_query("validation", center, radius, limit)
            query = self.query.get().strip()
            if not query or len(query) > 4000:
                raise ValueError("请输入 1–4000 个字符的查询。")
            if not demo and not (self.api_key.get().strip() or os.getenv(self.llm.api_key_env)):
                self.status.set("请在“接口设置”中填写 API Key，也可以先体验咖啡示例。")
                self.open_settings()
                return
            request = QueryRequest(query, center, radius, limit, self.config_path,
                                   "" if self.api_key_is_saved else self.api_key.get().strip(),
                                   self.llm.model_copy(deep=True), demo,
                                   self.preview_only.get(), planning=True, demo_trip=demo_trip)
        except ValueError as exc:
            messagebox.showerror("请检查查询条件", str(exc), parent=self)
            return
        self.generation += 1
        self.result = None
        for widget in self.stop_controls.winfo_children():
            widget.destroy()
        self.stop_combos = []
        self.road_route = None
        self._clear_parking()
        self.active_stop = 0
        self.table.delete(*self.table.get_children())
        self.result_title.configure(text="正在寻找附近的地点…")
        self.result_subtitle.configure(text="")
        self.notice.configure(text="示例模式：固定解析意图，使用真实数据查询。" if demo else "")
        self.route_summary.set("正在解析需求并选择地点…")
        self._set_text(self.navigation, "道路路线尚未生成。")
        self._set_text(self.explanation, "查询进行中，理解过程将在这里展示。")
        self._set_text(self.sql_view, "查询进行中…")
        self._set_busy(True)
        self.status.set("正在准备查询…")
        self.draw_map()
        self._queue_job(("query", request))

    def _set_busy(self, value):
        self.busy = value
        self.preview.configure(state="disabled" if value else "normal")
        self.auto_route_toggle.configure(state="disabled" if value else "normal")
        for button in (self.search_button, self.demo_button, self.trip_demo_button, self.settings_button,
                       self.config_button, self.center_button, self.import_button):
            button.configure(state="disabled" if value else "normal")
        self.locate_button.configure(state="disabled" if value or self.locating else "normal")
        ready = bool(self.result and self.result.stops and all(s.selected for s in self.result.stops))
        self.route_button.configure(state="normal" if not value and ready else "disabled")
        self.parking_toggle.configure(state="disabled" if value else "normal")
        self.parking_radius_entry.configure(state="disabled" if value else "normal")
        self.parking_button.configure(state="normal" if not value and ready and self.parking_enabled.get() else "disabled")
        self.parking_combo.configure(state="readonly" if not value and self.parking_enabled.get() and self.parking_options and self.parking_options.candidates else "disabled")
        self.choose_button.configure(state="normal" if not value and self.result and not self.result.request.dry_run else "disabled")
        for index, combo in enumerate(self.stop_combos):
            enabled = (not value and self.result and index < len(self.result.stops)
                       and self.result.stops[index].candidates and not self.result.request.dry_run)
            combo.configure(state="readonly" if enabled else "disabled")
        self.mode_combo.configure(state="disabled" if value or self.parking_enabled.get() else "readonly")
        self.export_button.configure(state="normal" if not value and self.result and any(s.selected for s in self.result.stops) else "disabled")
        if value:
            self.progress.place(x=0, y=0, relwidth=1, height=3)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.place_forget()

    def _queue_job(self, job):
        self.generation += 1
        self.jobs.put((self.generation, *job))

    def _work(self):
        engine = QueryEngine()
        try:
            while True:
                job = self.jobs.get()
                if job is None:
                    return
                generation, kind, request = job
                def emit(kind, value):
                    self.events.put((generation, kind, value))
                try:
                    progress = lambda status: emit("progress", status)
                    if kind == "query":
                        result = engine.run(request, progress)
                        emit("result", result)
                    elif kind == "reselect":
                        result = engine.reselect(*request, progress=progress)
                        emit("reselected", result)
                    elif kind == "route":
                        result, mode, routing, parking, parking_index = request
                        progress("正在请求真实道路路线…")
                        points = parking_route_points(parking, parking_index) if parking else route_points(result)
                        route = plan_road_route(points, "driving" if parking else mode, routing)
                        emit("route", (result, route, parking, parking_index))
                    elif kind == "parking":
                        result, radius = request
                        emit("parking", engine.find_parking(result, radius, progress))
                    elif kind == "import":
                        from geoask_data import import_region
                        source, destination, base, options = request
                        imported = import_region(source, destination, base, **options)
                        engine.close()
                        emit("imported", imported)
                except Exception as exc:
                    get_logger().exception("Worker operation failed (%s)", kind)
                    errors = {"route": "route_error", "parking": "parking_error", "import": "import_error", "reselect": "reselect_error"}
                    emit(errors.get(kind, "error"), str(exc))
        finally:
            engine.close()


    def _poll(self):
        if self.closed:
            return
        try:
            drain_events(self.events, self._handle_event, self._event_error)
        finally:
            if not self.closed:
                self.after(80, self._poll)

    def _event_error(self):
        self._set_busy(False)
        self.status.set("界面处理出错，已恢复操作。详情见用户缓存目录 logs/geoask.log，可重新查询。")

    def report_callback_exception(self, exc_type, exc_value, traceback):
        get_logger().error("Tk callback failed", exc_info=(exc_type, exc_value, traceback))
        self._event_error()

    def _handle_event(self, event):
        if len(event) == 3:
            generation, kind, value = event
            if generation != self.generation:
                return
        else:
            kind, value = event
        if kind == "progress":
            self.status.set(value)
        elif kind == "imported":
            path, count, named = value
            self._load_config(path)
            self.status.set(f"已导入 {count} 个地点（{named} 个有名称/别名），请输入该地区的目的地。")
        elif kind == "import_error":
            self._set_busy(False)
            self.status.set("导入未完成，当前地区数据已保留。")
            messagebox.showerror("地区数据无法导入", value, parent=self)
        elif kind in ("result", "reselected"):
            self.road_route = None
            self._clear_parking()
            self._show_result(value)
            self._set_busy(False)
            if self.auto_route.get() and not value.request.dry_run and all(s.selected for s in value.stops):
                self.plan_route()
        elif kind == "route":
            result, route, parking, parking_index = value
            if result is self.result:
                self.route_parking = parking.candidates[parking_index] if parking else None
                self.road_route = route
                self._show_route(route)
            self._set_busy(False)
        elif kind == "parking":
            if value.result is self.result:
                self.parking_options = value
                self.parking_index = None
                choices = [f"{i+1}. " + (row['name'] or f"未命名停车场 ({row['longitude']:.5f}, {row['latitude']:.5f})")
                           + f" · 距目的地 {distance_label(row['dist_m'])}"
                           for i, row in enumerate(value.candidates)]
                self.parking_combo.configure(values=choices)
                self.parking_combo.set("请选择停车场后规划路线" if choices else "当前范围内没有汽车停车场")
                self.parking_message.set(value.summary + " 距离为直线距离；请核对开放和准入信息" if choices else
                                         "没有匹配数据，可增大半径或切换地区数据")
                self.route_summary.set("已找到停车场候选，请选择后点击“规划路线”。" if choices else
                                       "目的地附近未找到汽车停车场；可增大半径重查或关闭停车模式。")
                self.draw_map()
                self.map_view.fit([value.result.request.center, *[(r['longitude'],r['latitude']) for r in value.candidates],
                                   (value.destination['longitude'], value.destination['latitude'])])
            self._set_busy(False)
        elif kind == "parking_error":
            self._set_busy(False)
            self.parking_message.set(value)
            self.route_summary.set("停车场查询未完成：" + value)
        elif kind == "route_error":
            self._set_busy(False)
            self.route_summary.set(value)
            self._set_text(self.navigation, value + "\n没有用直线或其他出行方式冒充道路路线。")
            self.status.set("路线暂未生成，地点选择已保留，可稍后重试。")
        elif kind in ("location", "location_error"):
            self.locating = False
            self.locate_button.configure(state="disabled" if self.busy else "normal", text="获取设备位置")
            original, payload = value
            if kind == "location_error":
                self.position_source.set(payload)
                self.status.set("设备定位暂不可用，请手动输入坐标或在地图上右键选点。")
            elif (self.longitude.get(), self.latitude.get()) != original:
                self.status.set("设备定位已返回，但你已经手动修改坐标，未覆盖输入。")
            elif self.busy:
                self.status.set("设备位置已返回，当前查询使用原起点；请完成查询后重新获取位置。")
            else:
                self._apply_origin(payload.longitude, payload.latitude)
                accuracy = f"，精度约 {payload.accuracy_m:.0f} 米" if payload.accuracy_m is not None else "，精度未知"
                self.position_source.set("Windows 设备定位（GPS / Wi-Fi）" + accuracy)
                self.status.set("已更新查询起点；请重新查询以使用当前位置。")
        elif kind == "reselect_error":
            self._set_busy(False)
            self.notice.configure(text="更换地点未完成：" + value)
            self.status.set("更换地点失败，已保留原行程，可重试或重新查询。")
            self.route_summary.set("已保留原行程地点；请重试换点，或重新规划原行程路线。")
        elif kind == "error":
            self._set_busy(False)
            self.result_title.configure(text="这次查询没有完成")
            self.notice.configure(text=value)
            self._set_text(self.explanation, value)
            self._set_text(self.sql_view, "没有生成查询。")
            self.status.set("查询未完成，请检查接口、网络或数据配置后重试。")
            self.route_summary.set("本次地点查询未完成，请检查条件后重试。")

    def _show_result(self, result):
        self.result = result
        self.active_stop = min(self.active_stop, len(result.stops)-1)
        for widget in self.stop_controls.winfo_children():
            widget.destroy()
        self.stop_combos = []
        explanations, queries = [], []
        for i, stop in enumerate(result.stops):
            row = tk.Frame(self.stop_controls, bg=WHITE)
            row.pack(fill="x", pady=4)
            label = stop.intent.place_name or stop.intent.intent
            self._label(row, f"{i+1:02d}  {label}", size=10, bold=True, width=12, anchor="w").pack(side="left")
            choices = [f"{j+1}. {poi['name'] or '未命名地点'} · {distance_label(poi['dist_m'])}"
                       for j, poi in enumerate(stop.candidates)]
            combo = ttk.Combobox(row, values=choices or [stop.blocked_reason or "仅预览，尚未选点"], state="readonly")
            combo.pack(side="left", fill="x", expand=True, padx=8)
            combo.current(stop.selected_index if stop.selected_index is not None else 0)
            if stop.candidates and stop.selected_index is None:
                combo.set("请选择具体地点")
            combo.bind("<<ComboboxSelected>>", lambda event, index=i, box=combo: self.reselect_stop(index, box.current()))
            self.stop_combos.append(combo)
            self._button(row, "查看候选", lambda index=i: self.show_candidates(index)).pack(side="right")
            resolution = (f"具体目的地：{stop.intent.place_name}；活动/类别：{stop.intent.intent}\n"
                          "名称/别名匹配：当前数据 + 配置的地名目录；不受附近半径限制\n"
                          if stop.intent.place_name else
                          (f"类别匹配不足，已改用名称/关键词搜索：{stop.intent.intent}\n"
                           f"原类别候选：{stop.match.best_candidate}，相似度 {stop.match.similarity:.3f}\n"
                           if stop.resolution == "name-fallback" else
                           f"语义映射：{stop.match.best_candidate}，相似度 {stop.match.similarity:.3f}\n")
                          + f"搜索半径：{result.request.radius:g} 米\n")
            if stop.intent.place_candidates:
                resolution += "模型提供的名称候选（需本地核对）：" + "、".join(stop.intent.place_candidates) + "\n"
            if stop.candidates and (stop.intent.place_name or stop.resolution == "name-fallback"):
                resolution += "匹配方式：" + {0: "数据名称/别名", 1: "模型名称候选，需确认", 2: "名称包含，需确认"}[stop.candidates[0]['name_rank']] + "\n"
            explanations.append(f"第 {i+1} 站：{label}\n" + resolution +
                                f"距离参考中心：{stop.center}\n"
                                + (f"已选：{stop.selected['name'] or '未命名地点'}" if stop.selected else stop.blocked_reason or "查询预览，未执行"))
            queries.append(f"-- 第 {i+1} 站\n{stop.sql or stop.blocked_reason}\n"
                           + ("绑定参数：" + json.dumps(stop.params, ensure_ascii=False) if stop.sql else ""))
        self._set_text(self.explanation, f"按顺序理解为：{' → '.join(s.intent.place_name or s.intent.intent for s in result.stops)}\n"
                       f"自然语言出行方式：{MODE_NAMES[result.travel_mode]}\n当前数据：{self.settings.geojson_path.name}（{result.data_count} 个类别 POI）\n\n"
                       + "\n\n".join(explanations) + "\n\n" + result.note)
        self._set_text(self.sql_view, "\n\n".join(queries))
        self.route_summary.set(result.note or "地点已选定，可以规划道路路线。")
        self._render_candidates()
        if result.request.dry_run:
            self.main_tabs.select(2)
            self.notebook.select(1)
        else:
            self.main_tabs.select(0)
        self.status.set(result_status(result))
        self.draw_map()
        points = result_map_points(result, self.active_stop)
        self.map_view.fit(points)

    def _render_candidates(self):
        self.table.delete(*self.table.get_children())
        if not self.result or not self.result.stops:
            return
        stop = self.result.stops[self.active_stop]
        for i, row in enumerate(stop.candidates, 1):
            self.table.insert("", "end", iid=str(i), values=(i, row["name"] or "未命名地点",
                              CATEGORY_NAMES.get(row["category"], row["category"]), distance_label(row["dist_m"])),
                              tags=("alternate",) if i % 2 == 0 else ())
        if stop.selected_index is not None:
            self.table.selection_set(str(stop.selected_index+1))
        self.result_title.configure(text=f"第 {self.active_stop+1} 站 · {stop.intent.place_name or stop.intent.intent} · {len(stop.candidates)} 个候选")
        self.result_subtitle.configure(text="距前一站 / 起点的直线距离")
        scope = ("名称来自当前数据及配置的地名目录（OSM）；未找到时请补充完整名称或切换区域数据。"
                 if stop.intent.place_name else f"附近类别查询使用当前 POI 文件：{self.settings.geojson_path.name}。")
        self.notice.configure(text=(stop.blocked_reason or self.result.note) + "\n" + scope)

    def show_candidates(self, index):
        if not self.busy and self.result:
            self.active_stop = index
            self._render_candidates()
            self.main_tabs.select(1)
            self.draw_map()
            self.map_view.fit(result_map_points(self.result, self.active_stop))

    def choose_candidate(self):
        selected = self.table.selection()
        if selected:
            self.reselect_stop(self.active_stop, int(selected[0])-1)

    def reselect_stop(self, index, candidate):
        if self.busy or not self.result or not self.result.stops[index].candidates:
            return
        self.active_stop = index
        self.road_route = None
        self._clear_parking()
        self.route_summary.set("正在更换地点并更新后续站点，旧路线已清除。")
        self._set_text(self.navigation, "地点已改变，等待重新规划。")
        self.draw_map()
        self._set_busy(True)
        self._queue_job(("reselect", (self.result, index, candidate)))

    def _clear_parking(self):
        self.parking_options = None
        self.parking_index = None
        self.route_parking = None
        self.parking_combo.configure(values=[], state="disabled")
        self.parking_combo.set("")
        if self.parking_enabled.get():
            self.parking_choices.pack(fill="x", padx=16, pady=(0,6), before=self.route_summary_label)
        else:
            self.parking_choices.pack_forget()
        self.parking_message.set("停车模式将驾车路线终点改为最后一站附近停车场" if self.parking_enabled.get() else
                                 "可选：驾车到最后一站附近的停车场")

    def parking_changed(self):
        if self.busy:
            return
        self._clear_parking()
        if self.parking_enabled.get():
            self.mode_before_parking = self.travel_mode.get()
            self.travel_mode.set("驾车")
        elif self.mode_before_parking is not None:
            self.travel_mode.set(self.mode_before_parking)
            self.mode_before_parking = None
        self.road_route = None
        self._set_text(self.navigation, "停车模式已改变，请重新规划路线。")
        self.draw_map()
        self._set_busy(False)
        if self.parking_enabled.get():
            self.find_parking()
        else:
            self.route_summary.set("已关闭停车模式，可按所选出行方式导航到原目的地。")
            if self.auto_route.get():
                self.plan_route()

    def find_parking(self):
        if self.busy or not self.parking_enabled.get():
            return
        try:
            if not self.result:
                raise g.GeoaskError("请先查询并选定每一站地点，再查找停车场。")
            route_points(self.result)
            radius = float(self.parking_radius.get())
            if not math.isfinite(radius) or not 0 < radius <= 5000:
                raise g.GeoaskError("停车场半径应在 (0,5000] 米内。")
        except (ValueError, g.GeoaskError) as exc:
            self.parking_message.set(str(exc))
            return
        self._clear_parking()
        self.road_route = None
        self.draw_map()
        self._set_text(self.navigation, "正在查找最后一站附近停车场，旧路线已清除。")
        self.route_summary.set("正在查找最后一站附近停车场…")
        self._set_busy(True)
        self._queue_job(("parking", (self.result, radius)))

    def select_parking(self, event=None):
        if self.busy or not self.parking_options:
            return
        index = self.parking_combo.current()
        if not 0 <= index < len(self.parking_options.candidates):
            return
        self.parking_index = index
        self.route_parking = None
        self.road_route = None
        row = self.parking_options.candidates[index]
        self.route_summary.set(f"已选：{row['name'] or '未命名停车场'}；点击“规划路线”驾车前往。")
        self._set_text(self.navigation, "停车场已改变，请重新规划驾车路线。")
        self.draw_map()

    def plan_route(self):
        if self.busy or not self.result:
            return
        try:
            route_points(self.result)
        except g.GeoaskError as exc:
            self.route_summary.set(str(exc))
            return
        mode = UI_MODES.get(self.travel_mode.get(), self.result.travel_mode)
        parking = None
        if self.parking_enabled.get():
            try:
                requested_radius = float(self.parking_radius.get())
            except ValueError:
                self.find_parking()
                return
            if (self.parking_options is None or self.parking_options.result is not self.result
                    or self.parking_options.radius != requested_radius):
                self.find_parking()
                return
            if self.parking_index is None:
                self.route_summary.set("请先选择一个停车场，或关闭停车模式后规划原路线。")
                return
            parking = self.parking_options
            mode = "driving"
        self.road_route = None
        self.route_parking = None
        self.draw_map()
        self._set_busy(True)
        self.route_summary.set("正在规划驾车到停车场的道路路线…" if parking else f"正在规划{MODE_NAMES[mode]}路线，按你指定的站点顺序行进…")
        self._queue_job(("route", (self.result, mode, self.routing_settings.model_copy(deep=True), parking, self.parking_index)))

    def mode_changed(self):
        if not self.busy:
            self.road_route = None
            self.route_parking = None
            self.draw_map()
            self.route_summary.set("出行方式已改变，请重新规划路线。")
            self._set_text(self.navigation, "出行方式已改变，旧路线已清除。请重新规划。")
            if self.auto_route.get():
                self.plan_route()

    def _show_route(self, route):
        duration = max(1, round(route.duration_s/60))
        self.route_summary.set(f"{MODE_NAMES[route.travel_mode]} · {distance_label(route.distance_m)} · 预计 {duration} 分钟"
                               f" · {len(route.legs)} 段道路路线（不含停留时间）")
        if self.route_parking:
            parking = self.route_parking
            self.route_summary.set(self.route_summary.get() + f"\n终点：{parking['name'] or '未命名停车场'} · 距原目的地直线 {distance_label(parking['dist_m'])}")
        sections = [self.route_summary.get(), f"服务：{route.provider}\n中文指引：{route.instruction_source}\n按用户顺序规划，默认逐站选最近候选，不保证全程最短。\n"
                    "地点代表点会吸附到可通行道路，可能不是实际入口。"]
        if self.route_parking:
            sections.append("停车模式：保留前面各站，最后一段驾车到已选停车场。原目的地：" +
                            (self.result.stops[-1].selected['name'] or '未命名地点') +
                            f"。停车后至目的地直线距离 {distance_label(self.route_parking['dist_m'])}，不是步行路线。\n"
                            "数据不提供实时余位、收费、开放时间或准入保证；停车场代表点未必是车辆入口。")
        for i, leg in enumerate(route.legs):
            stop = self.result.stops[i]
            name = stop.selected["name"] or "未命名地点"
            if self.route_parking and i == len(route.legs)-1:
                name = (self.route_parking['name'] or '未命名停车场') + '（停车场）'
            sections.append(f"第 {i+1} 段 → {name}\n{distance_label(leg.distance_m)} · 约 {max(1, round(leg.duration_s/60))} 分钟\n"
                            + "\n".join(leg.instructions))
        self._set_text(self.navigation, "\n\n".join(sections))
        self.status.set("道路路线已生成，可在“理解与导航 → 分段导航”查看指引。")
        self.draw_map()
        visible_points = [self.result.request.center, *route.coordinates]
        if self.route_parking:
            destination = self.result.stops[-1].selected
            visible_points.append((destination['longitude'], destination['latitude']))
        self.map_view.fit(visible_points)

    def draw_map(self):
        markers = []
        if self.result:
            markers.append({"point": self.result.request.center, "kind": "origin", "label": "起"})
            if self.result.stops:
                stop = self.result.stops[self.active_stop]
                selected = self.table.selection()
                for i, row in enumerate(stop.candidates, 1):
                    markers.append({"point": (row["longitude"], row["latitude"]), "kind": "candidate",
                                    "id": str(i), "active": str(i) in selected})
                for i, stop in enumerate(self.result.stops, 1):
                    if stop.selected:
                        markers.append({"point": (stop.selected["longitude"], stop.selected["latitude"]),
                                        "kind": "stop", "label": str(i)})
                if self.parking_enabled.get() and self.parking_options and self.parking_options.result is self.result:
                    for i, row in enumerate(self.parking_options.candidates):
                        if self.parking_index == i:
                            markers.append({"point": (row['longitude'],row['latitude']), "kind":"stop", "label":"P"})
        else:
            try:
                point = float(self.longitude.get()), float(self.latitude.get())
                g.validate_location(*point)
                markers.append({"point": point, "kind": "origin", "label": "起"})
            except ValueError:
                pass
        self.map_view.markers = markers
        self.map_view.route = self.road_route.coordinates if self.road_route else []
        self.map_view.redraw()
        self.marker_positions = list(self.map_view.marker_positions)

    def select_map_marker(self, iid):
        if self.table.exists(iid):
            self.table.selection_set(iid)
            self.table.see(iid)
            self.main_tabs.select(1)

    def _apply_origin(self, lon, lat):
        self.longitude.set(f"{lon:.7f}")
        self.latitude.set(f"{lat:.7f}")
        self.map_view.set_center(lon, lat, zoom=15)
        # Keep any previous result anchored to its captured origin until a new query.
        if not self.result:
            self.draw_map()

    def map_origin(self, lon, lat):
        if not self.busy:
            self._apply_origin(lon, lat)
            self.position_source.set("起点已通过地图选取 · WGS84")
            self.status.set("起点已改变，请重新查询以使用新起点。")

    def center_map(self):
        try:
            lon, lat = float(self.longitude.get()), float(self.latitude.get())
            g.validate_location(lon, lat)
        except ValueError as exc:
            messagebox.showerror("坐标无效", str(exc), parent=self)
            return
        self.map_view.set_center(lon, lat, zoom=15)
        self.main_tabs.select(0)
        self.position_source.set("手动输入的 WGS84 坐标")
        self.draw_map()

    def locate(self):
        if self.locating or self.busy:
            return
        self.locating = True
        self.locate_button.configure(state="disabled", text="正在定位…")
        self.position_source.set("正在请求 Windows 设备定位，最长约 30 秒…")
        original = self.longitude.get(), self.latitude.get()

        def worker():
            try:
                position = get_device_location()
                self.events.put(("location", (original, position)))
            except Exception as exc:
                get_logger().exception("Device location failed")
                self.events.put(("location_error", (original, str(exc))))
        Thread(target=worker, daemon=True, name="geoask-location").start()

    def export(self):
        if not self.result or not self.result.rows:
            return
        path = filedialog.asksaveasfilename(parent=self, title="导出查询结果", defaultextension=".csv",
                                           initialfile="geoask_附近地点.csv", filetypes=[("CSV 表格", "*.csv")])
        if path:
            try:
                export_csv(Path(path), self.result)
            except OSError as exc:
                messagebox.showerror("无法导出", str(exc), parent=self)
                return
            self.status.set(f"已导出已选择的行程地点到 {Path(path).name}。")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.api_key.set("")
        self.map_view.close()
        self.jobs.put(None)
        self.destroy()


def main():
    app = GeoaskApp()
    app.mainloop()


if __name__ == "__main__":
    main()
