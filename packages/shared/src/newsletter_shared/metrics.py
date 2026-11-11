#!/usr/bin/env python3
"""量測層：debug 開啟時把各階段耗時與 Claude token 用量寫進 logs/metrics/<date>.jsonl。

開關：config.json 的 "debug"，或 NEWSLETTER_DEBUG=1（0 強制關閉）。測試執行設 NEWSLETTER_RUN_LABEL=test；紀錄一律保留。

  uv run --project packages/shared newsletter-metrics claude [--since ISO] [--until ISO] [--session FILE]
  uv run --project packages/shared newsletter-metrics summary [天數] [--all]
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

from newsletter_shared import ROOT, tz

METRICS_DIR = ROOT / "logs" / "metrics"
TZ = tz()
RUN_ID_ENV = "NEWSLETTER_RUN_ID"  # worker 每次執行設一個，各步驟的紀錄才分得開
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
    row = {"ts": now.isoformat(timespec="seconds"), "stage": stage,
           "label": os.environ.get("NEWSLETTER_RUN_LABEL", "prod"), **fields}
    if run_id := os.environ.get(RUN_ID_ENV):
        row["run_id"] = run_id
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
        "non_tool_seconds": round(wall - tool_seconds, 1),  # 模型生成＋串流＋排隊；session 紀錄沒有逐回合延遲
        "api_turns": len(by_id),
        "models": models,
        "tokens": tokens,
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1]["seconds"])),
    }


def cmd_claude(argv: list[str]) -> int:
    if os.environ.get("NEWSLETTER_RUNNER") == "sdk":
        print("[metrics] 由 newsletter-write 記錄用量，略過", file=sys.stderr)
        return 0
    args = dict(zip(argv[::2], argv[1::2], strict=False))
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


def split_runs(rows: list[dict]) -> list[dict[str, dict]]:
    """依時間順序切成多次執行。有 run_id（worker 跑的）就依它分；
    舊紀錄或手動跑的沒有，每筆 fetch 開啟新的一次，之後的階段歸到這一次。"""
    runs: list[dict[str, dict]] = []
    by_id: dict[str, dict[str, dict]] = {}
    current: dict[str, dict] | None = None
    for row in rows:
        if row["stage"] == "fetch_source":
            continue  # 單一來源的明細，不進表格
        if rid := row.get("run_id"):
            if rid not in by_id:
                runs.append(by_id.setdefault(rid, {}))
            by_id[rid][row["stage"]] = row
            continue
        if row["stage"] == "fetch" or current is None or row["stage"] in current:
            current = {}
            runs.append(current)
        current[row["stage"]] = row
    return runs


def cmd_summary(argv: list[str]) -> int:
    show_all = "--all" in argv
    days = int(next((a for a in argv if a.isdigit()), 7))
    today = datetime.now(TZ).date()
    stages = ("fetch", "curate", "search", "enrich", "claude", "render", "pipeline")
    header = (f"{'date':10} {'time':5} {'label':5} {'via':4} " + " ".join(f"{s[:6]:>6}" for s in stages) +
              f" {'in+cache':>10} {'output':>8} {'turns':>5} {'usd':>6} {'s+':>3} {'miss':>4} {'nm':>3} {'result':8}  top tools")
    print(header)
    print("-" * len(header))
    for n in range(days - 1, -1, -1):
        day = today - timedelta(days=n)
        path = METRICS_DIR / f"{day}.jsonl"
        if not path.exists():
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not show_all:
            rows = [r for r in rows if r.get("label", "prod") == "prod"]  # 舊紀錄沒有 label，當 prod
        for run in split_runs(rows):
            print(summary_line(day, run, stages))
    return 0


def summary_line(day, run: dict[str, dict], stages: tuple[str, ...]) -> str:
    first = min(run.values(), key=lambda r: r["ts"])
    c, cov, p, sy = run.get("claude", {}), run.get("coverage", {}), run.get("pipeline"), run.get("search_yield", {})
    tok = c.get("tokens", {})
    top = ", ".join(f"{k}×{v['calls']}({v['seconds']:.0f}s)" for k, v in list(c.get("tools", {}).items())[:3])
    via = c.get("runner", "chat") if c else "-"  # sdk＝newsletter-write；chat＝對話裡的 /news-digest（舊紀錄沒有 runner）
    costs = [r["total_cost_usd"] for r in (c, *(run.get(s, {}) for s in ("search", "kg_extract", "kg_relate")))
             if r.get("total_cost_usd") is not None]
    usd = f"{sum(costs):.2f}" if costs else "-"  # writer＋搜尋＋KG；只有 sdk 路線拿得到
    result = "-" if p is None else "ok" if not p["exit_code"] else f"{p['step']}:{p['exit_code']}"
    secs = " ".join(f"{run[s]['seconds']:>6.1f}" if "seconds" in run.get(s, {}) else f"{'-':>6}" for s in stages)
    return (f"{day!s:10} {first['ts'][11:16]:5} {first.get('label', 'prod'):5} {via:4} {secs} "
            f"{tok.get('input', 0) + tok.get('cache_write', 0) + tok.get('cache_read', 0):>10,} "
            f"{tok.get('output', 0):>8,} {c.get('api_turns', '-'):>5} {usd:>6} {sy.get('accepted', '-'):>3} {cov.get('missing', '-'):>4} "
            f"{cov.get('near_misses_used', '-'):>3} {result:8}  {top}")


def main() -> int:
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("summary", [])
    return {"claude": cmd_claude, "summary": cmd_summary}[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main())
