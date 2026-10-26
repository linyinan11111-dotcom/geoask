"""Review regressions: no display, paid API, model download or network needed."""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from queue import Queue
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import numpy as np
from PIL import Image
import pytest
from click.testing import CliRunner

import geoask as g
import geoask_gui as gui
import geoask_map as maps
import geoask_location as location
import geoask_runtime as runtime
from geoask_data import import_region
from geoask_service import PlannedStop, QueryEngine, QueryRequest, QueryResult


def named_result():
    intent = g.GeoIntent(intent="咖啡", modifiers=[], place_name="星巴克")
    rows = [{"name": "星巴克", "category": "cafe", "longitude": 116.46 + .01*i,
             "latitude": 39.92 + .001*i, "dist_m": 800+i*70} for i in range(1, 11)]
    stop = PlannedStop(intent, g.CategoryMatch(None, "按名称", 0, 0), rows, (116.46, 39.92))
    request = QueryRequest("去星巴克", (116.46, 39.92), 1000, 10, planning=True)
    return QueryResult(request, intent, stop.match, [], rows, "", [], 10, [stop])


def map_stub():
    stub = NS(winfo_width=lambda: 800, winfo_height=lambda: 500)
    def set_center(lon, lat, zoom):
        stub.center, stub.zoom = (lon, lat), zoom
    stub.set_center = set_center
    return stub


def test_unresolved_candidates_fit_view_and_prompt_selection():
    result = named_result()
    points = gui.result_map_points(result, 0)
    assert len(points) == len(result.rows) + 1
    assert "请选择具体地点" in gui.result_status(result)
    stub = map_stub()
    maps.SlippyMap.fit(stub, points)
    for point in points:
        x, y = maps.SlippyMap.screen_point(stub, *point)
        assert 30 <= x <= 770 and 30 <= y <= 470
    selected = replace(result, stops=[replace(result.stops[0], selected_index=0)])
    assert "选择已完成" in gui.result_status(selected)
    assert "预览" in gui.result_status(replace(result, request=replace(result.request, dry_run=True)))


def test_single_point_fit_preserves_neighborhood_context():
    stub = map_stub()
    maps.SlippyMap.fit(stub, [(116.46, 39.92)]*2)
    assert 14 <= stub.zoom <= 16


def test_gui_handler_failure_recovers_busy_and_keeps_polling(caplog):
    fake = NS(closed=False, busy=True, events=Queue(), after=MagicMock(), _poll=MagicMock())
    fake.status = NS(set=MagicMock())
    fake._set_busy = lambda value: setattr(fake, "busy", value)
    fake._event_error = lambda: gui.GeoaskApp._event_error(fake)
    handled = []
    def handle(event):
        if event[0] == "bad":
            raise RuntimeError("injected display error")
        handled.append(event)
    fake._handle_event = handle
    fake.events.put(("bad", None))
    fake.events.put(("progress", "still running"))
    gui.GeoaskApp._poll(fake)
    assert not fake.busy and handled == [("progress", "still running")]
    assert fake.after.call_args.args[0] == 80
    assert "Traceback" in caplog.text and "injected display error" in caplog.text
    fake.events.put(("progress", "next tick"))
    gui.GeoaskApp._poll(fake)
    assert handled[-1][1] == "next tick" and fake.after.call_count == 2


def test_poll_finally_schedules_even_queue_failure():
    fake = NS(closed=False, events=NS(get_nowait=MagicMock(side_effect=RuntimeError("queue"))),
              after=MagicMock(), _poll=MagicMock(), _handle_event=MagicMock(), _event_error=MagicMock())
    with pytest.raises(RuntimeError):
        gui.GeoaskApp._poll(fake)
    fake.after.assert_called_once()


def test_obsolete_generation_ignored():
    fake = NS(generation=2, status=NS(set=MagicMock()))
    gui.GeoaskApp._handle_event(fake, (1, "progress", "old"))
    fake.status.set.assert_not_called()
    gui.GeoaskApp._handle_event(fake, (2, "progress", "new"))
    fake.status.set.assert_called_once_with("new")


