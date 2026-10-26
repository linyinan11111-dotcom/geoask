"""Parking searches use the final destination and actual regional category data."""
from dataclasses import replace
import json

import pytest
import yaml

import geoask as g
from geoask_parking import parking_route_points
from geoask_service import QueryEngine, QueryRequest, route_points


def test_parking_limit_reports_total_count(trip):
    engine, result = trip
    limited = replace(result, request=replace(result.request, limit=1))
    options = engine.find_parking(limited, 1000)
    assert len(options.candidates) == 1 and options.total_count == 2
    assert '2' in options.summary and '最近 1 个' in options.summary


@pytest.fixture
def trip(tmp_path, monkeypatch):
    features = []
    # All are synthetic. Origin-near parking must not outrank destination-near parking.
    for name, category, lon, lat in [
        ('第一站', 'venue', 116.41,39.91), ('目的地', 'venue',116.46,39.92),
        (None, 'parking', 116.4602,39.9201), ('目的地停车场', 'parking',116.461,39.921),
        ('起点停车场', 'parking',116.401,39.901), ('自行车停车区','parking_bicycle',116.46,39.92),
        ('汽车商店','car_dealership',116.46,39.92), ('别处停车场','parking',116.5,39.95)]:
        features.append({'type':'Feature','properties':{'name':name,'kind':category},
                         'geometry':{'type':'Point','coordinates':[lon,lat]}})
    source = tmp_path/'places.geojson'
    source.write_text(json.dumps({'type':'FeatureCollection','features':features}, ensure_ascii=False), encoding='utf-8')
    config = tmp_path/'region.yaml'
    config.write_text(yaml.safe_dump({'geojson_path':source.name, 'layer':None,
        'field_mapping':{'name':'name','category':'kind','geometry':'geom'}}), encoding='utf-8')
    plan = g.GeoPlan(stops=[g.GeoIntent(intent='参观', modifiers=[],place_name=n) for n in ('第一站','目的地')], travel_mode='walking')
    monkeypatch.setattr(g,'parse_plan', lambda *a, **kw:plan)
    monkeypatch.setattr(g,'SemanticMapper', lambda *a:pytest.fail('Parking loaded category embedding'))
    engine = QueryEngine()
    request = QueryRequest('先去第一站再去目的地', (116.4,39.9),1000,10, config_path=config, planning=True)
    result = engine.run(request)
    yield engine, result
    engine.close()


def test_parking_near_final_destination_includes_unnamed_and_excludes_bikes(trip):
    engine, result = trip
    options = engine.find_parking(result,500)
    assert options.result is result and options.destination['name']=='目的地'
    assert len(options.candidates)==2 and options.candidates[0]['name'] is None
    assert [r['dist_m'] for r in options.candidates] == sorted(r['dist_m'] for r in options.candidates)
    assert all(r['category']=='parking' and r['dist_m']<=500 for r in options.candidates)
    assert result.stops[-1].selected['name']=='目的地'


def test_parking_changes_only_last_driving_endpoint(trip):
    engine,result=trip
    options=engine.find_parking(result,500)
    before=route_points(result)
    points=parking_route_points(options,1)
    assert points[:-1]==before[:-1] and len(points)==len(before)
    assert points[-1]==(options.candidates[1]['longitude'],options.candidates[1]['latitude'])
    assert route_points(result)==before  # User's actual destinations are preserved.
    for bad in (-1,2,True,None):
        with pytest.raises(g.GeoaskError):
            parking_route_points(options,bad)


def test_missing_or_unresolved_parking_does_not_fabricate_point(trip):
    engine,result=trip
    options=engine.find_parking(result,1)
    assert not options.candidates
    with pytest.raises(g.GeoaskError):
        parking_route_points(options,0)
    for radius in (0,-1,5001,float('nan'),float('inf')):
        with pytest.raises(g.GeoaskError):
            engine.find_parking(result,radius)
    for invalid in (replace(result,request=replace(result.request,dry_run=True)),
                    replace(result,stops=[*result.stops[:-1],replace(result.stops[-1],selected_index=None)])):
        with pytest.raises(g.GeoaskError):
            engine.find_parking(invalid,500)


def test_custom_category_mapping_and_regional_catalog(trip,tmp_path):
    engine,result=trip
    config=result.request.config_path
    payload=yaml.safe_load(config.read_text(encoding='utf-8'))
    supplemental=tmp_path/'parking.geojson'
    supplemental.write_text(json.dumps({'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'title':'地下车库', 'type':'车库'},
         'geometry':{'type':'Point','coordinates':[116.4601,39.9201]}}
    ]},ensure_ascii=False),encoding='utf-8')
    payload.update(place_catalog_path=supplemental.name,place_catalog_layer=None,
        place_catalog_field_mapping={'name':'title','category':'type','geometry':'geom'},
        parking={'categories':['车库'], 'radius':500})
    config.write_text(yaml.safe_dump(payload,allow_unicode=True),encoding='utf-8')
    options=engine.find_parking(result,500)
    assert len(options.candidates)==1 and options.candidates[0]['name']=='地下车库'
    # Switching data categories cannot fall back to the old cached candidates.
    payload['parking']['categories']=["车库' OR 1=1 --"]
    config.write_text(yaml.safe_dump(payload,allow_unicode=True),encoding='utf-8')
    assert not engine.find_parking(result,500).candidates
