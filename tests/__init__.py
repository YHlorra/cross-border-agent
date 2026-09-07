"""Pytest fixtures for agent tests."""
from pathlib import Path

import pytest

from agent.tools import LocalJSONAdapter


@pytest.fixture
def data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "selection"


@pytest.fixture
def adapter(data_dir) -> LocalJSONAdapter:
    return LocalJSONAdapter(data_dir=data_dir)