def test_failed_query_keeps_stale_selectors_disabled():
    widget = lambda: NS(configure=MagicMock())
    names = ("search_button demo_button trip_demo_button settings_button config_button center_button import_button "
             "locate_button route_button parking_toggle parking_radius_entry parking_button parking_combo "
             "choose_button mode_combo export_button preview auto_route_toggle").split()
    fake = NS(**{name: widget() for name in names}, result=None, locating=False,
              parking_enabled=NS(get=lambda: False), stop_combos=[widget()], progress=MagicMock())
    gui.GeoaskApp._set_busy(fake, False)
    fake.stop_combos[0].configure.assert_called_with(state="disabled")
    fake.search_button.configure.assert_called_with(state="normal")


def test_settings_apply_validates_all_tabs_together():
    fields = dict(base_url="https://api.deepseek.com", model="deepseek-chat",
                  api_key_env="DEEPSEEK_API_KEY", api_key="session-only-test")
    maps_form = g.MapSettings().model_dump() | {"endpoint": "https://example.org/route"}
    llm, tiles, route, key = gui.validate_settings_form(fields, "json_object", maps_form, 30, 20)
    assert key == fields["api_key"] and route.endpoint == maps_form["endpoint"]
    assert llm.model == fields["model"] and tiles.tile_url == maps_form["tile_url"]
    with pytest.raises(ValueError):
        gui.validate_settings_form(fields, "json_object", maps_form | {"tile_url": "bad"}, 30, 20)


def png():
    output = BytesIO()
    Image.new("RGB", (256, 256), "green").save(output, format="PNG")
    return output.getvalue()


def test_tile_cache_write_failure_keeps_valid_download(tmp_path, monkeypatch):
    data = png()
    @contextmanager
    def denied(*a, **kw):
        raise PermissionError("read-only cache")
        yield
    monkeypatch.setattr(maps, "atomic_output", denied)
    assert maps.fetch_tile(tmp_path / "tile.png", g.MapSettings(), 1, 0, 0,
                           lambda *a, **kw: BytesIO(data)) == data


def test_corrupt_cached_tile_is_refetched(tmp_path):
    path = tmp_path / "tile.png"
    path.write_bytes(b"bad image")
    data = png()
    assert maps.fetch_tile(path, g.MapSettings(), 1, 0, 0, lambda *a, **kw: BytesIO(data)) == data
    assert path.read_bytes() == data


def test_bad_tile_does_not_stop_map_poll(monkeypatch):
    fake = NS(closed=False, generation=1, completed=Queue(), pending={(1, 1, 0, 0), (1, 1, 1, 0)},
              photos=OrderedDict(), tiles_loaded=0, tile_error="", failed={}, redraw=MagicMock(), after=MagicMock(), _poll=MagicMock())
    fake._handle_tile = lambda event: maps.SlippyMap._handle_tile(fake, event)
    fake._tile_error = lambda: maps.SlippyMap._tile_error(fake)
    monkeypatch.setattr(maps.ImageTk, "PhotoImage", lambda *a, **kw: "photo")
    fake.completed.put(((1, 1, 0, 0), b"bad"))
    fake.completed.put(((1, 1, 1, 0), png()))
    maps.SlippyMap._poll(fake)
    assert fake.tiles_loaded == 1 and not fake.pending
    assert fake.photos[(1, 1, 1, 0)] == "photo"
    fake.after.assert_called_once()
    fake.redraw.assert_called_once()


def test_tile_prune_uses_expiry_and_size_budget(tmp_path):
    for i in range(3):
        path = tmp_path / f"{i}.png"
        path.write_bytes(b"abc")
        os.utime(path, (100+i, 100+i))
    maps.prune_tile_cache(tmp_path, max_age=float("inf"), max_bytes=4)
    assert [p.name for p in tmp_path.iterdir()] == ["2.png"]


class Embedder:
    def embed(self, texts):
        return [np.array([1., 0., 0.]) if t != "park" else np.array([0., 1., 0.]) for t in texts]


@pytest.mark.parametrize("corruption", ["empty", "zip", "dimensions", "rows", "nan", "zero"])
def test_vector_cache_rebuilds_all_corrupt_forms(tmp_path, corruption):
    settings = g.Settings(cache_dir=tmp_path, embedding_model="test")
    mapper = g.SemanticMapper(["cafe", "park"], settings, Embedder())
    cache = mapper.cache
    if corruption == "empty":
        cache.write_bytes(b"")
    elif corruption == "zip":
        with cache.open("wb") as output:
            np.savez(output, accidental=np.ones((2, 3)))
    else:
        value = {"dimensions": np.ones((2, 4)), "rows": np.ones((3, 3)),
                 "nan": np.full((2, 3), np.nan), "zero": np.zeros((2, 3))}[corruption]
        np.save(cache, value)
    repaired = g.SemanticMapper(["cafe", "park"], settings, Embedder())
    assert repaired.match("咖啡").category == "cafe"
    assert np.load(cache).shape == (2, 3)


