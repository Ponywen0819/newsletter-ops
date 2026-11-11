import json
import os
from datetime import datetime, timezone

import pytest

from newsletter_collect import curate as mod
from newsletter_collect.curate import (
    _hits, _names, _pick, curate, dedupe, load_seen, near_misses, save_seen, score, source_boosts, venue_of,
)
from newsletter_shared import ROOT

V = {"conference": ["NeurIPS", "ICLR", "ACL", "EMNLP", "COLM"], "journal": ["Nature", "TMLR"],
     "minor_tracks": ["Workshop", "Findings", "Industry Track"]}
NOW = datetime.now(timezone.utc).isoformat()
REAL_STATE = mod.STATE  # 收集時還沒被 conftest 換掉


def test_state_path():
    """去重記憶寫在 repo 根的 state/，不是套件目錄。"""
    assert REAL_STATE == ROOT / "state" / "seen.json" and (ROOT / "state").parent == ROOT


@pytest.mark.parametrize(("note", "want"), [
    ("Accepted at NeurIPS 2026 (spotlight)", ("main", "NeurIPS 2026", "")),
    ("Oral at ICLR 2026", ("main", "ICLR 2026", "")),
    ("Under review at ICLR 2027", ("submitted", "ICLR 2027", "")),
    ("NeurIPS 2026 Workshop on Efficient ML", ("minor", "NeurIPS 2026", "Workshop")),
    ("Accepted to DocInsights at EMNLP 2026", ("minor", "EMNLP 2026", "DocInsights")),
    ("Accepted to EMNLP 2026 Industry Track. 19 pages", ("minor", "EMNLP 2026", "Industry Track")),
    ("Accepted at the ICLR 2026 workshop on agents", ("minor", "ICLR 2026", "Workshop")),
    ("Findings of ACL 2026", ("minor", "ACL 2026", "Findings")),
    ("Published in TMLR", ("main", "TMLR", "")),
    ("Accepted at COLM 2026. Project website: https://x", ("main", "COLM 2026", "")),
    ("12 pages; on the nature of attention", None),
    ("accepted at neurips 2026", ("main", "NeurIPS 2026", "")),
    ("Project page: https://www.example.com/acl", None),  # 小寫沒有年份不算
    ("12 pages, nature 2026 style", None),  # 期刊不放寬
    ("Rejected from ICLR 2026", None),
    ("Rejected from ICLR 2026; published in TMLR", ("main", "TMLR", "")),
    ("Accepted at NeurIPS 2026. Previously rejected elsewhere", ("main", "NeurIPS 2026", "")),
    ("ACLU report analysis", None),
    ("", None),
])
def test_venue(note, want):
    v = venue_of(note, V)
    assert (v and (v["status"], v["name"], v["track"])) == want


def test_venue_labels():
    assert venue_of("Accepted at NeurIPS 2026", V)["label"] == "已被 NeurIPS 2026 接受（主會議）"
    assert venue_of("Published in TMLR", V)["label"] == "已被 TMLR 接受（期刊）"
    assert venue_of("to appear in iclr 2027", V)["label"] == "已被 ICLR 2027 接受（主會議）"


def test_keyword_hits():
    assert _hits("openai agents tried to bruteforce", "agent") and _hits("new gpus", "gpu")
    assert _hits("ai agents ship", "ai agent") and _hits("two foundation models", "foundation model")
    assert not _hits("agentic workflows", "agent") and not _hits("agentsx", "agent")


def test_score():
    item = {"title": "x", "summary": "", "venue_note": "ICLR 2026 camera-ready"}
    assert score(item, {}, V)[0] == 2.0 and item["venue"]["status"] == "main"
    base = {"title": "x", "summary": "", "baseline_relevance": 1.5}
    assert score({**base, "order_boost": 1.0, "position": 0}, {})[1] == 2.5    # 只加 rank，不加 relevance
    assert score({**base, "order_boost": 1.0, "position": 0}, {})[0] == 1.5
    assert score({**base, "order_boost": 1.0, "position": 5}, {})[1] == 2.0
    assert score({**base, "order_boost": 1.0, "position": 12}, {})[1] == 1.5
    assert score({**base, "position": 0}, {})[1] == 1.5                        # 沒設 order_boost 不加


def test_dedupe_by_names():
    """標題＋摘要的名字夠重疊才合併；只共有 Trump、Iran 不合併；不在 name_topics 不合併。"""
    assert _names("Israel's president tasks opposition leader Eisenkot with forming government") == {"israel", "eisenkot"}

    def w(uid, source, title, summary, rank):
        return {"uid": uid, "url": f"https://{uid}", "source": source, "topic": "world", "title": title,
                "summary": summary, "rank": rank}
    batch = [w("b1", "BBC", "Israel's president tasks opposition leader Eisenkot with forming government",
               "Isaac Herzog asked Gadi Eisenkot to form a government after Netanyahu failed.", 3),
             w("a1", "AJ", "Eisenkot chosen over Netanyahu to form Israel’s next government",
               "President Isaac Herzog picked Gadi Eisenkot, sidelining Benjamin Netanyahu.", 2),
             w("b2", "BBC", "Trump to visit Iran border region", "Donald Trump will tour Iraq's Kurdistan.", 2.5),
             w("a2", "AJ", "Iran oil exports fall as Trump tariffs bite", "Tehran's sales to China dropped in October.", 1.5)]
    got = dedupe([dict(i) for i in batch], name_topics=frozenset({"world"}))
    assert [i["uid"] for i in got] == ["b1", "b2", "a2"]
    assert got[0]["also_in"] == ["AJ"] and got[0]["rank"] == 3.5  # 多一家報導，rank 加 0.5
    assert len(dedupe([dict(i) for i in batch])) == 4  # 沒開就照舊


