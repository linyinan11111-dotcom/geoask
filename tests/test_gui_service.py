"""Test the desktop query boundary without opening windows or calling paid APIs."""
import csv
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import geoask as g
import geoask_service as service


@pytest.fixture
def engine(monkeypatch):
    mapper_factory = MagicMock(return_value=SimpleNamespace(
        match=lambda intent: g.CategoryMatch("cafe", "cafe", .9, .4)))
    monkeypatch.setattr(g, "SemanticMapper", mapper_factory)
    engine = service.QueryEngine()
    yield engine, mapper_factory
    engine.close()


def request(**kwargs):
    return service.QueryRequest("我想喝咖啡", (116.46, 39.92), 1000, 10, **kwargs)


def test_demo_real_spatial_results_coordinates_and_reuse(engine, monkeypatch):
    engine, mapper_factory = engine
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: pytest.fail("Demo called an LLM"))
    statuses = []
    result = engine.run(request(demo=True), statuses.append)
    assert result.rows and result.data_count == json.loads((g.ROOT / "data/manifest.json").read_text())["rows"]
    assert "固定" in result.note
    assert all(-180 <= r["longitude"] <= 180 and -90 <= r["latitude"] <= 90 for r in result.rows)
    assert result.rows[0]["dist_m"] < 1000
    original_connection = engine.con
    engine.run(request(demo=True))
    assert engine.con is original_connection
    assert mapper_factory.call_count == 1
    assert len(statuses) >= 4


def test_preview_does_not_run_search(engine, monkeypatch):
    engine, _ = engine
    monkeypatch.setattr(g, "search", lambda *a, **kw: pytest.fail("Preview executed search"))
    result = engine.run(request(demo=True, dry_run=True))
    assert result.rows == []
    assert "longitude" in result.sql and "ST_Distance_Spheroid" in result.sql
    assert result.params[-2:] == [1000, 10]
    assert "未执行" in result.note


def test_rejected_mapping_never_searches(engine, monkeypatch):
    engine, mapper_factory = engine
    mapper_factory.return_value.match = lambda intent: g.CategoryMatch(None, "cafe", .2, .01)
    monkeypatch.setattr(g, "search", lambda *a, **kw: pytest.fail("Rejected match ran search"))
    result = engine.run(request(demo=True))
    assert not result.rows and not result.sql
    assert "可用类别" in result.note


def test_invalid_inputs_never_reach_api(monkeypatch):
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: pytest.fail("Invalid input reached API"))
    engine = service.QueryEngine()
    for bad in (replace(request(), center=(39.92, 116.46)), replace(request(), query=" "),
                replace(request(), radius=float('nan')), replace(request(), limit=0)):
        with pytest.raises((ValueError, g.GeoaskError)):
            engine.run(bad)


def test_session_key_uses_explicit_client_and_is_not_exported(engine, monkeypatch, tmp_path):
    engine, _ = engine
    key = "test-only-session-secret"
    client_factory = MagicMock()
    monkeypatch.setattr(g, "OpenAI", client_factory)
    parser = MagicMock(return_value=g.GeoIntent(intent="咖啡", modifiers=["安静"]))
    monkeypatch.setattr(g, "parse_intent", parser)
    llm = g.LLMSettings(base_url="http://localhost:1234/v1", model="local-model")
    req = request(api_key=key, llm=llm)
    result = engine.run(req)
    assert client_factory.call_args.kwargs["api_key"] == key
    assert client_factory.call_args.kwargs["base_url"] == llm.base_url
    assert parser.call_args.args[1] == llm
    assert "未应用" in result.note
    assert key not in repr(req) and key not in repr(result) and key not in result.sql
    output = tmp_path / "results.csv"
    service.export_csv(output, result)
    assert key not in output.read_text(encoding="utf-8-sig")
    with output.open(encoding="utf-8-sig", newline="") as contents:
        rows = list(csv.reader(contents))
    assert len(rows) == len(result.rows) + 1
    assert rows[0][0] == "名称" and rows[1][0] == result.rows[0]["name"]


def test_csv_does_not_treat_untrusted_names_as_formulas(engine, tmp_path):
    engine, _ = engine
    result = engine.run(request(demo=True))
    result.rows[0]["name"] = "=SUM(1,2)"
    output = tmp_path / "safe.csv"
    service.export_csv(output, result)
    with output.open(encoding="utf-8-sig", newline="") as contents:
        rows = list(csv.reader(contents))
    assert rows[1][0] == "'=SUM(1,2)"


def test_failed_mapper_closes_new_connection(monkeypatch):
    con = MagicMock()
    con.execute.return_value.fetchone.return_value = (401,)
    con.execute.return_value.fetchall.return_value = [("cafe",)]
    monkeypatch.setattr(g, "load_data", lambda settings: con)
    def fail(*args):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(g, "SemanticMapper", fail)
    engine = service.QueryEngine()
    with pytest.raises(RuntimeError, match="model unavailable"):
        engine.run(request(demo=True))
    con.close.assert_called_once()
    assert engine.con is None
