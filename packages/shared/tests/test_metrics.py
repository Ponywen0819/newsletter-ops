from datetime import date
import json
import re

from newsletter_shared import PIPELINE_STEPS, ROOT
from newsletter_shared.metrics import (
    COST_STAGES,
    METRICS_DIR,
    STAGES,
    TRANSCRIPTS,
    claude_usage,
    read_day_rows,
    split_runs,
    summarize_run,
    summary_line,
)


def test_paths_from_root():
    """紀錄在 ROOT/logs/metrics，Claude session 紀錄的位置由 cwd（＝ROOT）決定，debug 旗標讀 ROOT/config/config.json。"""
    assert METRICS_DIR == ROOT / "logs" / "metrics"
    assert TRANSCRIPTS.name == re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    assert (ROOT / "config" / "config.json").is_file()


def test_claude_usage(tmp_path):
    rows = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "hi"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "message": {"id": "m0", "model": "x",
         "usage": {"input_tokens": 999}, "content": []}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:10Z", "message": {"id": "m1", "model": "x",
         "usage": {"input_tokens": 5, "cache_read_input_tokens": 100, "output_tokens": 7},
         "content": [{"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": "news-digest"}}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:11Z", "message": {"id": "m2", "model": "x",
         "usage": {"input_tokens": 1, "output_tokens": 3}, "content": [{"type": "thinking"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:12Z", "message": {"id": "m2", "model": "x",
         "usage": {"input_tokens": 1, "output_tokens": 3},
         "content": [{"type": "tool_use", "id": "t1", "name": "WebFetch", "input": {}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:20Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    u = claude_usage(path)
    assert u["api_turns"] == 2
    assert u["tokens"] == {"input": 6, "cache_write": 0, "cache_read": 100, "output": 10}
    assert u["tools"] == {"WebFetch": {"calls": 1, "seconds": 8.0}}
    assert u["seconds"] == 10.0 and u["non_tool_seconds"] == 2.0


def test_split_runs():
    stages = ["fetch_source", "fetch", "curate", "render", "claude", "fetch", "curate", "render", "render"]
    runs = split_runs([{"stage": st, "ts": str(i)} for i, st in enumerate(stages)])
    assert [sorted(r) for r in runs] == [["claude", "curate", "fetch", "render"],
                                         ["curate", "fetch", "render"], ["render"]]


def test_split_runs_by_run_id():
    """第二次從 write 補跑（沒有 fetch）也自成一列，不黏到前一次。"""
    tagged = [("a", "fetch"), ("a", "claude"), ("a", "pipeline"), ("b", "claude"), ("b", "render"), ("b", "pipeline")]
    runs = split_runs([{"stage": st, "ts": str(i), "run_id": r} for i, (r, st) in enumerate(tagged)])
    assert [sorted(r) for r in runs] == [["claude", "fetch", "pipeline"], ["claude", "pipeline", "render"]]


def test_summary_line():
    run = {"fetch": {"ts": "2026-01-01T08:00:00", "seconds": 20.0},
           "claude": {"ts": "x", "seconds": 300.0, "runner": "sdk", "total_cost_usd": 1.5, "api_turns": 9,
                      "tokens": {"input": 1, "cache_read": 2, "output": 3}},
           "search": {"ts": "x", "seconds": 60.0, "total_cost_usd": 0.25},
           "kg_extract": {"ts": "x", "total_cost_usd": 0.01}, "kg_relate": {"ts": "x", "total_cost_usd": 0.01},
           "coverage": {"ts": "x", "missing": 2, "near_misses_used": 1},
           "search_yield": {"ts": "x", "accepted": 0},
           "pipeline": {"ts": "x", "seconds": 400.0, "exit_code": 6, "step": "write"}}
    line = summary_line("2026-01-01", run, ("fetch", "search", "enrich", "pipeline"))
    assert line.split()[4:14] == ["20.0", "60.0", "-", "400.0", "3", "3", "9", "1.77", "0", "2"]
    assert "write:6" in line


def test_constants():
    assert PIPELINE_STEPS == ("collect", "write", "render", "send")
    assert STAGES == ("fetch", "curate", "search", "enrich", "claude", "render", "pipeline")
    assert COST_STAGES == ("claude", "search", "kg_extract", "kg_relate")


def test_read_day_rows(tmp_path):
    """容忍不存在的檔案、空行、寫到一半的殘缺 JSON 行，以及缺 stage/ts 的列。"""
    assert read_day_rows(tmp_path, "2026-01-01") == []
    metrics_dir = tmp_path / "logs" / "metrics"
    metrics_dir.mkdir(parents=True)
    r1 = {"ts": "2026-01-01T08:00:00+08:00", "stage": "fetch", "seconds": 10.0}
    r2 = {"ts": "2026-01-01T08:01:00+08:00", "stage": "curate", "seconds": 2.0, "items": 15}
    (metrics_dir / "2026-01-01.jsonl").write_text("\n".join([
        json.dumps(r1),
        "",
        "123",
        json.dumps({"ts": "2026-01-01T08:00:30+08:00"}),
        json.dumps({"stage": "search"}),
        json.dumps(r2),
        '{"ts": "2026-01-01T08:05',
    ]), encoding="utf-8")
    assert read_day_rows(tmp_path, "2026-01-01") == [r1, r2]
    assert read_day_rows(tmp_path, date(2026, 1, 1)) == [r1, r2]


def test_summarize_run():
    run = {
        "claude": {"ts": "2026-01-01T08:05:00+08:00", "label": "ops", "seconds": 200.0, "api_turns": 12,
                   "total_cost_usd": 0.5,
                   "tokens": {"input": 10, "cache_write": 20, "cache_read": 30, "output": 40}},
        "fetch": {"ts": "2026-01-01T08:00:00+08:00", "label": "test", "seconds": 10.0},
        "curate": {"ts": "2026-01-01T08:01:00+08:00", "seconds": 2.5, "items": 30},
        "search": {"ts": "2026-01-01T08:02:00+08:00", "seconds": 15.0, "total_cost_usd": 0.125},
        "kg_extract": {"ts": "2026-01-01T08:06:00+08:00", "seconds": 5.0, "total_cost_usd": 0.05},
        "kg_relate": {"ts": "2026-01-01T08:06:30+08:00", "total_cost_usd": 0.025},
        "pipeline": {"ts": "2026-01-01T08:07:00+08:00", "seconds": 420.0, "trigger": "schedule",
                     "exit_code": 0, "step": None},
    }
    assert summarize_run(date(2026, 1, 1), run) == {
        "date": "2026-01-01",
        "time": "08:00",
        "label": "test",
        "trigger": "schedule",
        "result": {"exit_code": 0, "step": None},
        "seconds": {"fetch": 10.0, "curate": 2.5, "search": 15.0, "claude": 200.0, "pipeline": 420.0},
        "tokens": {"input": 60, "output": 40},
        "turns": 12,
        "usd": 0.7,
        "items": 30,
    }
    minimal = {"render": {"ts": "2026-01-02T09:15:30+08:00", "seconds": 1.2}}
    assert summarize_run("2026-01-02", minimal) == {
        "date": "2026-01-02",
        "time": "09:15",
        "label": "prod",
        "trigger": None,
        "result": None,
        "seconds": {"render": 1.2},
        "tokens": None,
        "turns": None,
        "usd": None,
        "items": None,
    }
