import json
import re

from newsletter_shared import ROOT
from newsletter_shared.metrics import METRICS_DIR, TRANSCRIPTS, claude_usage, split_runs, summary_line


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
