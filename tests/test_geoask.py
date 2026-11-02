"""Intent parsing, configuration and the Amap CLI. No paid API calls."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_shipped_config_is_amap_only():
    settings = g.read_settings(g.DEFAULT_CONFIG)
    assert settings.amap.enabled and settings.amap.primary
    assert settings.routing.provider == "amap"
    assert settings.default_location == (116.46, 39.92)


def test_legacy_local_keys_are_ignored_when_amap_stays_primary(tmp_path):
    path = tmp_path / "old.yaml"
    path.write_text("""
amap: {enabled: true, primary: true}
routing: {provider: amap, endpoint: https://valhalla1.openstreetmap.de/route}
geojson_path: data/beijing_poi.gpkg
embedding_model: sentence-transformers/example
place_catalog_path: data/beijing_places.gpkg
parking: {categories: [parking], radius: 800}
""", encoding="utf-8")
    settings = g.read_settings(path)
    assert settings.amap.primary and settings.routing.provider == "amap"
    assert not hasattr(settings, "geojson_path")
    assert "endpoint" not in settings.routing.model_dump()


def test_disabled_amap_config_is_rejected(tmp_path):
    path = tmp_path / "local.yaml"
    path.write_text("amap: {enabled: true, primary: false}\n", encoding="utf-8")
    with pytest.raises(g.GeoaskError, match="高德"):
        g.read_settings(path)


@pytest.mark.parametrize("args", [
    ["--near", "39.92,116.46"], ["--near", "nan,39"], ["--near", "bad"],
    ["--radius", "0"], ["--radius", "-2"], ["--limit", "0"],
])
def test_cli_rejects_invalid_arguments(args):
    result = CliRunner().invoke(g.cli, ["咖啡", *args])
    assert result.exit_code == 2


def test_cli_dry_run_prints_amap_query_without_search(monkeypatch):
    monkeypatch.setattr(g, "parse_intent", lambda *args, **kwargs: g.GeoIntent(intent="洗浴中心", modifiers=[]))
    called = False

    def search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("dry-run must not call Amap")

    monkeypatch.setattr("geoask_amap.search_pois", search)
    result = CliRunner().invoke(g.cli, ["洗浴中心", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "高德 place/text" in result.output
    assert not called


@pytest.mark.parametrize("word", ["unknown", "未知", "不明确"])
def test_cli_rejected_intent_has_nonzero_exit(monkeypatch, word):
    monkeypatch.setattr(g, "parse_intent", lambda *a, **kw: g.GeoIntent(intent=word, modifiers=[]))
    result = CliRunner().invoke(g.cli, [word])
    assert result.exit_code != 0 and "未能理解" in result.output


def test_nan_radius_rejected_before_api(monkeypatch):
    monkeypatch.setattr(g, "parse_intent", lambda *a: pytest.fail("nan reached API"))
    result = CliRunner().invoke(g.cli, ["去鸟巢", "--radius", "nan"])
    assert result.exit_code != 0 and "半径" in result.output
