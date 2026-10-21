"""No paid API calls. Integration tests use the real model only when requested."""
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from click.testing import CliRunner

import geoask as g


def completion(content=None, parsed=None, finish_reason="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content, parsed=parsed, refusal=refusal))])


@pytest.mark.parametrize("query,intent,modifiers", [
    ("我想喝咖啡", "咖啡", []), ("找一家安静的餐厅", "餐厅", ["安静"]),
])
def test_deepseek_intent_boundary(query, intent, modifiers):
    client = MagicMock()
    client.chat.completions.create.return_value = completion(json.dumps({"intent": intent, "modifiers": modifiers}))
    result = g.parse_intent(query, g.LLMSettings(), client=client)
    assert result == g.GeoIntent(intent=intent, modifiers=modifiers)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][-1] == {"role": "user", "content": query}
    assert kwargs["model"] == "deepseek-chat"


def test_openai_structured_boundary():
    client = MagicMock()
    client.chat.completions.parse.return_value = completion(parsed=g.GeoIntent(intent="咖啡", modifiers=[]))
    result = g.parse_intent("我想喝咖啡", g.LLMSettings(response_format="structured"), client)
    assert result.intent == "咖啡"
    assert client.chat.completions.parse.call_args.kwargs["response_format"] is g.GeoIntent


@pytest.mark.parametrize("reply", [
    completion("not JSON"), completion('{"intent":"cafe","modifiers":[],"sql":"DROP TABLE poi"}'),
    completion('{"intent":"","modifiers":[]}'), completion('{}'),
    completion('{"intent":"咖啡","modifiers":[]}', finish_reason="length"),
    completion(refusal="refused"), SimpleNamespace(choices=[]),
])
def test_bad_llm_response_is_rejected(reply):
    client = MagicMock()
    client.chat.completions.create.return_value = reply
    with pytest.raises(g.GeoaskError):
        g.parse_intent("咖啡", g.LLMSettings(), client)


