"""Tests must never read, replace or delete the user's actual saved API key."""
import pytest
import geoask as g
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


@pytest.fixture(autouse=True)
def legacy_local_provider(monkeypatch):
    """Keep the historical local-OSM regression suite offline.

    Production config.yaml selects strict Amap; tests that exercise the local
    engine opt out here, while dedicated Amap tests patch read_settings again.
    """
    original = g.read_settings

    def local_settings(path):
        settings = original(path)
        settings.amap.primary = False
        settings.routing.provider = "valhalla"
        return settings

    monkeypatch.setattr(g, 'read_settings', local_settings)
