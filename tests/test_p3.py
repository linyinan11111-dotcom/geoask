"""Runtime file publication and parser schema limits."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import geoask as g
import geoask_runtime as runtime


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


@pytest.mark.parametrize('values', [['x'] * 17, ['x' * 101], ['']])
def test_unbounded_or_blank_modifiers_are_rejected(values):
    with pytest.raises(ValidationError):
        g.GeoIntent(intent='咖啡', modifiers=values)


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
