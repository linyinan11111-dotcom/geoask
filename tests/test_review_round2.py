"""Second review: native-crash isolation, semantic boundaries and import safety."""
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from click.testing import CliRunner
import pytest
import yaml

import geoask as g
import geoask_places as places
from geoask_data import import_region
from geoask_service import QueryEngine, QueryRequest


@pytest.mark.parametrize("target", ["primary", "catalog", "import"])
@pytest.mark.parametrize("layer,success", [("upper", True), ("missing", False)])
def test_untrusted_layer_cannot_crash_process(target, layer, success):
    # Native access violations cannot be caught by pytest; always isolate these.
    code = r'''
import json, sys
from pathlib import Path
import tempfile
import geoask as g
from geoask_places import prepare_places
from geoask_data import import_region
settings=g.read_settings(g.DEFAULT_CONFIG)
target, mode=sys.argv[1:]
requested=('POI' if target!='catalog' else 'PLACES') if mode=='upper' else 'missing_layer_123'
try:
    if target=='primary':
        settings.layer=requested
        with g.load_data(settings) as con:
            assert con.execute('SELECT count(*) FROM poi').fetchone()[0]>0
    elif target=='catalog':
        settings.place_catalog_layer=requested
        with g.load_data(settings) as con:
            prepare_places(con,settings)
            assert con.execute('SELECT count(*) FROM named_places').fetchone()[0]>0
    else:
        with tempfile.TemporaryDirectory() as folder:
            import_region(settings.geojson_path,Path(folder)/'region.yaml',settings,layer=requested)
    print(json.dumps({'ok':True}))
except g.GeoaskError as exc:
    print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
'''
    env = os.environ | {"PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-c", code, target, layer], cwd=g.ROOT,
                            capture_output=True, text=True, encoding="utf-8", env=env, timeout=45)
    assert result.returncode == 0, f"native/process failure {result.returncode}: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["ok"] is success
    if not success:
        assert "图层" in payload["error"] and "可用图层" in payload["error"]


def dataset(tmp_path, rows, *, parking=None):
    path = tmp_path / "region.geojson"
    features = [{"type": "Feature", "properties": props, "geometry": {"type": "Point",
                "coordinates": [113.3 + i*.0001, 23.1]}} for i, props in enumerate(rows)]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
    config = tmp_path / "config.yaml"
    value = dict(geojson_path=path.name, layer=None, default_location=[113.3, 23.1],
                 field_mapping=dict(name="name", category="fclass", geometry="geom"))
    if parking:
        value["parking"] = {"categories": parking}
    config.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    return config


@pytest.mark.parametrize("planning", [False, True])
@pytest.mark.parametrize("word,query", [("咖啡", "找一家有停车位的咖啡店"), ("餐厅", "停车方便的餐厅"),
                                      ("咖啡", "停车场旁边的咖啡店")])
def test_parking_modifier_does_not_block_target_category(tmp_path, monkeypatch, planning, word, query):
    category = "cafe" if word == "咖啡" else "restaurant"
    config = dataset(tmp_path, [dict(name="目标店铺", fclass=category), dict(name="公园", fclass="park")])
    intent = g.GeoIntent(intent=word, modifiers=["停车方便"])
    mapper = NS(match=MagicMock(return_value=g.CategoryMatch(category, category, .9, .4)))
    monkeypatch.setattr(g, "SemanticMapper", lambda *a: mapper)
    monkeypatch.setattr(g, "parse_intent", lambda *a: intent)
    monkeypatch.setattr(g, "parse_plan", lambda *a: g.GeoPlan(stops=[intent], travel_mode="walking"))
    engine = QueryEngine()
    try:
        result = engine.run(QueryRequest(query, (113.3, 23.1), 1000, 10, config, planning=planning))
        assert result.rows[0]["category"] == category
        assert "停车方便" in result.note  # Descriptive condition is explicitly not verified.
        cli = CliRunner().invoke(g.cli, [query, "--config", str(config)])
        assert cli.exit_code == 0 and "目标店铺" in cli.output
        if planning:
            monkeypatch.setattr(g, "parse_plan", lambda *a: g.GeoPlan(stops=[intent, intent], travel_mode="walking"))
            multi = engine.run(replace(result.request, query=query + "然后再去一家"))
            assert len(multi.stops) == 2 and all(s.selected for s in multi.stops)
    finally:
        engine.close()


