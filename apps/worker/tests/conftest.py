import os

import pytest
from helpers import fake_steps

os.environ["NEWSLETTER_DEBUG"] = "0"  # 假的執行不進 metrics


@pytest.fixture
def steps(tmp_path):
    with fake_steps(tmp_path):
        yield tmp_path
