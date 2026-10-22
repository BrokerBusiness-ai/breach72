import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path_factory, monkeypatch):
    """Each test gets an empty organisational memory (H-MEM) unless it sets HMEM_DIR itself."""
    monkeypatch.setenv("HMEM_DIR", str(tmp_path_factory.mktemp("hmem")))
