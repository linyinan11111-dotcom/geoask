"""The frozen runtime must load its version-matched bundled extension first."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import geoask as g


def test_frozen_extension_loads_bundled_path_and_closes_on_failure(monkeypatch):
    con = MagicMock()
    monkeypatch.setattr(g.duckdb, 'connect', lambda: con)
    monkeypatch.setattr(g.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(g, 'resource_file', lambda *parts: Path("C:/test's app/spatial.duckdb_extension"))
    assert g.connect_spatial() is con
    sql = con.execute.call_args_list[0].args[0]
    assert "test''s app" in sql and 'spatial.duckdb_extension' in sql
    con.execute.side_effect = g.duckdb.Error('invalid extension')
    with pytest.raises(g.GeoaskError, match='无法加载'):
        g.connect_spatial()
    con.close.assert_called_once()
