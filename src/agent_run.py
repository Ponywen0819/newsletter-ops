#!/usr/bin/env python3
"""無人值守入口：用 Claude Agent SDK 跑同一份 news-digest skill，不必開 Claude app。

用法：uv run src/agent_run.py [--max-turns N]
      uv run src/agent_run.py --selftest

抓取、寫報告、render_email.py 都由 agent 依 SKILL.md 完成；本程式負責啟動、記錄用量、驗收產出。
stdout 只印 render_email.py 那行 JSON（subject / headline / html_path），可直接接 send_email.py，
其餘訊息一律走 stderr（cron 會收進 logs）。

exit code：0 成功
           1 agent 失敗（回報錯誤、超過 max_turns、SDK 例外）
           2 環境問題（沒裝 claude-agent-sdk）
           3 報告沒產出（沒更新 reports/<date>.md，或 curated 沒有收錄項目）
           4 render_email.py 失敗

認證：SDK 底層是 claude CLI；有 ANTHROPIC_API_KEY 就按 token 計費，沒設會退回本機的登入身分。
用量寫進 logs/metrics/<date>.jsonl（stage: claude, runner: sdk），遵守 debug 開關
（NEWSLETTER_DEBUG=1 或 config.json 的 debug），label 沿用 NEWSLETTER_RUN_LABEL。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import traceback
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402

try:
    import claude_agent_sdk as sdk
except ImportError:
    sdk = None

ALLOWED_TOOLS = ["Skill", "Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"]
DEFAULT_MAX_TURNS = 60  # 還沒量過實際回合數；跑幾天看 metrics 的 turns 再調

PROMPT = """請使用 news-digest skill，產出今天（{stamp}）的晨間簡報。專案根目錄 ROOT 就是目前的工作目錄（{root}）。
這是無人值守執行，沒有人可以回答問題：不確定的地方依 skill 的規則自行判斷，不要停下來問。
無法完成時（例如抓取全失敗）照 skill 的說明停止、不要寫報告，最後一則回覆說明原因。
完成時最後一則回覆只寫報告路徑與今日頭條。"""


class Usage:
    """逐則累積訊息串流裡的工具耗時與模型，結束時併入 ResultMessage 的 token 與費用。"""

    def __init__(self) -> None:
        self.result = None
        self.tools: dict[str, dict] = {}
        self.models: dict[str, int] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self._seen: set[str] = set()

    def feed(self, msg, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if isinstance(msg, sdk.ResultMessage):
            self.result = msg
            return
        if not isinstance(msg, (sdk.AssistantMessage, sdk.UserMessage)) or not isinstance(msg.content, list):
            return
        if isinstance(msg, sdk.AssistantMessage):
            key = msg.message_id or msg.uuid  # 同一則回應會拆成多個 message，依 id 去重
            if key is None or key not in self._seen:
                self._seen.add(key)
                self.models[msg.model] = self.models.get(msg.model, 0) + 1
        for block in msg.content:
            if isinstance(block, sdk.ToolUseBlock):
                self._pending[block.id] = (block.name, now)
            elif isinstance(block, sdk.ToolResultBlock) and block.tool_use_id in self._pending:
                name, t0 = self._pending.pop(block.tool_use_id)
                t = self.tools.setdefault(name, {"calls": 0, "seconds": 0.0})
                t["calls"] += 1
                t["seconds"] = round(t["seconds"] + now - t0, 1)

    def record(self, wall: float) -> dict:
        """欄位與 metrics.claude_usage 對齊，summary 才能同一張表列出兩條路線。"""
        r = self.result
        u = r.usage or {}
        tool_seconds = round(sum(t["seconds"] for t in self.tools.values()), 1)
        return {
            "runner": "sdk",
            "session": r.session_id,
            "seconds": round(wall, 1),
            "tool_seconds": tool_seconds,
            "non_tool_seconds": round(wall - tool_seconds, 1),
            "api_turns": r.num_turns,
            "models": self.models,
            "tokens": {"input": u.get("input_tokens", 0),
                       "cache_write": u.get("cache_creation_input_tokens", 0),
                       "cache_read": u.get("cache_read_input_tokens", 0),
                       "output": u.get("output_tokens", 0)},
            "tools": dict(sorted(self.tools.items(), key=lambda kv: -kv[1]["seconds"])),
            "total_cost_usd": None if r.total_cost_usd is None else round(r.total_cost_usd, 4),
            "stop": r.subtype,
        }


def brief(block) -> str:
    for key in ("command", "url", "query", "skill", "file_path"):
        if key in block.input:
            return f"{block.name}: {str(block.input[key]).splitlines()[0][:100]}"
    return block.name


async def run_agent(prompt: str, max_turns: int, usage: Usage) -> None:
    options = sdk.ClaudeAgentOptions(
        cwd=ROOT,
        setting_sources=["project"],  # 只載入專案的 .claude/skills，不吃使用者層級的同名 skill
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="dontAsk",    # 沒預先允許的工具直接拒絕，不要卡在沒人回答的提示
        max_turns=max_turns,
        env={"NEWSLETTER_RUNNER": "sdk"},  # metrics.py claude 看到這個就略過，用量由本程式記錄
    )
    async for msg in sdk.query(prompt=prompt, options=options):
        usage.feed(msg)
        if isinstance(msg, sdk.AssistantMessage):
            if msg.error:
                print(f"[agent] API 錯誤：{msg.error}", file=sys.stderr)
            for block in msg.content if isinstance(msg.content, list) else []:
                if isinstance(block, sdk.ToolUseBlock):
                    print(f"[agent] {brief(block)}", file=sys.stderr)


def verify(root: Path, stamp: str, started: float) -> str | None:
    """回傳失敗原因；None 表示這次執行確實產出了報告。"""
    curated = root / "data" / "curated" / f"{stamp}.json"
    try:
        if not json.loads(curated.read_text(encoding="utf-8")).get("items"):
            return f"{curated.name} 沒有收錄項目（抓取全失敗時不該有報告）"
    except (OSError, ValueError):
        return f"找不到可讀的 {curated}"
    report = root / "reports" / f"{stamp}.md"
    if not report.exists():
        return f"沒有產出 {report}"
    if report.stat().st_mtime < started:
        return f"{report.name} 是舊檔，這次執行沒有更新它"
    return None


def render_email(root: Path, stamp: str) -> tuple[int, str, str]:
    # agent 照 skill 已經跑過一次並記了 render；這裡只驗收，關掉 metrics 避免多出一筆 render 把一次執行切成兩列
    env = {**os.environ, "NEWSLETTER_DEBUG": "0"}
    p = subprocess.run([sys.executable, str(root / "src" / "render_email.py"), stamp],
                       cwd=root, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if sdk is None:
        print("缺 claude-agent-sdk：請用 `uv run src/agent_run.py` 執行（或先 `uv sync`）", file=sys.stderr)
        return 2
    if args.selftest:
        selftest()
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[agent] 沒設 ANTHROPIC_API_KEY，會改用本機 claude 的登入身分計費（排程建議用 API key）",
              file=sys.stderr)

    stamp = f"{datetime.now(metrics.TZ):%Y-%m-%d}"
    started, t0, usage = time.time(), time.monotonic(), Usage()
    sdk_error = None
    try:
        asyncio.run(run_agent(PROMPT.format(stamp=stamp, root=ROOT), args.max_turns, usage))
    except Exception as exc:  # noqa: BLE001
        # 結果是錯誤時（例如超過 max_turns）SDK 會在吐出 ResultMessage 之後丟一般 Exception，
        # 所以不能只接 ClaudeSDKError；先把已收到的用量記下來再判定失敗。
        sdk_error = f"{type(exc).__name__}: {exc}"
        if usage.result is None:  # 連結果都沒有＝非預期的失敗，留下 traceback 方便查
            traceback.print_exc()
    wall = time.monotonic() - t0

    r, code, status, reason, meta = usage.result, 0, "ok", None, ""
    if r is None:
        code, status, reason = 1, "agent_failed", f"沒有收到結果（{sdk_error or '串流意外中止'}）"
    elif r.is_error or r.subtype != "success":
        code, status, reason = 1, "agent_failed", f"{r.subtype}：{'; '.join(r.errors or []) or r.result or sdk_error or ''}"
    elif (reason := verify(ROOT, stamp, started)) is not None:
        code, status = 3, "no_report"
    else:
        rc, meta, err = render_email(ROOT, stamp)
        if rc != 0:
            code, status, reason = 4, "render_failed", err or f"render_email.py exit {rc}"

    if r is not None:
        rec = usage.record(wall)
        metrics.record("claude", **rec, status=status)
        tk = rec["tokens"]
        cost = "-" if r.total_cost_usd is None else f"${r.total_cost_usd:.2f}"
        print(f"[agent] {status} turns={r.num_turns} cost={cost} {rec['seconds']}s "
              f"tokens in={tk['input'] + tk['cache_write'] + tk['cache_read']:,} out={tk['output']:,}",
              file=sys.stderr)
        if r.result:
            print(f"[agent] 最後回覆：{r.result[:500]}", file=sys.stderr)
    if code:
        print(f"[agent] 失敗（exit {code}）：{reason}", file=sys.stderr)
    else:
        print(meta)
    return code


def selftest() -> None:
    u = Usage()
    u.feed(sdk.AssistantMessage(content=[sdk.ToolUseBlock("t1", "WebFetch", {"url": "x"})], model="m", message_id="a1"), now=10)
    u.feed(sdk.UserMessage(content=[sdk.ToolResultBlock("t1", "ok")]), now=18)
    u.feed(sdk.AssistantMessage(content=[sdk.TextBlock("done")], model="m", message_id="a1"), now=19)  # 同 id 只算一次
    u.feed(sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
                             session_id="s", total_cost_usd=0.5, result="ok",
                             usage={"input_tokens": 5, "cache_creation_input_tokens": 7,
                                    "cache_read_input_tokens": 100, "output_tokens": 9}))
    rec = u.record(30.0)
    assert rec["tokens"] == {"input": 5, "cache_write": 7, "cache_read": 100, "output": 9}, rec
    assert rec["tools"] == {"WebFetch": {"calls": 1, "seconds": 8.0}}, rec
    assert rec["models"] == {"m": 1} and rec["api_turns"] == 3 and rec["total_cost_usd"] == 0.5, rec
    assert rec["tool_seconds"] == 8.0 and rec["non_tool_seconds"] == 22.0 and rec["runner"] == "sdk", rec

    with tempfile.TemporaryDirectory() as d:
        root, stamp, now = Path(d), "2026-01-01", time.time()
        (root / "data" / "curated").mkdir(parents=True)
        (root / "reports").mkdir()
        curated, report = root / "data" / "curated" / f"{stamp}.json", root / "reports" / f"{stamp}.md"
        assert "找不到" in verify(root, stamp, now)
        curated.write_text('{"items": []}')
        assert "沒有收錄" in verify(root, stamp, now)
        curated.write_text('{"items": [{"uid": "x"}]}')
        assert "沒有產出" in verify(root, stamp, now)
        report.write_text("# 報告")
        os.utime(report, (now - 100, now - 100))
        assert "舊檔" in verify(root, stamp, now)
        os.utime(report, (now + 1, now + 1))
        assert verify(root, stamp, now) is None
    print("ok")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
