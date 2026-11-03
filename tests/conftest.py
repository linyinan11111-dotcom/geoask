"""Tests must never read, replace or delete the user's actual saved API key."""
import pytest
import geoask_credentials as credentials


@pytest.fixture(scope='session', autouse=True)
def isolated_connection_store(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    path = tmp_path_factory.mktemp('connection-session') / 'connection.dat'
    patch.setattr(credentials, 'connection_file', lambda: path)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def fresh_connection_store(tmp_path, monkeypatch, isolated_connection_store):
    path = tmp_path / 'private' / 'connection.dat'
    monkeypatch.setattr(credentials, 'connection_file', lambda: path)
