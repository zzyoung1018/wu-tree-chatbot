import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, state_dir=tmp_path, deepseek_api_key='test-only-key', access_password='')
