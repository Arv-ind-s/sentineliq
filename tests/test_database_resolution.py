from pathlib import Path

import pytest

from core.config import ROOT
from core.db import get_engine, sqlite_path
from core.ingest import database_path


def test_all_sqlite_clients_resolve_relative_urls_from_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    url = "sqlite:///data/plant.db"
    assert sqlite_path(url) == database_path(url) == ROOT / "data/plant.db"
    assert Path(get_engine(url).url.database) == ROOT / "data/plant.db"


@pytest.mark.parametrize("url", ["postgresql://localhost/plant", "sqlite:///:memory:", "sqlite:///plant.db?mode=ro"])
def test_unsupported_database_urls_fail_before_connecting(url):
    with pytest.raises(ValueError, match="file-backed SQLite"):
        get_engine(url)
