"""Regression: explicit named destinations must not be mapped to arbitrary venue categories."""
from dataclasses import replace
from unittest.mock import MagicMock
from types import SimpleNamespace
import json

import pytest
from click.testing import CliRunner

import geoask as g
import geoask_places as places
from geoask_service import QueryEngine, QueryRequest, route_points


QUERY = '我想去鸟巢看演唱会'


def named(name='鸟巢'):
    return g.GeoIntent(intent='演唱会', modifiers=[], place_name=name)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(g, 'SemanticMapper', lambda *a, **kw: pytest.fail('Named place used category embeddings'))
    engine = QueryEngine()
    yield engine
    engine.close()


def request(**kwargs):
    return QueryRequest(QUERY, (116.46,39.92), 1000, 10, planning=True, **kwargs)


def test_named_destination_preserved_by_both_parser_boundaries():
    client = MagicMock()
    payload = named().model_dump()
    def response(data):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content=json.dumps(data), refusal=None))])
    client.chat.completions.create.return_value = response(payload)
    assert g.parse_intent(QUERY, g.LLMSettings(), client).place_name == '鸟巢'
    client.chat.completions.create.return_value = response({'stops':[payload], 'travel_mode':'walking'})
    plan = g.parse_plan(QUERY, g.LLMSettings(), client)
    assert len(plan.stops) == 1 and plan.stops[0].place_name == '鸟巢'
    assert plan.stops[0].intent == '演唱会'


@pytest.mark.parametrize('name', ['鸟巢', '国家体育场'])
def test_bird_nest_resolves_real_venue_beyond_nearby_radius(engine, monkeypatch, name):
    monkeypatch.setattr(g, 'parse_plan', lambda *a: g.GeoPlan(stops=[named(name)], travel_mode='walking'))
    result = engine.run(request())
    stop = result.stops[0]
    assert stop.resolution == 'name' and stop.selected['name'] == '国家体育场'
    assert stop.selected['category'] == 'stadium'
    manifest = json.loads((g.ROOT / 'data/places-manifest.json').read_text(encoding='utf-8'))
    expected = manifest['verified_alias']['鸟巢']['coordinate']
    assert (stop.selected['longitude'], stop.selected['latitude']) == pytest.approx(expected)
    assert stop.selected['dist_m'] > result.request.radius
    assert len(route_points(result)) == 2
    assert '不确认演出' in result.note


def test_missing_named_venue_never_substitutes_nearby_stadium(engine, monkeypatch):
    monkeypatch.setattr(g, 'parse_plan', lambda *a: g.GeoPlan(stops=[named('不存在的星际演唱会场馆123')], travel_mode='walking'))
    result = engine.run(request())
    assert not result.rows and not result.stops[0].selected
    assert '不会替换' in result.note
    with pytest.raises(g.GeoaskError):
        route_points(result)


def test_ambiguous_names_require_choice_even_with_one_displayed_candidate(engine, monkeypatch):
    monkeypatch.setattr(g, 'parse_plan', lambda *a: g.GeoPlan(stops=[named('星巴克'), named()], travel_mode='walking'))
    result = engine.run(replace(request(), limit=1))
    first, second = result.stops
    assert len(first.candidates) == 1 and first.candidates[0]['name_match_count'] > 1
    assert first.selected is None and second.center is None
    with pytest.raises(g.GeoaskError):
        route_points(result)
    changed = engine.reselect(result, 0, 0)
    assert not changed.stops[0].blocked_reason
    assert changed.stops[1].center == (first.candidates[0]['longitude'], first.candidates[0]['latitude'])
    assert changed.stops[1].selected['name'] == '国家体育场'
    assert len(route_points(changed)) == 3


def test_named_preview_is_parameterized_and_does_not_search(engine, monkeypatch):
    malicious = "鸟巢%_' OR 1=1; DROP TABLE poi; --"
    monkeypatch.setattr(g, 'parse_plan', lambda *a: g.GeoPlan(stops=[named(malicious)], travel_mode='walking'))
    monkeypatch.setattr(places, 'search_place', lambda *a: pytest.fail('Preview searched places'))
    result = engine.run(request(dry_run=True))
    assert malicious not in result.sql and places.name_key(malicious) in result.params
    assert not result.rows and not result.stops[0].selected


def test_named_query_uses_literal_substrings_and_supports_custom_data():
    settings = g.Settings(place_catalog_path=None)
    with g.connect_spatial() as con:
        con.execute('CREATE TABLE poi (name VARCHAR, category VARCHAR, geometry GEOMETRY)')
        con.execute("INSERT INTO poi VALUES (?, 'venue', ST_Point(1,2))", ["Venue_%'s"])
        assert not places.search_place(con, "%' OR 1=1 --", (1,2), 10, settings)
        assert places.search_place(con, "Venue_%'s", (1,2), 10, settings)[0]['dist_m'] == 0
        assert con.execute('SELECT count(*) FROM poi').fetchone()[0] == 1


def test_generic_center_suffix_can_match_imported_name():
    assert places.name_search_terms('洗浴中心') == ['洗浴中心', '洗浴']
    assert places.name_search_terms('咖啡') == ['咖啡']


def test_named_cli_skips_embeddings_and_prints_destination(monkeypatch):
    monkeypatch.setattr(g, 'parse_intent', lambda *a: named())
    monkeypatch.setattr(g, 'SemanticMapper', lambda *a: pytest.fail('CLI used category embedding'))
    result = CliRunner().invoke(g.cli, [QUERY, '--explain'])
    assert result.exit_code == 0, result.output
    assert '国家体育场' in result.output and '不受附近半径限制' in result.output
