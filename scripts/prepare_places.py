"""Extract a compact Beijing named-destination catalogue from the existing dated source."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoask import ROOT, connect_spatial, resolve_layer, GeoaskError
from scripts.prepare_data import literal
from scripts.data_pipeline import URL, source_data, sha256, publish_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive, source, _ = source_data(ROOT / '.downloads')
    with connect_spatial() as con:
        con.execute('''CREATE TABLE places (osm_id VARCHAR, source_layer VARCHAR,
                       name VARCHAR, fclass VARCHAR, geom GEOMETRY)''')
        layers = ['gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free',
                  'gis_osm_traffic_free', 'gis_osm_traffic_a_free']
        for layer in layers:
            layer = resolve_layer(con, source, layer)
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
        # Validate all semantic invariants before writing/replacing the catalogue.
        bird = con.execute("SELECT osm_id,name,fclass,ST_X(geom),ST_Y(geom) FROM unique_places WHERE name = ?",
                           ['国家体育场']).fetchall()
        if len(bird) != 1:
            raise GeoaskError(f'国家体育场来源应唯一，当前 {len(bird)} 条；请核对数据，旧目录未替换。')
        output = args.output_dir / 'beijing_places.gpkg'
        with tempfile.TemporaryDirectory(dir=ROOT / '.downloads') as tmp:
            generated = Path(tmp) / output.name
            con.execute(f'''COPY unique_places TO {literal(str(generated))}
                (FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME 'places', SRS 'EPSG:4326',
                 LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom')''')
            layer = resolve_layer(con, generated, 'places')
            count = con.execute('SELECT count(*) FROM unique_places').fetchone()[0]
            if con.execute('SELECT count(*) FROM ST_Read(?,layer=?)', [str(generated), layer]).fetchone()[0] != count:
                raise GeoaskError('导出目录数量不一致，旧数据未替换。')
            report = {
                'source_url': URL, 'source_date': '2026-10-20', 'layers': layers,
                'source_archive_sha256': sha256(archive),
                'catalog_sha256': sha256(generated),
                'rows': con.execute('SELECT count(*) FROM unique_places').fetchone()[0],
                'bytes': generated.stat().st_size, 'crs': 'EPSG:4326',
                'selection': 'Named Beijing POI points/polygons and transport, plus unnamed parking; excluding bus_stop/taxi; deduplicated by osm_id/name',
                'scope': 'Explicit destination names and optional nearby parking; nearby-category queries still use geojson_path',
                'parking_categories': dict(con.execute("SELECT fclass,count(*) FROM unique_places WHERE fclass LIKE 'parking%' GROUP BY fclass").fetchall()),
                'polygon_policy': 'ST_PointOnSurface; not a verified entrance',
                'verified_alias': {'鸟巢': {'canonical_name': bird[0][1], 'osm_id': bird[0][0],
                                           'category': bird[0][2], 'coordinate': list(bird[0][3:])}},
                'attribution': '© OpenStreetMap contributors; Geofabrik', 'license': 'ODbL-1.0',
            }
            publish_dataset(generated, output, report, args.output_dir / 'places-manifest.json')
        print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
