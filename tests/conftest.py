import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path_factory, monkeypatch):
    """Each test gets an empty organisational memory (H-MEM) unless it sets HMEM_DIR itself."""
    monkeypatch.setenv("HMEM_DIR", str(tmp_path_factory.mktemp("hmem")))


@pytest.fixture(autouse=True)
def _no_local_platform(monkeypatch):
    """A developer .env may point at the local Zetaris platform (UI proxy route).
    Tests never reach it; tests of that route set ZETARIS_BASE_URL themselves."""
    monkeypatch.delenv("ZETARIS_BASE_URL", raising=False)
