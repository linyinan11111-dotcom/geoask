"""Synthetic regional fixtures: field/alias mapping, isolation and import behavior."""
import json
from dataclasses import replace

import pytest
import yaml

import geoask as g
import geoask_places as places
from geoask_data import import_region
from geoask_service import QueryEngine, QueryRequest, route_points


def feature(name, coordinate, **properties):
    return {'type': 'Feature', 'properties': {'title': name, **properties},
            'geometry': {'type':'Point', 'coordinates': coordinate}}


def region(tmp_path, label, features):
    data = tmp_path / (label + '.geojson')
    data.write_text(json.dumps({'type':'FeatureCollection', 'features':features}, ensure_ascii=False), encoding='utf-8')
    config = tmp_path / (label + '.yaml')
    config.write_text(yaml.safe_dump({'geojson_path':data.name, 'layer':None,
        'field_mapping': {'name':'title', 'category':None, 'geometry':'geom'},
        'default_location': features[0]['geometry']['coordinates']}, allow_unicode=True), encoding='utf-8')
    return config


def test_other_regions_auto_read_names_aliases_and_languages(tmp_path):
    path = region(tmp_path, 'guangzhou', [feature('广州塔', [113.3309,23.1122],
        alt_name='小蛮腰;广州新电视塔', short_name='广塔', **{'name:en':'Canton Tower'})])
    settings = g.read_settings(path)
    assert settings.place_catalog_path is None and not settings.place_aliases
    with g.load_data(settings) as con:
        assert con.execute('SELECT count(*) FROM poi').fetchone()[0] == 0
        for name in ['广州塔','小蛮腰','广塔','广州新电视塔','Canton Tower',' canton tower ']:
            rows = places.search_place(con, name, (113.3,23.1), 10, settings)
            assert len(rows) == 1 and rows[0]['name'] == '广州塔' and rows[0]['name_rank'] == 0
            assert rows[0]['category'] == 'unknown'


def test_custom_alias_fields_and_array_values(tmp_path):
    path = region(tmp_path, 'shanghai', [feature('东方明珠广播电视塔', [121.499,31.239],
        **{'别"称':['东方明珠','东方明珠塔'], 'kind':'tower'})])
    settings = g.read_settings(path)
    settings.field_mapping.aliases = ['别"称']
    settings.field_mapping.category = 'kind'
    with g.load_data(settings) as con:
        assert places.search_place(con, '东方明珠', (121.4,31.2), 10, settings)[0]['name_rank'] == 0
        assert places.search_place(con, '东方明珠塔', (121.4,31.2), 10, settings)[0]['category'] == 'tower'


def test_llm_name_candidate_requires_confirmation_and_real_local_match(tmp_path, monkeypatch):
    path = region(tmp_path, 'guangzhou', [feature('广州塔', [113.3309,23.1122])])
    intent = g.GeoIntent(intent='观光', modifiers=[], place_name='小蛮腰', place_candidates=['广州塔'])
    monkeypatch.setattr(g, 'parse_plan', lambda *a, **kw: g.GeoPlan(stops=[intent], travel_mode='walking'))
    monkeypatch.setattr(g, 'SemanticMapper', lambda *a: pytest.fail('Named destination loaded category model'))
    request = QueryRequest('去小蛮腰观光', (113.3,23.1), 1000, 10, config_path=path, planning=True)
    engine = QueryEngine()
    try:
        result = engine.run(request)
        assert result.stops[0].candidates[0]['name'] == '广州塔'
        assert result.stops[0].candidates[0]['name_rank'] == 1
        assert result.stops[0].selected is None and '正式名称' in result.note
        with pytest.raises(g.GeoaskError):
            route_points(result)
        chosen = engine.reselect(result, 0, 0)
        assert chosen.stops[0].selected['name'] == '广州塔'
        assert route_points(chosen)[1] == (113.3309,23.1122)
        missing = intent.model_copy(update={'place_candidates':['不存在的高塔']})
        monkeypatch.setattr(g, 'parse_plan', lambda *a, **kw: g.GeoPlan(stops=[missing], travel_mode='walking'))
        assert not engine.run(request).stops[0].candidates
    finally:
        engine.close()


def test_explicit_alias_wins_over_model_suggestion_and_fuzzy_name(tmp_path):
    path = region(tmp_path, 'local', [feature('本地场馆', [113.3,23.1], alt_name='小场馆'),
                                    feature('另一个场馆', [113.4,23.2]),
                                    feature('小场馆附近商店', [113.31,23.11])])
    settings = g.read_settings(path)
    with g.load_data(settings) as con:
        rows = places.search_place(con, '小场馆', (113.3,23.1), 10, settings, ['另一个场馆'])
        assert len(rows) == 1 and rows[0]['name'] == '本地场馆' and rows[0]['name_rank'] == 0


