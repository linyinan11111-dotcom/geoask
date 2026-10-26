"""Reproduce the bundled subset from a dated, public Geofabrik download."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoask import ROOT, connect_spatial, resolve_layer, GeoaskError
from geoask_runtime import atomic_output
from scripts.data_pipeline import URL, source_data, sha256, publish_dataset

BBOX = (116.44, 39.904, 116.48, 39.936)
CATEGORIES = ('cafe', 'restaurant', 'park', 'railway_station', 'supermarket', 'fitness_centre')


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    downloads = ROOT / '.downloads'
    archive, source, readme = source_data(downloads)

    con = connect_spatial()
    try:
        con.execute('''CREATE TABLE poi (osm_id VARCHAR, source_layer VARCHAR,
                       name VARCHAR, fclass VARCHAR, geom GEOMETRY)''')
        for layer in ('gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free'):
            layer = resolve_layer(con, source, layer)
            # A park's representative point, rather than an arbitrary polygon corner,
            # is used consistently during clipping and later distance computation.
            con.execute('''INSERT INTO poi
                SELECT osm_id, ?, name, fclass, ST_PointOnSurface(geom)
                FROM ST_Read(?, layer = ?)
                WHERE fclass IN (SELECT unnest(?))
                  AND ST_Intersects(ST_PointOnSurface(geom), ST_MakeEnvelope(?, ?, ?, ?))''',
                [layer, str(source), layer, list(CATEGORIES), *BBOX])
        count, named = con.execute("SELECT count(*), count(nullif(trim(name), '')) FROM poi").fetchone()
        if not 200 <= count <= 500:
            raise GeoaskError(f'裁剪数量异常：{count}；请核对上游数据，旧样例未替换。')
        output = args.output_dir / 'beijing_poi.gpkg'
        with tempfile.TemporaryDirectory(dir=downloads) as tmp:
            generated = Path(tmp) / 'beijing_poi.gpkg'
            con.execute(f'''COPY poi TO {literal(str(generated))}
                (FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME 'poi', SRS 'EPSG:4326',
                 LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom')''')
            # Verify the written GDAL dataset before replacing any published file.
            layer = resolve_layer(con, generated, 'poi')
            if con.execute('SELECT count(*) FROM ST_Read(?, layer=?)', [str(generated), layer]).fetchone()[0] != count:
                raise GeoaskError('导出样例数量不一致，旧数据未替换。')
            report = make_report(con, archive, generated, count, named)
            publish_dataset(generated, output, report, args.output_dir / 'manifest.json')
        with atomic_output(args.output_dir / 'GEOFABRIK_README.txt', mode='wb') as stream:
            stream.write(readme)
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        con.close()


def make_report(con, archive, generated, count, named):
    distribution = dict(con.execute('SELECT fclass,count(*) FROM poi GROUP BY fclass ORDER BY fclass').fetchall())
    return {
        'source_url': URL, 'source_date': '2026-10-20',
        'archive_sha256': sha256(archive),
        'subset_sha256': sha256(generated),
        'bbox_wgs84': list(BBOX), 'center_wgs84': [116.46, 39.92],
        'rows': count, 'named_rows': named, 'categories': distribution,
        'layers': ['gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free'],
        'polygon_policy': 'ST_PointOnSurface; distances to representative point, not entrance',
        'attribution': '© OpenStreetMap contributors; Geofabrik',
        'license': 'ODbL-1.0', 'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/',
    }


if __name__ == '__main__':
    main()
