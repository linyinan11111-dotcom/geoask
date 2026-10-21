"""Extract a compact Beijing named-destination catalogue from the existing dated source."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoask import ROOT, connect_spatial
from scripts.prepare_data import URL, literal


def main():
    source = ROOT / '.downloads/source/beijing.gpkg'
    if not source.is_file():
        raise SystemExit('先运行 python scripts/prepare_data.py 获取日期固定的北京源数据。')
    with connect_spatial() as con:
        con.execute('''CREATE TABLE places (osm_id VARCHAR, source_layer VARCHAR,
                       name VARCHAR, fclass VARCHAR, geom GEOMETRY)''')
        layers = ['gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free',
                  'gis_osm_traffic_free', 'gis_osm_traffic_a_free']
        for layer in layers:
            con.execute('''INSERT INTO places
                SELECT osm_id, ?, name, fclass, ST_PointOnSurface(geom)
                FROM ST_Read(?,layer=?) WHERE ((name IS NOT NULL AND trim(name) <> '') OR fclass IN ('parking','parking_underground','parking_multistorey'))
                AND geom IS NOT NULL AND NOT ST_IsEmpty(geom)
                AND fclass NOT IN ('bus_stop','taxi')
                AND (? OR fclass IN ('parking','parking_underground','parking_multistorey'))''', [layer, str(source), layer, 'traffic' not in layer])
        # Collapse repeated classifications of the same OSM feature, preferring a venue type.
        con.execute('''CREATE TABLE unique_places AS SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY osm_id, name ORDER BY
                CASE WHEN fclass = 'stadium' THEN 0 WHEN fclass = 'attraction' THEN 2 ELSE 1 END,
                fclass, source_layer) rn FROM places) WHERE rn=1''')
        output = ROOT / 'data/beijing_places.gpkg'
        with tempfile.TemporaryDirectory(dir=ROOT / '.downloads') as tmp:
            generated = Path(tmp) / output.name
            con.execute(f'''COPY unique_places TO {literal(str(generated))}
                (FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME 'places', SRS 'EPSG:4326',
                 LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom')''')
            generated.replace(output)
        bird = con.execute("SELECT osm_id,name,fclass,ST_X(geom),ST_Y(geom) FROM unique_places WHERE name = ?",
                           ['国家体育场']).fetchall()
        if len(bird) != 1:
            raise RuntimeError(f'请核对国家体育场源数据：{bird!r}')
        report = {
            'source_url': URL, 'source_date': '2026-10-20', 'layers': layers,
            'source_archive_sha256': hashlib.sha256((ROOT / '.downloads' / URL.rsplit('/',1)[-1]).read_bytes()).hexdigest(),
            'catalog_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'rows': con.execute('SELECT count(*) FROM unique_places').fetchone()[0],
            'bytes': output.stat().st_size, 'crs': 'EPSG:4326',
            'selection': 'Named Beijing POI points/polygons and transport, plus unnamed parking; excluding bus_stop/taxi; deduplicated by osm_id/name',
            'scope': 'Explicit destination names and optional nearby parking; nearby-category queries still use geojson_path',
            'parking_categories': dict(con.execute("SELECT fclass,count(*) FROM unique_places WHERE fclass LIKE 'parking%' GROUP BY fclass").fetchall()),
            'polygon_policy': 'ST_PointOnSurface; not a verified entrance',
            'verified_alias': {'鸟巢': {'canonical_name': bird[0][1], 'osm_id': bird[0][0],
                                       'category': bird[0][2], 'coordinate': list(bird[0][3:])}},
            'attribution': '© OpenStreetMap contributors; Geofabrik', 'license': 'ODbL-1.0',
        }
        (ROOT / 'data/places-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
