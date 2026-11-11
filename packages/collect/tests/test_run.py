from newsletter_collect.run import list_sources, load_config, prune_raw
from newsletter_shared import ROOT


def test_prune_raw(tmp_path):
    for name in ("2026-10-08.jsonl", "2026-10-09.jsonl", "2026-11-08.jsonl", "notes.jsonl", "2026-01-01.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert prune_raw("2026-11-08", 30, tmp_path) == ["2026-10-08.jsonl"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-01-01.txt", "2026-10-09.jsonl", "2026-11-08.jsonl", "notes.jsonl"]
    assert prune_raw("2026-11-08", 30, tmp_path) == []


def test_repo_sources_load(capsys):
    """repo 的 config 與來源清單載入得了（不連網）。"""
    list_sources(load_config(ROOT / "config" / "config.json"))
    assert capsys.readouterr().out