@pytest.mark.parametrize("planning", [True, False])
@pytest.mark.parametrize("word,query", [("停车场", "附近停车场"),
                                      ("不明确", "随便吧"), ("unknown", "随便吧"), ("未知", "随便吧")])
def test_reserved_and_parking_intents_never_reach_category_model(monkeypatch, planning, word, query):
    intent = g.GeoIntent(intent=word, modifiers=[])
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: intent)
    monkeypatch.setattr(g, "parse_plan", lambda *a, **kw: g.GeoPlan(stops=[intent], travel_mode="walking"))
    monkeypatch.setattr(g, "SemanticMapper", lambda *a, **kw: pytest.fail("unsafe intent embedded"))
    engine = QueryEngine()
    try:
        with pytest.raises(g.GeoaskError, match="停车场|未能理解"):
            engine.run(QueryRequest(query, (116.46, 39.92), 1000, 10, planning=planning))
    finally:
        engine.close()


@pytest.mark.parametrize("word", ["unknown", "未知", "不明确"])
def test_cli_rejected_intent_has_nonzero_exit(monkeypatch, word):
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: g.GeoIntent(intent=word, modifiers=[]))
    monkeypatch.setattr(g, "load_data", lambda *a: pytest.fail("rejected intent loaded data"))
    result = CliRunner().invoke(g.cli, [word])
    assert result.exit_code != 0 and "Error" in result.output


def test_nan_radius_rejected_before_api_even_named_query(monkeypatch):
    monkeypatch.setattr(g, "parse_intent", lambda *a: pytest.fail("nan reached API"))
    result = CliRunner().invoke(g.cli, ["去鸟巢", "--radius", "nan"])
    assert result.exit_code != 0 and "半径" in result.output


@pytest.mark.parametrize("category", [None, "cafe"])
def test_cli_semantic_failure_and_empty_results_exit_nonzero(monkeypatch, category):
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: g.GeoIntent(intent="咖啡", modifiers=[]))
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [("cafe",)]
    monkeypatch.setattr(g, "load_data", lambda *a: con)
    monkeypatch.setattr(g, "SemanticMapper", lambda *a: NS(match=lambda _: g.CategoryMatch(category, "cafe", .9, .5)))
    monkeypatch.setattr(g, "search", lambda *a, **kw: [])
    result = CliRunner().invoke(g.cli, ["咖啡"])
    assert result.exit_code != 0
    assert "类别匹配失败" in result.output if category is None else "没有匹配地点" in result.output
    con.close.assert_called_once()


def test_cli_missing_named_result_exits_nonzero(monkeypatch):
    import geoask_places
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: g.GeoIntent(intent="参观", modifiers=[], place_name="不存在的地方"))
    monkeypatch.setattr(g, "load_data", lambda *a: MagicMock())
    monkeypatch.setattr(geoask_places, "search_place", lambda *a, **kw: [])
    result = CliRunner().invoke(g.cli, ["去不存在的地方"])
    assert result.exit_code != 0 and "未找到该目的地" in result.output


def regional_data(tmp_path, *, name="地方名称", aliases=" ; ； | "):
    source = tmp_path / "region.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": {"Name": name, "Category": "unknown", "Alias": aliases},
        "geometry": {"type": "Point", "coordinates": [113.3, 23.1]}}]}, ensure_ascii=False), encoding="utf-8")
    return source


def test_case_insensitive_mappings_and_empty_alias_cleanup(tmp_path):
    source = regional_data(tmp_path)
    settings = g.Settings(geojson_path=source, layer=None, field_mapping=g.FieldMapping(
        name="name", category="category", geometry="GEOM", aliases=["alias"]))
    with g.load_data(settings) as con:
        assert con.execute("SELECT name, aliases FROM source_places").fetchone() == ("地方名称", [])


