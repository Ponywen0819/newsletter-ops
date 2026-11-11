import pytest

from newsletter_collect import curate


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """不碰真的 state/seen.json 與回饋。"""
    monkeypatch.setattr(curate, "STATE", tmp_path / "seen.json")
    monkeypatch.setattr(curate, "FEEDBACK", tmp_path / "feedback.jsonl")
