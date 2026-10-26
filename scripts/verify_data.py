"""Rebuild both datasets in a temporary directory; leave bundled data unchanged."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoask import ROOT, connect_spatial
from geoask_runtime import atomic_output
from scripts.data_pipeline import sha256


def main():
    report = {'verified_at': datetime.now(timezone.utc).isoformat(),
              'source': 'dated Geofabrik archive; fixed SHA256 checked by source_data',
              'script_sha256': {name: sha256(ROOT / 'scripts' / name) for name in
                                ['data_pipeline.py', 'prepare_data.py', 'prepare_places.py', 'verify_data.py']},
              'datasets': {}}
    original = {p.name: sha256(p) for p in (ROOT / 'data').glob('*') if p.is_file()}
    with tempfile.TemporaryDirectory(prefix='geoask-data-') as temporary:
        folder = Path(temporary)
        for script, filename, hash_key, dataset in [
            ('prepare_data.py', 'manifest.json', 'subset_sha256', 'beijing_poi.gpkg'),
            ('prepare_places.py', 'places-manifest.json', 'catalog_sha256', 'beijing_places.gpkg')]:
            subprocess.run([sys.executable, str(ROOT / 'scripts' / script), '--output-dir', str(folder)],
                           cwd=ROOT, check=True, timeout=180)
            fresh = json.loads((folder / filename).read_text(encoding='utf-8'))
            bundled = json.loads((ROOT / 'data' / filename).read_text(encoding='utf-8'))
            assert fresh['rows'] == bundled['rows']
            assert fresh[hash_key] == sha256(folder / dataset)
            if 'categories' in fresh:
                assert fresh['categories'] == bundled['categories']
            else:
                assert fresh['verified_alias'] == bundled['verified_alias']
                assert fresh['parking_categories'] == bundled['parking_categories']
            with connect_spatial() as con:
                assert con.execute('SELECT count(*) FROM ST_Read(?)', [str(folder / dataset)]).fetchone()[0] == fresh['rows']
            report['datasets'][dataset] = {'rows': fresh['rows'], 'sha256': fresh[hash_key],
                                         'manifest_matches': True, 'bundled_content_statistics_match': True}
    assert original == {p.name: sha256(p) for p in (ROOT / 'data').glob('*') if p.is_file()}
    report['bundled_files_unchanged'] = True
    report['passed'] = True
    with atomic_output(ROOT / 'docs/data-verification.json') as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
