import json
from datetime import datetime, timedelta, timezone

from newsletter_collect.curate import load_seen
from newsletter_collect.run import gather, list_sources, load_config, load_pool, merge_pool, prune_raw
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


def news(n: int, rel: float = 2.0, source: str = "s") -> dict:
    return {"uid": f"u{n}", "title": f"title {n}", "url": f"https://x.example/{n}", "source": source, "topic": "t",
            "summary": "", "published": datetime.now(timezone.utc).isoformat(), "weight": 1.0, "baseline_relevance": rel}


def test_pool_rounds(tmp_path):
    """白天一輪：過門檻的進池、不記 seen、原文失敗的下一輪再試、超過 POOL_DAYS 的丟掉；晨報併進來時用池裡的。"""
    path, cfg = tmp_path / "pool.json", {"min_relevance": 1.0}
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    path.write_text(json.dumps({"stale": {**news(9), "uid": "stale", "pooled_at": old}}), encoding="utf-8")
    hits: list[str] = []

    def get(url: str) -> str:
        hits.append(url)
        if url.endswith("/1") and hits.count(url) == 1:
            raise OSError("timeout")
        return "<article><p>" + "內文很長的一段，" * 20 + "</p></article>"

    raw = [news(0), news(1), news(2, rel=0.1), {**news(3), "source": "arXiv cs.CL"}]
    assert gather(raw, cfg, path, get) == {"added": 2, "pool": 2, "no_fulltext": 1}
    assert load_seen() == {}
    assert gather([news(0)], cfg, path, get) == {"added": 0, "pool": 2, "no_fulltext": 0}
    assert hits.count("https://x.example/0") == 1  # 有原文的不重抓

    pool = load_pool(path)
    merged = merge_pool([news(0), news(5)], pool)
    assert [i["uid"] for i in merged] == ["u0", "u5", "u1"] and "fulltext" in merged[0]