def test_missing_key_has_actionable_message(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(g.GeoaskError, match="DEEPSEEK_API_KEY"):
        g.parse_intent("咖啡", g.LLMSettings())


class ToyEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        values = {"cafe": [1, 0, 0], "咖啡": [1, .1, 0], "park": [0, 1, 0],
                  "火锅": [0, 0, 1], "ambiguous": [1, 1, 0]}
        return [np.array(values[t], dtype=np.float32) for t in texts]


def test_semantic_match_rejection_cache_and_invalidation(tmp_path):
    settings = g.Settings(embedding_model="test", cache_dir=tmp_path)
    embedder = ToyEmbedder()
    mapper = g.SemanticMapper(["park", "cafe"], settings, embedder)
    assert mapper.match("咖啡").category == "cafe"
    assert mapper.match("火锅").category is None
    assert mapper.match("ambiguous").category is None
    second = ToyEmbedder()
    g.SemanticMapper(["cafe", "park"], settings, second)
    assert second.calls == []  # Category vectors reused across processes.
    g.SemanticMapper(["cafe"], settings, second)
    assert second.calls == [["cafe"]]  # New dataset vocabulary invalidates cache.
    for path in tmp_path.glob('*.npy'):
        path.write_bytes(b'corrupt')
    recovered = ToyEmbedder()
    g.SemanticMapper(["cafe", "park"], settings, recovered)
    assert recovered.calls == [["cafe", "park"]]


@pytest.fixture
def con():
    conn = g.connect_spatial()
    conn.execute("CREATE TABLE poi (name VARCHAR, category VARCHAR, geometry GEOMETRY)")
    rows = [("at center", "cafe", 116.46, 39.92),
            ("north", "cafe", 116.46, 39.925),
            ("east", "cafe", 116.47, 39.92),
            ("corner outside circle", "cafe", 116.47, 39.928),
            ("far", "cafe", 116.56, 39.92),
            ("other type", "park", 116.46, 39.92)]
    for name, category, lon, lat in rows:
        conn.execute("INSERT INTO poi VALUES (?, ?, ST_Point(?, ?))", [name, category, lon, lat])
    conn.execute("CREATE INDEX poi_geom_idx ON poi USING RTREE(geometry)")
    yield conn
    conn.close()


def test_meter_filter_sort_and_coordinate_order(con):
    rows = g.search(con, "cafe", (116.46, 39.92), 1000)
    assert [r["name"] for r in rows] == ["at center", "north", "east"]
    assert 554 < rows[1]["dist_m"] < 556
    assert 854 < rows[2]["dist_m"] < 857
    assert all(math.isfinite(r["dist_m"]) and r["dist_m"] <= 1000 for r in rows)
    assert g.search(con, "cafe", (116.46, 39.92), 1) == [rows[0]]
    assert len(g.search(con, "cafe", (116.46, 39.92), 1000, limit=2)) == 2
    with pytest.raises(g.GeoaskError):
        g.search(con, "nonexistent", (116.46, 39.92), 1000)


def test_index_scan_in_actual_parameterized_plan(con):
    sql, params = g.build_query("cafe", (116.46, 39.92), 1000)
    plan = con.execute("EXPLAIN " + sql, params).fetchall()
    assert "RTREE_INDEX_SCAN" in str(plan)


@pytest.mark.parametrize("center,other", [
    ((179.999, 0), (-179.999, 0)), ((-179.999, 0), (179.999, 0)),
    ((15, 89.999), (-165, 89.999)), ((15, -89.999), (-165, -89.999)),
])
def test_date_line_and_polar_caps(con, center, other):
    con.execute("DELETE FROM poi")
    con.execute("INSERT INTO poi VALUES ('across', 'cafe', ST_Point(?,?))", list(other))
    rows = g.search(con, "cafe", center, 500)
    assert len(rows) == 1 and 200 < rows[0]["dist_m"] < 250


def test_sql_injection_is_only_a_bound_value(con):
    attack = "cafe'); DROP TABLE poi; --"
    sql, params = g.build_query(attack, (116.46, 39.92), 1000)
    assert attack not in sql
    assert attack in params
    assert con.execute(sql, params).fetchall() == []
    assert con.execute("SELECT count(*) FROM poi").fetchone()[0] == 6


def test_dry_run_never_executes_search(monkeypatch, con, tmp_path):
    attack = "我想喝咖啡'; DROP TABLE poi; --"
    monkeypatch.setattr(g, "parse_intent", lambda *args: g.GeoIntent(intent="咖啡", modifiers=["安静"]))
    monkeypatch.setattr(g, "load_data", lambda *args: con)
    monkeypatch.setattr(g, "SemanticMapper", lambda *args: SimpleNamespace(
        match=lambda word: g.CategoryMatch("cafe", "cafe", .9, .3)))
    def forbidden(*args):
        pytest.fail("dry-run executed search")
    monkeypatch.setattr(g, "search", forbidden)
    result = CliRunner().invoke(g.cli, [attack, "--dry-run", "--near", "116.46,39.92"])
    assert result.exit_code == 0, result.output
    assert "SQL" in result.output and "绑定参数" in result.output
    assert "未应用: 安静" in result.output
    assert "WHERE dist_m <= ?" in result.output
    assert attack not in result.output and "DROP TABLE" not in result.output


@pytest.mark.parametrize("args", [
    ["--near", "39.92,116.46"], ["--near", "nan,39"], ["--near", "bad"],
    ["--radius", "0"], ["--radius", "-2"], ["--limit", "0"],
])
def test_cli_rejects_invalid_arguments(args):
    result = CliRunner().invoke(g.cli, ["咖啡", *args])
    assert result.exit_code == 2


def test_config_relative_paths_and_quoted_field_mapping(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('geojson_path: fixture.geojson\nlayer: null\nfield_mapping:\n  name: weird\"name\n  category: kind\n  geometry: geom\n', encoding="utf-8")
    data = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {
        'weird"name': "Cafe", "kind": "cafe"}, "geometry": {"type": "Point", "coordinates": [116.46, 39.92]}}]}
    (tmp_path / "fixture.geojson").write_text(json.dumps(data), encoding="utf-8")
    settings = g.read_settings(path)
    assert settings.geojson_path == tmp_path / "fixture.geojson"
    conn = g.load_data(settings)
    try:
        assert g.search(conn, "cafe", (116.46, 39.92), 10)[0]["name"] == "Cafe"
    finally:
        conn.close()


def test_bundled_real_data():
    settings = g.read_settings(g.ROOT / "config.yaml")
    conn = g.load_data(settings)
    try:
        count = conn.execute("SELECT count(*) FROM poi").fetchone()[0]
        assert 200 <= count <= 500
        categories = {row[0] for row in conn.execute("SELECT DISTINCT category FROM poi").fetchall()}
        assert categories == {"cafe", "restaurant", "park", "railway_station", "supermarket", "fitness_centre"}
        rows = g.search(conn, "cafe", settings.default_location, 1000)
        assert rows and all(0 <= row["dist_m"] <= 1000 for row in rows)
        assert [r['dist_m'] for r in rows] == sorted(r['dist_m'] for r in rows)
    finally:
        conn.close()


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("GEOASK_MODEL_TESTS") != "1", reason="Set GEOASK_MODEL_TESTS=1 to use/download the real embedding model")
def test_real_multilingual_model():
    settings = g.read_settings(g.ROOT / "config.yaml")
    mapper = g.SemanticMapper(["cafe", "restaurant", "park", "railway_station", "supermarket", "fitness_centre"], settings)
    assert mapper.match("咖啡").category == "cafe"
    assert mapper.match("咖啡").similarity > .6
    assert mapper.match("餐厅").category == "restaurant"
    assert mapper.match("健身").category == "fitness_centre"
    assert mapper.match("火锅").category is None
    assert mapper.match("量子计算机").category is None