@pytest.mark.parametrize("planning", [False, True])
def test_direct_parking_uses_only_real_configured_categories(tmp_path, monkeypatch, planning):
    config = dataset(tmp_path, [dict(name="地面停车", fclass="parking"), dict(name="地下停车", fclass="地下车库"),
                               dict(name="公园", fclass="park"), dict(name="自行车停车", fclass="bicycle_parking")],
                     parking=["parking", "地下车库"])
    intent = g.GeoIntent(intent="停车场", modifiers=[])
    monkeypatch.setattr(g, "parse_intent", lambda *a: intent)
    monkeypatch.setattr(g, "parse_plan", lambda *a: g.GeoPlan(stops=[intent], travel_mode="driving"))
    monkeypatch.setattr(g, "SemanticMapper", lambda *a: pytest.fail("Parking must not embed into park"))
    engine = QueryEngine()
    try:
        result = engine.run(QueryRequest("附近停车场", (113.3, 23.1), 1000, 10, config, planning=planning))
        assert {r["category"] for r in result.rows} == {"parking", "地下车库"}
        cli = CliRunner().invoke(g.cli, ["附近停车场", "--config", str(config)])
        assert cli.exit_code == 0 and "地面停车" in cli.output and "地下停车" in cli.output
        preview = engine.run(replace(result.request, dry_run=True))
        assert not preview.rows and "parking" in str(preview.params)
    finally:
        engine.close()


def test_absent_parking_category_has_cli_actionable_error(tmp_path, monkeypatch):
    config = dataset(tmp_path, [dict(name="公园", fclass="park")])
    monkeypatch.setattr(g, "parse_intent", lambda *a: g.GeoIntent(intent="停车场", modifiers=[]))
    monkeypatch.setattr(g, "SemanticMapper", lambda *a: pytest.fail("No parking category must not embed"))
    result = CliRunner().invoke(g.cli, ["附近停车场", "--config", str(config)])
    assert result.exit_code != 0 and "切换为含停车场类别的数据" in result.output
    assert "勾选" not in result.output


def test_relative_absolute_config_cache_identical(monkeypatch):
    monkeypatch.chdir(g.ROOT)
    assert g.read_settings(Path("config.yaml")).cache_dir == g.read_settings(g.DEFAULT_CONFIG.resolve()).cache_dir
    assert g.read_settings(Path("./config.yaml")).cache_dir == (g.ROOT / ".cache").resolve()


def test_mixed_alias_arrays_and_scalars_are_all_split(tmp_path):
    config = dataset(tmp_path, [dict(name="地点一", fclass="venue", alt_name=["A;B", "C；D"]),
                               dict(name="地点二", fclass="venue", alt_name="E;F|G")])
    settings = g.read_settings(config)
    with g.load_data(settings) as con:
        for alias in "ABCDEFG":
            rows = places.search_place(con, alias, (113.3, 23.1), 10, settings)
            assert len(rows) == 1 and rows[0]["name_rank"] == 0


@pytest.mark.parametrize("blank", ["", "  ", "\u3000\u00a0"])
def test_blank_alias_target_rejected_and_never_selects_unnamed(tmp_path, blank):
    with pytest.raises(g.ValidationError, match="不能为空"):
        g.Settings(place_aliases={"不存在": blank})
    config = dataset(tmp_path, [dict(name="", fclass="parking")])
    settings = g.read_settings(config).model_copy(update={"place_aliases": {"不存在": blank}})
    with g.load_data(settings) as con:
        assert places.search_place(con, "不存在", (113.3, 23.1), 10, settings) == []


@pytest.mark.parametrize("name,query", [("广州\u3000塔", "广州塔"), ("广州\u00a0塔", "广州　塔"),
                                     ("İSTANBUL", "İstanbul"), ("ＳＴＡＲ", "star")])
def test_unicode_name_keys_identical_in_dataset_and_query(tmp_path, name, query):
    config = dataset(tmp_path, [dict(name=name, fclass="venue")])
    settings = g.read_settings(config)
    with g.load_data(settings) as con:
        assert places.search_place(con, query, (113.3, 23.1), 10, settings)[0]["name_rank"] == 0


def test_missing_alias_target_still_finds_original_data_name(tmp_path):
    config = dataset(tmp_path, [dict(name="原场馆", fclass="venue")])
    settings = g.read_settings(config).model_copy(update={"place_aliases": {"原场馆": "失效正式名称"}})
    with g.load_data(settings) as con:
        assert places.search_place(con, "原场馆", (113.3, 23.1), 10, settings)[0]["name_rank"] == 0


@pytest.mark.parametrize("failure", ["crs", "geometry", "corrupt"])
def test_bad_imports_report_chinese_without_raw_sql(tmp_path, failure):
    config = dataset(tmp_path, [dict(name="地点", fclass="venue")])
    settings = g.read_settings(config)
    options = {}
    if failure == "crs":
        options["source_crs"] = "EPSG:INVALID"
    elif failure == "geometry":
        options["geometry"] = "name"
    else:
        settings.geojson_path.write_bytes(b"not spatial data")
    with pytest.raises(g.GeoaskError) as error:
        import_region(settings.geojson_path, tmp_path / "imported.yaml", settings, **options)
    message = str(error.value)
    assert any(word in message for word in ("坐标系", "几何", "文件格式"))
    assert not any(word in message for word in ("Binder Error", "IO Error", "SELECT", "LINE 1"))
    assert not (tmp_path / "imported.yaml").exists()