@pytest.mark.parametrize("aliases", [" ; ； | ", ["", " ", None]])
def test_import_rejects_unnamed_data_with_empty_aliases(tmp_path, aliases):
    source = regional_data(tmp_path, name="  ", aliases=aliases)
    with pytest.raises(g.GeoaskError, match="没有名称或别名"):
        import_region(source, tmp_path / "new.yaml", g.Settings(), layer=None,
                      name="Name", category="Category", aliases=["Alias"])
    assert not (tmp_path / "new.yaml").exists()


def test_import_does_not_need_hardlinks_and_save_error_is_actionable(tmp_path, monkeypatch):
    source = regional_data(tmp_path)
    monkeypatch.setattr(os, "link", lambda *a: pytest.fail("requires hardlinks"))
    destination = tmp_path / "new.yaml"
    args = dict(layer=None, name="Name", category="Category", aliases=["Alias"])
    assert import_region(source, destination, g.Settings(), **args)[1:] == (1, 1)
    original = destination.read_bytes()
    with pytest.raises(g.GeoaskError):
        import_region(source, destination, g.Settings(), **args)
    assert destination.read_bytes() == original
    monkeypatch.setattr(runtime, "publish_new", MagicMock(side_effect=PermissionError("read-only")))
    with pytest.raises(g.GeoaskError, match="无法保存"):
        import_region(source, tmp_path / "fail.yaml", g.Settings(), **args)
    assert not (tmp_path / "fail.yaml").exists()


def test_dotnet_seven_digit_timestamp_normalized_for_older_python(monkeypatch):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".1234567+00:00"
    real = datetime
    class OldDatetime:
        now = staticmethod(real.now)
        @staticmethod
        def fromisoformat(value):
            assert ".123456+00:00" in value
            return real.fromisoformat(value)
    monkeypatch.setattr(location, "datetime", OldDatetime)
    point = location.parse_device_location(dict(status="ok", longitude=116.46, latitude=39.92, timestamp=stamp))
    assert point.longitude == 116.46


def test_location_subprocess_helper_and_errors():
    if os.name != "nt":
        pytest.skip("Windows device helper")
    runner = MagicMock(return_value=NS(stdout=json.dumps(dict(status="ok", longitude=116.46,
        latitude=39.92, timestamp=datetime.now(timezone.utc).isoformat()))))
    assert location.get_device_location(runner).latitude == 39.92
    assert g.location_script().is_file()
    assert runner.call_args.args[0][-1] == str(g.location_script())
    for failure in (subprocess.TimeoutExpired("powershell", 30), OSError("unavailable")):
        runner.side_effect = failure
        with pytest.raises(g.GeoaskError, match="当前坐标未改变"):
            location.get_device_location(runner)
    runner.side_effect = None
    runner.return_value.stdout = "invalid JSON"
    with pytest.raises(g.GeoaskError):
        location.get_device_location(runner)


def test_atomic_export_failure_preserves_previous_file(tmp_path, monkeypatch):
    from geoask_service import export_csv
    path = tmp_path / "trip.csv"
    path.write_bytes(b"previous")
    monkeypatch.setattr(os, "replace", MagicMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        export_csv(path, named_result())
    assert path.read_bytes() == b"previous" and list(tmp_path.iterdir()) == [path]


def test_chinese_turns_use_structured_data_when_provider_falls_back_to_english():
    from geoask_routing import chinese_instruction, parse_route_response
    from test_travel import encode
    start = dict(type=1, instruction="Walk east on Coffee Street.", begin_cardinal_direction=2,
                 street_names=["Coffee Street"], length=.12)
    turn = dict(type=15, instruction="Turn left.", street_names=["健身路"], length=.05)
    trip = {"trip": {"language": "en-US", "legs": [{"shape": encode([(116.46, 39.92), (116.461, 39.92)]),
            "summary": {"length": .17, "time": 120}, "maneuvers": [start, turn, {"type": 4}]}]}}
    route = parse_route_response(trip, "walking", 1)
    assert route.language == "en-US" and route.instruction_language == "zh-CN"
    assert "本地中文模板" in route.instruction_source
    assert route.legs[0].instructions == ["出发，向东行进（Coffee Street），行进约 120 米",
                                          "左转（健身路），行进约 50 米", "到达目的地"]
    assert "第 3 个出口" in chinese_instruction(dict(type=27, roundabout_exit_count=3))
    assert chinese_instruction(dict(type=999, instruction="New maneuver")) == "请沿地图路线行进"
