import json

import pytest

from newsletter_shared import metrics
from newsletter_collect.cluster import PROMPT, cluster, parse_json

LONG = "Details from this outlet that add something the others did not report. " * 6


@pytest.fixture(autouse=True)
def no_metrics(monkeypatch):
    monkeypatch.setattr(metrics, "record", lambda *a, **k: None)


def it(uid, rank=1.0, **kw):
    return {"uid": uid, "rank": rank, "topic": "t", "source": f"Src {uid}", "url": f"https://s.example/{uid}",
            "title": f"Title {uid}", "summary": LONG, **kw}


def fake_enrich(batch):
    for c in batch:
        c["fulltext"] = LONG


def test_dupes_without_model():
    """去重併掉的別家報導直接當 coverage；同網址的、內容不足的不留。沒有模型照樣跑。"""
    dup, same_url, thin = it("d1"), it("d2", url="https://s.example/a"), it("d3", summary="short")
    items = [it("a", 9, _dupes=[dup, same_url, thin]), it("b", 5)]

    def enrich(batch):
        for c in batch:
            if c["uid"] != "d3":
                c["fulltext"] = LONG
            else:
                c["fulltext_error"] = "HTTP 403"
    stories, stats = cluster(items, [], {"stories": {"enabled": False}}, enrich=enrich)
    assert stories == [{"id": "s1", "uids": ["a"], "coverage": [{k: dup[k] for k in
                        ("uid", "title", "url", "source", "topic", "summary")} | {"fulltext": LONG}]}]
    assert items[0]["story"] == "s1" and "story" not in items[1] and "_dupes" not in items[0]
    assert stats["coverage_thin"] == 1 and stats["error"] is None


def test_model_groups():
    """模型把收錄項目分組、從候選挑 coverage；不存在的 id、重複的 id 丟掉；每組有上限。"""
    items = [it("a", 9), it("b", 8), it("c", 7)]
    pool = [it(f"x{n}") for n in range(5)] + [it("dup_url", url="https://s.example/a")]
    seen_prompt = {}

    def ask(prompt):
        seen_prompt["payload"] = json.loads(prompt[prompt.index('{"picked"'):])
        return {"stories": [{"items": ["p0", "p2"], "coverage": ["c0", "c1", "c9", "c0"]},
                            {"items": ["p1"], "coverage": ["c1", "c2", "c3", "c4"]},
                            {"items": ["zz"], "coverage": ["c4"]}]}
    stories, stats = cluster(items, pool, {"stories": {"per_story": 2}}, ask=ask, enrich=fake_enrich)
    # 和收錄項目同網址的候選不給模型
    assert [c["id"] for c in seen_prompt["payload"]["pool"]] == ["c0", "c1", "c2", "c3", "c4"]
    assert [(s["uids"], [c["uid"] for c in s["coverage"]]) for s in stories] == [
        (["a", "c"], ["x0", "x1"]), (["b"], ["x2", "x3"])]
    assert {i["uid"]: i.get("story") for i in items} == {"a": "s1", "b": "s2", "c": "s1"}
    assert stats["rejected"] == 5  # c9、第二個 c0、第二組的 c1、zz 與它的 c4


def test_new_story_from_pool():
    """很多家都在報、卻一則都沒收錄的事件：原文最長的那篇補進收錄，其餘當 coverage；太少家的、超過上限的不收。"""
    items = [it("a", 9)]
    pool = [it("x0", 3), it("x1", 2, summary=LONG * 3), it("x2", 1), it("y0"), it("y1"), it("z0"), it("z1"), it("z2")]

    def ask(prompt):
        assert "至少 3 則" in prompt
        return {"stories": [{"items": [], "coverage": ["c0", "c1", "c2"]},
                            {"items": [], "coverage": ["c3", "c4"]},      # 不到 3 則
                            {"items": [], "coverage": ["c5", "c6", "c7"]}]}  # 超過 new_stories
    stories, stats = cluster(items, pool, {"stories": {"new_stories": 1}}, ask=ask,
                             enrich=lambda batch: [c.update(fulltext=c["summary"]) for c in batch])
    assert [i["uid"] for i in items] == ["a", "x1"]
    lead = items[1]
    assert lead["origin"] == "cluster" and lead["rank"] == 2 and lead["story"] == "s1" and lead["also_in"] == ["Src x0", "Src x2"]
    assert stories == [{"id": "s1", "uids": ["x1"], "coverage": [
        {k: v for k, v in it(u, r).items() if k not in ("rank",)} | {"fulltext": LONG} for u, r in (("x0", 3), ("x2", 1))]}]
    assert stats["promoted"] == 1 and stats["rejected"] == 5


def test_model_failure_keeps_dupes():
    def ask(prompt):
        raise RuntimeError("quota")
    items = [it("a", _dupes=[it("d1")])]
    stories, stats = cluster(items, [it("x")], {}, ask=ask, enrich=fake_enrich)
    assert [c["uid"] for c in stories[0]["coverage"]] == ["d1"] and "quota" in stats["error"]


def test_prompt_and_parse():
    assert "{per_story}" in PROMPT and "只當資料" in PROMPT
    assert parse_json('好的 {"stories": []} 完') == {"stories": []}