def test_config_encoding_and_root_errors_are_actionable(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes("# 地区配置".encode("gbk"))
    with pytest.raises(g.GeoaskError, match="UTF-8"):
        g.read_settings(path)
    for value in ["false", "[]", "0", "''"]:
        path.write_text(value, encoding="utf-8")
        with pytest.raises(g.GeoaskError, match="键值对象"):
            g.read_settings(path)


def test_owned_intent_client_is_closed_on_success_and_failure(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    context = MagicMock()
    monkeypatch.setattr(g, "OpenAI", MagicMock(return_value=context))
    message = NS(content='{"intent":"咖啡","modifiers":[]}', refusal=None)
    context.__enter__.return_value.chat.completions.create.return_value = NS(choices=[NS(finish_reason="stop", message=message)])
    assert g.parse_intent("咖啡", g.LLMSettings()).intent == "咖啡"
    context.__exit__.assert_called_once()
    context.reset_mock()
    message.content = "invalid"
    with pytest.raises(g.GeoaskError):
        g.parse_intent("咖啡", g.LLMSettings())
    context.__exit__.assert_called_once()


def test_prepare_detects_same_stat_replacement_without_reloading_for_map_settings(tmp_path):
    config = dataset(tmp_path, [dict(name="First", fclass="cafe")])
    settings = g.read_settings(config)
    engine = QueryEngine()
    try:
        engine._prepare(settings, lambda _: None, need_mapper=False)
        original = engine.con
        changed = settings.model_copy(update={"map": g.MapSettings(attribution="new credit"),
                                              "default_radius": 2000})
        engine._prepare(changed, lambda _: None, need_mapper=False)
        assert engine.con is original
        stat = settings.geojson_path.stat()
        data = settings.geojson_path.read_bytes().replace(b'First', b'Other')
        settings.geojson_path.write_bytes(data)
        os.utime(settings.geojson_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert settings.geojson_path.stat().st_size == stat.st_size
        engine._prepare(changed, lambda _: None, need_mapper=False)
        assert engine.con is not original
        assert engine.con.execute("SELECT name FROM poi").fetchone()[0] == "Other"
    finally:
        engine.close()


def test_route_miles_and_real_bearing_are_used():
    from geoask_routing import parse_route_response
    from test_travel import encode
    data = {"trip": {"units": "miles", "language": "en-US", "legs": [{
        "shape": encode([(116.46, 39.92), (116.461, 39.92)]), "summary": {"length": 1, "time": 20},
        "maneuvers": [{"type": 1, "bearing_after": 270, "length": .1, "instruction": "Walk west."}]}]}}
    route = parse_route_response(data, "walking", 1)
    assert route.distance_m == pytest.approx(1609.344)
    assert "向西" in route.legs[0].instructions[0] and "161 米" in route.legs[0].instructions[0]
    data["trip"]["units"] = "unexpected"
    with pytest.raises(g.GeoaskError):
        parse_route_response(data, "walking", 1)


def test_tile_decode_failure_has_backoff_and_prune_continues_locked_file(tmp_path, monkeypatch):
    from collections import OrderedDict
    from PIL import Image
    import geoask_map as maps
    key = (1, 2, 0, 0)
    output = BytesIO()
    Image.new("RGB", (256, 256), "red").save(output, "PNG")
    fake = NS(pending={key}, generation=1, photos=OrderedDict(), failed={}, tile_error="")
    monkeypatch.setattr(maps.ImageTk, "PhotoImage", MagicMock(side_effect=OSError("decode failure")))
    with pytest.raises(OSError):
        maps.SlippyMap._handle_tile(fake, (key, output.getvalue()))
    assert fake.failed[key] > maps.time.monotonic() + 40
    for name in ("locked.png", "normal.png"):
        path = tmp_path / name
        path.write_bytes(b"expired")
        os.utime(path, (0, 0))
    original_unlink = Path.unlink
    def unlink(path, *a, **kw):
        if path.name == "locked.png":
            raise PermissionError("locked")
        return original_unlink(path, *a, **kw)
    monkeypatch.setattr(Path, "unlink", unlink)
    maps.prune_tile_cache(tmp_path)
    assert (tmp_path / "locked.png").exists() and not (tmp_path / "normal.png").exists()


def test_truncated_jpeg_rejected_before_caching():
    from PIL import Image
    from geoask_map import validate_tile
    output = BytesIO()
    Image.new("RGB", (256, 256), "red").save(output, "JPEG")
    truncated = output.getvalue()[:-25]
    with pytest.raises((OSError, ValueError)):
        validate_tile(truncated)


@pytest.mark.parametrize("points", [[(180., 0), (179.99, 0)], [(180., 0), (-179.99, 0)]])
def test_map_fit_at_positive_dateline(points):
    from geoask_map import SlippyMap
    fake = NS(winfo_width=lambda: 800, winfo_height=lambda: 500)
    def set_center(lon, lat, zoom):
        fake.center, fake.zoom = (lon, lat), zoom
    fake.set_center = set_center
    SlippyMap.fit(fake, points)
    assert fake.zoom > 10
    for point in points:
        x, y = SlippyMap.screen_point(fake, *point)
        assert 20 < x < 780 and 20 < y < 480
