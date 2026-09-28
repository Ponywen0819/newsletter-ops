#!/usr/bin/env python3
"""量測層：debug 開啟時，把各階段耗時與 Claude token 用量寫進 logs/metrics/<date>.jsonl。

開關：config.json 的 "debug": true，或環境變數 NEWSLETTER_DEBUG=1（0 可強制關閉）。
關閉時 record() / timed() 什麼都不做，對正常執行沒有影響。

  python3 src/metrics.py claude [--since ISO] [--until ISO] [--session FILE]
      從 Claude Code 的 session 逐則紀錄（~/.claude/projects/<專案>/<session>.jsonl）
      統計最近一次 news-digest 開始到現在的 token、API 回合數、工具呼叫次數與耗時。
  python3 src/metrics.py summary [天數]
      彙整最近幾天的紀錄成表格（同一天跑多次時取每個階段的最後一筆）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
METRICS_DIR = ROOT / "logs" / "metrics"
TZ = ZoneInfo("Asia/Taipei")
TRANSCRIPTS = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))


@lru_cache(maxsize=1)
def enabled() -> bool:
    env = os.environ.get("NEWSLETTER_DEBUG")
    if env is not None:
        return env not in ("", "0", "false")
    try:
        return bool(json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8")).get("debug"))
    except (OSError, ValueError):
        return False


def record(stage: str, **fields) -> None:
    if not enabled():
        return
    now = datetime.now(TZ)
    row = {"ts": now.isoformat(timespec="seconds"), "stage": stage, **fields}
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with (METRICS_DIR / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if "seconds" in fields:
        print(f"[metrics] {stage} {fields['seconds']}s", file=sys.stderr)


@contextmanager
def timed(stage: str, **fields):
    """with timed("curate") as m: ...; m["items"] = 27  —— 可在區塊內補欄位。"""
    start = time.perf_counter()
    try:
        yield fields
    finally:
        record(stage, seconds=round(time.perf_counter() - start, 2), **fields)


# ---------- Claude session 統計 ----------

def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_digest_start(row: dict) -> bool:
    """news-digest 的起點：Skill 工具呼叫，或使用者直接打 /news-digest。"""
    msg = row.get("message") or {}
    content = msg.get("content")
    if row.get("type") == "user" and isinstance(content, str):
        return "<command-name>/news-digest</command-name>" in content
    if row.get("type") == "assistant" and isinstance(content, list):
        return any(c.get("type") == "tool_use" and c.get("name") == "Skill"
                   and c.get("input", {}).get("skill") == "news-digest" for c in content)
    return False


def claude_usage(path: Path, since: datetime | None = None, until: datetime | None = None) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r.get("timestamp")]
    if since is None:
        starts = [r for r in rows if _is_digest_start(r)]
        # 找不到 news-digest 起點（例如排程 session 由 prompt 直接執行）就算整個 session
        since = _ts(starts[-1]["timestamp"]) if starts else _ts(rows[0]["timestamp"])
    rows = [r for r in rows if since <= _ts(r["timestamp"]) and (until is None or _ts(r["timestamp"]) <= until)]

    # 同一則回應會拆成多行（thinking / text / tool_use 各一行），usage 相同，依 message id 去重
    by_id: dict[str, dict] = {}
    tool_start: dict[str, tuple[str, datetime]] = {}
    tools: dict[str, dict] = {}
    for r in rows:
        msg = r.get("message") or {}
        if r.get("type") == "assistant":
            by_id[msg.get("id") or r.get("uuid")] = msg
            for c in msg.get("content") or []:
                if c.get("type") == "tool_use":
                    tool_start[c["id"]] = (c["name"], _ts(r["timestamp"]))
        elif r.get("type") == "user" and isinstance(msg.get("content"), list):
            for c in msg["content"]:
                if c.get("type") == "tool_result" and c.get("tool_use_id") in tool_start:
                    name, t0 = tool_start.pop(c["tool_use_id"])
                    t = tools.setdefault(name, {"calls": 0, "seconds": 0.0})
                    t["calls"] += 1
                    t["seconds"] = round(t["seconds"] + (_ts(r["timestamp"]) - t0).total_seconds(), 1)

    tokens = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    models: dict[str, int] = {}
    for msg in by_id.values():
        u = msg.get("usage") or {}
        tokens["input"] += u.get("input_tokens", 0)
        tokens["cache_write"] += u.get("cache_creation_input_tokens", 0)
        tokens["cache_read"] += u.get("cache_read_input_tokens", 0)
        tokens["output"] += u.get("output_tokens", 0)
        models[msg.get("model", "?")] = models.get(msg.get("model", "?"), 0) + 1

    wall = (_ts(rows[-1]["timestamp"]) - since).total_seconds() if rows else 0.0
    tool_seconds = sum(t["seconds"] for t in tools.values())
    return {
        "session": path.stem,
        "since": since.isoformat(timespec="seconds"),
        "seconds": round(wall, 1),
        "tool_seconds": round(tool_seconds, 1),
        "model_seconds": round(wall - tool_seconds, 1),  # ponytail: 牆鐘時間扣掉工具時間，含串流與排隊，非純推論
        "api_turns": len(by_id),
        "models": models,
        "tokens": tokens,
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1]["seconds"])),
    }


def cmd_claude(argv: list[str]) -> int:
    args = dict(zip(argv[::2], argv[1::2]))
    path = Path(args["--session"]) if "--session" in args else None
    if path is None:
        candidates = sorted(TRANSCRIPTS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            print(f"找不到 session 紀錄：{TRANSCRIPTS}", file=sys.stderr)
            return 1
        path = candidates[-1]  # 正在跑的 session 一定是最新寫入的那個
    since = _ts(args["--since"]) if "--since" in args else None
    until = _ts(args["--until"]) if "--until" in args else None
    usage = claude_usage(path, since, until)
    record("claude", **usage)
    print(json.dumps(usage, ensure_ascii=False, indent=2))
    return 0


def cmd_summary(argv: list[str]) -> int:
    days = int(argv[0]) if argv else 7
    today = datetime.now(TZ).date()
    header = f"{'date':10} {'fetch':>7} {'curate':>7} {'claude':>7} {'render':>7} {'in+cache':>10} {'output':>8} {'turns':>5}  top tools"
    print(header)
    print("-" * len(header))
    for n in range(days - 1, -1, -1):
        day = today - timedelta(days=n)
        path = METRICS_DIR / f"{day}.jsonl"
        if not path.exists():
            continue
        last: dict[str, dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            last[row["stage"]] = row
        c = last.get("claude", {})
        tok = c.get("tokens", {})
        top = ", ".join(f"{k}×{v['calls']}({v['seconds']:.0f}s)" for k, v in list(c.get("tools", {}).items())[:3])
        sec = lambda s: f"{last[s]['seconds']:.1f}" if s in last else "-"  # noqa: E731
        print(f"{day!s:10} {sec('fetch'):>7} {sec('curate'):>7} {sec('claude'):>7} {sec('render'):>7} "
              f"{tok.get('input', 0) + tok.get('cache_write', 0) + tok.get('cache_read', 0):>10,} "
              f"{tok.get('output', 0):>8,} {c.get('api_turns', '-'):>5}  {top}")
    return 0


def selftest() -> None:
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
    tmp = METRICS_DIR.parent / ".selftest.jsonl"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    try:
        u = claude_usage(tmp)
    finally:
        tmp.unlink()
    assert u["api_turns"] == 2, u                      # m0 在起點之前不算；m2 兩行只算一次
    assert u["tokens"] == {"input": 6, "cache_write": 0, "cache_read": 100, "output": 10}, u
    assert u["tools"] == {"WebFetch": {"calls": 1, "seconds": 8.0}}, u
    assert u["seconds"] == 10.0 and u["model_seconds"] == 2.0, u
    print("ok")


if __name__ == "__main__":
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("summary", [])
    if cmd == "--selftest":
        selftest()
        raise SystemExit(0)
    raise SystemExit({"claude": cmd_claude, "summary": cmd_summary}[cmd](rest))
