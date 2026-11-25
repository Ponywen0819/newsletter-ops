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
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from newsletter_shared import ROOT, tz

METRICS_DIR = ROOT / "logs" / "metrics"
TZ = tz()
RUN_ID_ENV = "NEWSLETTER_RUN_ID"  # worker 每次執行設一個，各步驟的紀錄才分得開
STAGES = ("fetch", "curate", "search", "enrich", "claude", "render", "pipeline")
COST_STAGES = ("claude", "search", "kg_extract", "kg_relate")  # 有 total_cost_usd 的階段（只有 SDK 路線）
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


def read_day_rows(root: Path, day: date | str) -> list[dict]:
    """讀取 root/logs/metrics/<day>.jsonl；容忍不存在與寫到一半的殘缺 JSON 行。"""
    try:
        lines = (root / "logs" / "metrics" / f"{day}.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 寫到一半的最後一行
        if isinstance(row, dict) and isinstance(row.get("stage"), str) and isinstance(row.get("ts"), str):
            rows.append(row)
    return rows


def summarize_run(day: date | str, run: dict[str, dict]) -> dict:
    """單次執行的結構化彙總（CLI summary 與 /api/ops/metrics 共用）。"""
    first = min(run.values(), key=lambda r: r["ts"])
    claude, pipeline = run.get("claude", {}), run.get("pipeline")
    tok = claude.get("tokens", {})
    costs = [r["total_cost_usd"] for r in (run.get(s, {}) for s in COST_STAGES) if r.get("total_cost_usd") is not None]
    return {
        "date": str(day),
        "time": first["ts"][11:16],
        "label": first.get("label", "prod"),
        "trigger": (pipeline or {}).get("trigger"),
        "result": None if pipeline is None else {"exit_code": pipeline.get("exit_code"), "step": pipeline.get("step")},
        "seconds": {s: run[s]["seconds"] for s in STAGES if isinstance(run.get(s, {}).get("seconds"), (int, float))},
        "tokens": {"input": tok.get("input", 0) + tok.get("cache_write", 0) + tok.get("cache_read", 0),
                   "output": tok.get("output", 0)} if tok else None,
        "turns": claude.get("api_turns"),
        "usd": round(sum(costs), 4) if costs else None,
        "items": run.get("curate", {}).get("items"),
    }


def cmd_summary(argv: list[str]) -> int:
    show_all = "--all" in argv
    days = int(next((a for a in argv if a.isdigit()), 7))
    today = datetime.now(TZ).date()
    header = (f"{'date':10} {'time':5} {'label':5} {'via':4} " + " ".join(f"{s[:6]:>6}" for s in STAGES) +
              f" {'in+cache':>10} {'output':>8} {'turns':>5} {'usd':>6} {'s+':>3} {'miss':>4} {'nm':>3} {'result':8}  top tools")
    print(header)
    print("-" * len(header))
    for n in range(days - 1, -1, -1):
        day = today - timedelta(days=n)
        rows = read_day_rows(ROOT, day)
        if not show_all:
            rows = [r for r in rows if r.get("label", "prod") == "prod"]  # 舊紀錄沒有 label，當 prod
        for run in split_runs(rows):
            if run:
                print(summary_line(day, run, STAGES))
    return 0


def summary_line(day: date | str, run: dict[str, dict], stages: tuple[str, ...] = STAGES) -> str:
    s = summarize_run(day, run)
    c, cov, sy = run.get("claude", {}), run.get("coverage", {}), run.get("search_yield", {})
    tok = s["tokens"] or {"input": 0, "output": 0}
    top = ", ".join(f"{k}×{v['calls']}({v['seconds']:.0f}s)" for k, v in list(c.get("tools", {}).items())[:3])
    via = c.get("runner", "chat") if c else "-"  # sdk＝newsletter-write；chat＝對話裡的 /news-digest（舊紀錄沒有 runner）
    usd = f"{s['usd']:.2f}" if s["usd"] is not None else "-"  # writer＋搜尋＋KG；只有 sdk 路線拿得到
    res = s["result"]
    result = "-" if res is None else "ok" if not res["exit_code"] else f"{res['step']}:{res['exit_code']}"
    secs = " ".join(f"{s['seconds'][st]:>6.1f}" if st in s["seconds"] else f"{'-':>6}" for st in stages)
    turns = "-" if s["turns"] is None else s["turns"]
    return (f"{s['date']:10} {s['time']:5} {s['label']:5} {via:4} {secs} "
            f"{tok['input']:>10,} {tok['output']:>8,} {turns:>5} {usd:>6} {sy.get('accepted', '-'):>3} {cov.get('missing', '-'):>4} "
            f"{cov.get('near_misses_used', '-'):>3} {result:8}  {top}")


def main() -> int:
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("summary", [])
    return {"claude": cmd_claude, "summary": cmd_summary}[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main())
