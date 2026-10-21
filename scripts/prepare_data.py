"""Reproduce the bundled subset from a dated, public Geofabrik download."""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoask import ROOT, connect_spatial

URL = 'https://download.geofabrik.de/asia/china/beijing-261020-free.gpkg.zip'
BBOX = (116.44, 39.904, 116.48, 39.936)
CATEGORIES = ('cafe', 'restaurant', 'park', 'railway_station', 'supermarket', 'fitness_centre')


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main():
    downloads = ROOT / '.downloads'
    downloads.mkdir(exist_ok=True)
    archive = downloads / URL.rsplit('/', 1)[-1]
    if not archive.exists():
        print('Downloading', URL, flush=True)
        temporary = archive.with_suffix('.partial')
        with urllib.request.urlopen(URL, timeout=120) as response, temporary.open('wb') as output:
            import shutil
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
    with zipfile.ZipFile(archive) as z:
        member = next(n for n in z.namelist() if n.endswith('.gpkg'))
        # Extract only a known basename, never arbitrary archive paths.
        source = downloads / 'source' / Path(member).name
        source.parent.mkdir(exist_ok=True)
        if not source.exists():
            with z.open(member) as contents, source.open('wb') as output:
                import shutil
                shutil.copyfileobj(contents, output)
        (ROOT / 'data' / 'GEOFABRIK_README.txt').write_bytes(z.read('README'))

    con = connect_spatial()
    try:
        con.execute('''CREATE TABLE poi (osm_id VARCHAR, source_layer VARCHAR,
                       name VARCHAR, fclass VARCHAR, geom GEOMETRY)''')
        for layer in ('gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free'):
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
            raise RuntimeError(f'Unexpected subset size: {count}; inspect upstream before publishing.')
        output = ROOT / 'data' / 'beijing_poi.gpkg'
        # Write to a temporary dataset then replace, preserving the old data on failure.
        import tempfile
        with tempfile.TemporaryDirectory(dir=downloads) as tmp:
            generated = Path(tmp) / 'beijing_poi.gpkg'
            con.execute(f'''COPY poi TO {literal(str(generated))}
                (FORMAT GDAL, DRIVER 'GPKG', LAYER_NAME 'poi', SRS 'EPSG:4326',
                 LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom')''')
            generated.replace(output)
        distribution = dict(con.execute('SELECT fclass,count(*) FROM poi GROUP BY fclass ORDER BY fclass').fetchall())
        report = {
            'source_url': URL, 'source_date': '2026-10-20',
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'subset_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'bbox_wgs84': list(BBOX), 'center_wgs84': [116.46, 39.92],
            'rows': count, 'named_rows': named, 'categories': distribution,
            'layers': ['gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free'],
            'polygon_policy': 'ST_PointOnSurface; distances to representative point, not entrance',
            'attribution': '© OpenStreetMap contributors; Geofabrik',
            'license': 'ODbL-1.0', 'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/',
        }
        (ROOT / 'data' / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        con.close()


if __name__ == '__main__':
    main()