def test_switching_regions_and_reloading_same_file_rebuilds_name_index(tmp_path, monkeypatch):
    first = region(tmp_path, 'shanghai', [feature('东方明珠', [121.499,31.239])])
    second = region(tmp_path, 'tokyo', [feature('東京スカイツリー', [139.81,35.71], **{'name:zh':'东京晴空塔'})])
    monkeypatch.setattr(g, 'parse_plan', lambda *a, **kw: g.GeoPlan(stops=[g.GeoIntent(
        intent='参观', modifiers=[], place_name=a[0])], travel_mode='walking'))
    engine = QueryEngine()
    request = QueryRequest('东方明珠', (121.49,31.23), 1000, 10, config_path=first, planning=True)
    try:
        assert engine.run(request).stops[0].selected['name'] == '东方明珠'
        assert not engine.run(replace(request, config_path=second)).rows
        tokyo = replace(request, query='东京晴空塔', center=(139.8,35.7), config_path=second)
        assert engine.run(tokyo).stops[0].selected['name'] == '東京スカイツリー'
        source = tmp_path / 'tokyo.geojson'
        payload = json.loads(source.read_text(encoding='utf-8'))
        payload['features'][0]['properties']['name:zh'] = '全新的中文别称'
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        assert not engine.run(tokyo).rows
        assert engine.run(replace(tokyo, query='全新的中文别称')).stops[0].selected
    finally:
        engine.close()


def test_primary_file_change_disables_bound_beijing_catalog(tmp_path):
    path = region(tmp_path, 'shanghai', [feature('东方明珠', [121.499,31.239])])
    payload = yaml.safe_load(path.read_text(encoding='utf-8'))
    payload.update(place_catalog_path=str(g.ROOT/'data/beijing_places.gpkg'),
                   place_catalog_for=str(g.ROOT/'data/beijing_poi.gpkg'), place_aliases={'鸟巢':'国家体育场'})
    path.write_text(yaml.safe_dump(payload), encoding='utf-8')
    settings = g.read_settings(path)
    assert settings.place_catalog_path is None and settings.place_aliases == {}
    with g.load_data(settings) as con:
        assert not places.search_place(con, '鸟巢', (121.49,31.23), 10, settings)


def test_regional_catalog_has_independent_fields_and_crs(tmp_path):
    path = region(tmp_path, 'main', [feature('起点', [0,0])])
    catalog_config = region(tmp_path, 'projected', [feature('外地场馆', [111319.490793,222684.208506], alt_name='别称')])
    settings = g.read_settings(path)
    settings.place_catalog_path = catalog_config.with_suffix('.geojson')
    settings.place_catalog_layer = None
    settings.place_catalog_field_mapping = g.FieldMapping(name='title', category=None)
    settings.place_catalog_source_crs = 'EPSG:3857'
    with g.load_data(settings) as con:
        rows = places.search_place(con, '别称', (1,2), 10, settings)
        assert rows[0]['longitude'] == pytest.approx(1)
        assert rows[0]['latitude'] == pytest.approx(2)
        assert rows[0]['dist_m'] < .001


def test_import_creates_independent_region_and_validates_before_saving(tmp_path):
    path = region(tmp_path, 'shanghai', [feature('东方明珠', [121.499,31.239])])
    base = g.read_settings(g.ROOT / 'config.yaml')
    destination = tmp_path / 'imported.yaml'
    imported, count, named = import_region(path.with_suffix('.geojson'), destination, base, name='title', category=None)
    assert imported == destination and count == named == 1
    settings = g.read_settings(destination)
    assert settings.place_catalog_path is settings.place_catalog_for is None
    assert settings.place_aliases == {}
    assert settings.default_location == (121.499,31.239)
    assert base.place_catalog_path is not None  # Original config remains unchanged.
    with g.load_data(settings) as con:
        assert places.search_place(con, '东方明珠', settings.default_location, 10, settings)
    invalid = tmp_path/'invalid.yaml'
    with pytest.raises(g.GeoaskError, match='字段映射'):
        import_region(path.with_suffix('.geojson'), invalid, base, name='missing', category=None)
    assert not invalid.exists()
    before = destination.read_bytes()
    with pytest.raises(g.GeoaskError, match='已存在'):
        import_region(path.with_suffix('.geojson'), destination, base)
    assert destination.read_bytes() == before


def test_parser_checks_candidate_schema():
    for bad in (['x']*5, [''], [123]):
        with pytest.raises(ValueError):
            g.GeoIntent(intent='参观', modifiers=[], place_name='地标', place_candidates=bad)
    with pytest.raises(ValueError):
        g.GeoIntent(intent='参观', modifiers=[], place_name='地标', longitude=123)