def batch() -> list[dict]:
    return [{"uid": f"u{n}", "title": t, "url": f"https://x.example/{n}", "source": "s", "topic": "t",
             "summary": "", "published": NOW, "weight": 1.0, "baseline_relevance": 2.0 + n / 10}
            for n, t in enumerate(["alpha quantum", "bravo reactor", "charlie harvest", "delta voyage"])]


def test_rerun_same_day():
    """沒有 redo 時第二次拿到第二梯隊；帶上次的 uid 就拿回同一批。"""
    def uids(r: tuple) -> set[str]:
        return {i["uid"] for i in r[0]}

    cfg = {"min_relevance": 0.8, "quota": {"t": 2}}
    first = curate(batch(), cfg)
    assert uids(first) == {"u3", "u2"}
    assert uids(curate(batch(), cfg, redo=uids(first))) == uids(first)
    assert uids(curate(batch(), cfg)) == {"u1", "u0"}  # redo 之後的 seen 仍是對的


def test_feedback_boosts():
    """沒有回饋＝{}；少量幾乎不動；壓倒性也不超過 1±strength；只動 rank 不動 relevance。"""
    assert source_boosts({}) == {}
    mod.FEEDBACK.write_text("".join(json.dumps({"uid": f"g{n}", "mark": "+", "source": "Good"}) + "\n" for n in range(10)) +
                            "".join(json.dumps({"uid": f"b{n}", "mark": "--", "source": "Bad"}) + "\n" for n in range(200)) +
                            json.dumps({"uid": "g0", "mark": "", "source": "Good"}) + "\n")  # 取消的不算
    assert source_boosts({}) == {"Good": 1.095, "Bad": 0.81}  # Good 9 個 +（1 個取消）
    assert source_boosts({"feedback_ranking": {"strength": 0}}) == {}
    assert source_boosts({"feedback_ranking": {"strength": 0.5, "prior": 1}})["Bad"] > 0.5
    b0, b1 = batch()[:2]
    good, plain = sorted(curate([{**b0, "uid": "fg", "source": "Good"}, {**b1, "uid": "fp", "baseline_relevance": 2.0}],
                                {"min_relevance": 0})[0], key=lambda i: i["uid"])
    assert good["feedback_boost"] == 1.095 and good["rank"] > plain["rank"] and good["relevance"] == plain["relevance"]
    assert "feedback_boost" not in plain


def test_per_source_cap():
    """有別的來源時讓位，沒有就補回來。"""
    def src_item(uid, source, rank):
        return {"uid": uid, "source": source, "rank": rank, "topic": "sci"}
    sci = [src_item("d1", "SD", 9), src_item("d2", "SD", 8), src_item("d3", "SD", 7), src_item("n1", "Nature", 1)]
    assert [i["uid"] for i in _pick(sci, 3, 2)] == ["d1", "d2", "n1"]
    assert [i["uid"] for i in _pick(sci[:3], 3, 2)] == ["d1", "d2", "d3"]
    assert [i["uid"] for i in _pick(sci, 3, None)] == ["d1", "d2", "d3"]


def test_near_misses():
    """配額沒滿的主題才附；命中關鍵字的優先；0 分與 arXiv 不附。"""
    def near(uid, topic, rel, rank, **kw):
        return {"uid": uid, "url": uid, "topic": topic, "relevance": rel, "rank": rank,
                "matched_keywords": kw.get("hits", []), "source": kw.get("source", "s")}
    fresh = [near("n1", "tech", 0.5, 0.9), near("n2", "tech", 0.4, 0.5, hits=["ai chip"]), near("n3", "tech", 0.5, 0.4),
             near("n4", "tech", 0.5, 0.3), near("z", "tech", 0.0, 2.0), near("ax", "tech", 0.5, 3.0, source="arXiv cs.LG"),
             near("f1", "full", 0.5, 1.0), near("k", "full", 2.0, 1.0)]
    got = near_misses(fresh, [fresh[-1]], {"min_relevance": 0.8, "quota": {"full": 1}, "default_quota": 5})
    assert [i["uid"] for i in got] == ["n2", "n1", "n3"]


def test_save_seen_interrupted(monkeypatch):
    """舊的 seen.json 要完好、不留暫存檔。"""
    save_seen({"old": NOW})

    def boom(*args, **kwargs):
        raise OSError("disk full")
    with monkeypatch.context() as m, pytest.raises(OSError):
        m.setattr(os, "replace", boom)
        save_seen({"new": NOW})
    assert list(mod.STATE.parent.glob(f"{mod.STATE.name}.*")) == []
    assert load_seen() == {"old": NOW}


@pytest.mark.parametrize("bad", ["", '{"a": "2026-10-', "[]", '{"a": 1}'])
def test_corrupt_seen(bad):
    mod.STATE.write_text(bad, encoding="utf-8")
    assert load_seen() == {}
