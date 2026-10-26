"""Remaining review regressions: publication races, damaged sources and UI contracts."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import importlib
import json
import logging
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import zipfile

import numpy as np
import pytest
from pydantic import ValidationError

import geoask as g
import geoask_runtime as runtime
from scripts import data_pipeline as pipeline


def test_non_subscriptable_event_recovers_and_delivers_next(caplog):
    events = Queue()
    events.put(None)
    events.put(('progress', 'after malformed event'))
    seen, recovery = [], []
    def handle(event):
        if event is None:
            raise ValueError('malformed')
        seen.append(event)
    assert runtime.drain_events(events, handle, lambda: recovery.append(True)) == 2
    assert recovery == [True] and seen == [('progress', 'after malformed event')]
    assert 'malformed' in caplog.text


def test_concurrent_logger_initialization_has_one_handler(tmp_path, monkeypatch):
    logger = logging.Logger('isolated')
    monkeypatch.setattr(runtime, 'logging', NS(getLogger=lambda _: logger, INFO=logging.INFO,
                                            NullHandler=logging.NullHandler, Formatter=logging.Formatter))
    monkeypatch.setattr(runtime, 'user_cache_dir', lambda: tmp_path)
    barrier = Barrier(8)
    def get(_):
        barrier.wait()
        return runtime.get_logger()
    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            assert all(item is logger for item in workers.map(get, range(8)))
        assert len(logger.handlers) == 1
    finally:
        for handler in logger.handlers:
            handler.close()


def test_new_file_publication_race_never_overwrites_winner(tmp_path):
    target = tmp_path / 'new.yaml'
    barrier = Barrier(6)
    def write(index):
        try:
            with runtime.atomic_output(target, overwrite=False) as output:
                output.write(str(index) * 2000)
                barrier.wait()
                assert not target.exists() or target.stat().st_size > 0
            return index
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=6) as workers:
        winners = [i for i in workers.map(write, range(6)) if i is not None]
    assert len(winners) == 1 and target.read_text() == str(winners[0]) * 2000
    assert list(tmp_path.iterdir()) == [target]


def test_killed_writer_does_not_expose_empty_destination(tmp_path):
    target = tmp_path / 'new.yaml'
    code = "from geoask_runtime import atomic_output\nimport os,sys\nwith atomic_output(sys.argv[1],overwrite=False) as f:\n f.write('not yet published')\n f.flush()\n os._exit(12)"
    result = subprocess.run([sys.executable, '-c', code, str(target)], cwd=g.ROOT, timeout=15)
    assert result.returncode == 12 and not target.exists()


def test_atomic_cleanup_failure_does_not_mask_original(tmp_path, monkeypatch):
    target = tmp_path / 'value.txt'
    monkeypatch.setattr(Path, 'unlink', MagicMock(side_effect=PermissionError('locked temporary')))
    with pytest.raises(RuntimeError, match='original'):
        with runtime.atomic_output(target) as output:
            output.write('partial')
            raise RuntimeError('original')
    assert not target.exists()


def test_atomic_parents_and_posix_permissions(tmp_path):
    target = tmp_path / 'new' / 'value.txt'
    with runtime.atomic_output(target, parents=True) as output:
        output.write('first')
    if os.name != 'nt':
        target.chmod(0o640)
    with runtime.atomic_output(target) as output:
        output.write('second')
    assert target.read_text() == 'second'
    if os.name != 'nt':
        assert target.stat().st_mode & 0o777 == 0o640


def archive(tmp_path, entries=None):
    path = tmp_path / 'source.zip'
    with zipfile.ZipFile(path, 'w') as bundle:
        for name, data in (entries or {'nested/Beijing.GPKG': b'complete source bytes', 'nested/README.txt': b'OSM credit'}).items():
            bundle.writestr(name, data)
    return path


def test_partial_extraction_repaired_even_when_same_size(tmp_path):
    path = archive(tmp_path)
    args = dict(url='https://example.test/source.zip', expected_hash=pipeline.sha256(path))
    _, source, readme = pipeline.source_data(tmp_path, **args)
    expected = source.read_bytes()
    assert readme == b'OSM credit'
    source.write_bytes(b'X' * len(expected))
    _, repaired, _ = pipeline.source_data(tmp_path, **args)
    assert repaired == source and source.read_bytes() == expected
    assert list(source.parent.iterdir()) == [source]


@pytest.mark.parametrize('entries', [{'README': b'readme'}, {'a.gpkg': b'a', 'b.gpkg': b'b', 'README': b'r'}, {'a.gpkg': b'a'}])
def test_archive_layout_change_is_actionable_before_extraction(tmp_path, entries):
    path = archive(tmp_path, entries)
    with pytest.raises(g.GeoaskError, match='布局'):
        pipeline.source_data(tmp_path, url='https://example.test/source.zip', expected_hash=pipeline.sha256(path))
    assert not (tmp_path / 'source' / 'beijing.gpkg').exists()


def test_download_hash_failure_keeps_no_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.urllib.request, 'urlopen', lambda *a, **kw: BytesIO(b'bad download'))
    with pytest.raises(g.GeoaskError, match='SHA256'):
        pipeline.source_data(tmp_path, url='https://example.test/source.zip', expected_hash='0' * 64)
    assert not (tmp_path / 'source.zip').exists() and not list(tmp_path.iterdir())


def test_bad_cached_hash_does_not_reuse_or_redownload(tmp_path, monkeypatch):
    archive(tmp_path)
    monkeypatch.setattr(pipeline.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('unrequested redownload'))
    with pytest.raises(g.GeoaskError, match='缓存归档校验失败'):
        pipeline.source_data(tmp_path, url='https://example.test/source.zip', expected_hash='0' * 64)


@pytest.mark.parametrize('existing', [False, True])
def test_manifest_publish_failure_restores_previous_dataset(tmp_path, monkeypatch, existing):
    generated, target, manifest = [tmp_path / name for name in ['generated.gpkg', 'published.gpkg', 'manifest.json']]
    generated.write_bytes(b'new dataset')
    if existing:
        target.write_bytes(b'previous dataset')
    manifest.write_text('{"previous":true}')
    original = os.replace
    def replace(source, destination):
        if Path(destination) == manifest:
            raise PermissionError('locked manifest')
        original(source, destination)
    monkeypatch.setattr(os, 'replace', replace)
    with pytest.raises(PermissionError, match='locked manifest'):
        pipeline.publish_dataset(generated, target, {'new': True}, manifest)
    assert target.read_bytes() == b'previous dataset' if existing else not target.exists()
    assert json.loads(manifest.read_text()) == {'previous': True}
    assert not list(tmp_path.glob('.geoask-publish-*'))


def test_dataset_and_manifest_published_with_matching_hash(tmp_path):
    generated, target, manifest = [tmp_path / name for name in ['generated.gpkg', 'output/published.gpkg', 'output/manifest.json']]
    generated.write_bytes(b'new dataset')
    pipeline.publish_dataset(generated, target, {'sha256': pipeline.sha256(generated)}, manifest)
    assert pipeline.sha256(target) == json.loads(manifest.read_text())['sha256']


def test_missing_verified_venue_leaves_existing_catalogue(tmp_path, monkeypatch):
    from scripts import prepare_places
    output = tmp_path / 'beijing_places.gpkg'
    output.write_bytes(b'previous catalogue')
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(prepare_places, 'source_data', lambda *a: (tmp_path / 'source.zip', tmp_path / 'source.gpkg', b''))
    monkeypatch.setattr(prepare_places, 'resolve_layer', lambda con, path, requested: requested)
    context = MagicMock()
    context.__enter__.return_value = con
    monkeypatch.setattr(prepare_places, 'connect_spatial', lambda: context)
    with pytest.raises(g.GeoaskError, match='应唯一'):
        prepare_places.main(['--output-dir', str(tmp_path)])
    assert output.read_bytes() == b'previous catalogue'
    assert not (tmp_path / 'places-manifest.json').exists()
    assert not any(str(call.args[0]).startswith('COPY') for call in con.execute.call_args_list)


def test_inspection_import_does_not_access_database(monkeypatch):
    monkeypatch.setattr(g, 'connect_spatial', lambda: pytest.fail('inspection ran during import'))
    import scripts.inspect_named_places as inspect
    importlib.reload(inspect)


@pytest.mark.parametrize('values', [['x'] * 17, ['x' * 101], ['']])
def test_unbounded_or_blank_modifiers_are_rejected(values):
    with pytest.raises(ValidationError):
        g.GeoIntent(intent='咖啡', modifiers=values)


def test_e5_query_passage_prefix_and_cache_separation(tmp_path):
    seen = []
    class Embedder:
        def embed(self, texts):
            texts = list(texts)
            seen.extend(texts)
            return [np.array([1., 0.]) for _ in texts]
    settings = g.Settings(cache_dir=tmp_path, embedding_model='intfloat/multilingual-e5-small')
    mapper = g.SemanticMapper(['cafe'], settings, Embedder())
    assert mapper.match('咖啡').category == 'cafe'
    assert seen == ['passage: cafe', 'query: 咖啡']


def test_parser_requires_completed_plan_and_explicit_travel_mode():
    client = MagicMock()
    completion = NS(choices=[NS(finish_reason='length', message=NS(refusal=None, content='{"stops":[{"intent":"咖啡","modifiers":[]}],"travel_mode":"walking"}'))])
    client.chat.completions.create.return_value = completion
    with pytest.raises(g.GeoaskError, match='不完整'):
        g.parse_plan('咖啡', g.LLMSettings(), client)
    completion.choices[0].finish_reason = 'stop'
    completion.choices[0].message.content = '{"stops":[{"intent":"咖啡","modifiers":[]}]}'
    with pytest.raises(g.GeoaskError, match='解析失败'):
        g.parse_plan('咖啡', g.LLMSettings(), client)


def test_null_transformed_coordinate_rejected(tmp_path, monkeypatch):
    source = tmp_path / 'source.geojson'
    source.touch()
    monkeypatch.setattr(g, 'source_select', lambda *a: (
        "SELECT 'place' AS name, 'cafe' AS category, []::VARCHAR[] AS aliases, ST_Point(NULL, NULL) AS geometry", []))
    with pytest.raises(g.GeoaskError, match='无效经纬度'):
        g.load_data(g.Settings(geojson_path=source))
