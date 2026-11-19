import importlib.util

import pytest

from basilisk_mcp import spec as S
from basilisk_mcp.config import load_settings
from basilisk_mcp.manager import replay
from basilisk_mcp.templates import template_spec

HAVE_BASILISK = importlib.util.find_spec("Basilisk") is not None

requires_basilisk = pytest.mark.skipif(not HAVE_BASILISK, reason="Basilisk (pip install bsk) is not installed")


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path / "data")


@pytest.fixture
def make_spec(settings):
    def build(template=None):
        return replay(template_spec(template), settings) if template else S.new_spec()

    return build
