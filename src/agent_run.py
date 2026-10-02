#!/usr/bin/env python3
"""無人值守入口：用 Claude Agent SDK 跑同一份 news-digest skill，不必開 Claude app。

用法：uv run src/agent_run.py [--max-turns N]
      uv run src/agent_run.py --auth-check [--token-from-env]
      uv run src/agent_run.py --selftest

抓取、寫報告、render_email.py 都由 agent 依 SKILL.md 完成；本程式負責啟動、記錄用量、驗收產出。
stdout 只印 render_email.py 那行 JSON（subject / headline / html_path），可直接接 send_email.py，
其餘訊息一律走 stderr（cron 會收進 logs）。

exit code：0 成功
           1 agent 失敗（回報錯誤、超過 max_turns、SDK 例外）
           2 環境問題（沒裝 claude-agent-sdk、沒有可用的 OAuth token）
           3 報告沒產出（沒更新 reports/<date>.md，或 curated 沒有收錄項目）
           4 render_email.py 失敗
           5 授權失敗（token 無效或已過期）：到 web.py 的 /auth 重新貼上 `claude setup-token` 的 token
           6 額度用完（訂閱的使用額度或帳務問題）：不會自動換成別的認證方式，等額度恢復再跑

認證：只用 OAuth（訂閱額度），**不支援 API key**。token 來源見 auth_store.py：/auth 頁面存的，或環境變數
CLAUDE_CODE_OAUTH_TOKEN；都沒有就以 exit 2 結束，不退回本機登入。環境裡的 ANTHROPIC_API_KEY 等
優先序高於 OAuth 的來源會先被移除，否則 claude CLI 會優先用 key 而悄悄開始計費。
--auth-check：只做一次最小呼叫驗證 token，stdout 印一行 JSON（ok / kind / message / source）；
web.py 用它驗證貼上的 token。--token-from-env 只用環境變數 CLAUDE_CODE_OAUTH_TOKEN（驗證尚未儲存的候選 token）。
用量寫進 logs/metrics/<date>.jsonl（stage: claude, runner: sdk），遵守 debug 開關
（NEWSLETTER_DEBUG=1 或 config.json 的 debug），label 沿用 NEWSLETTER_RUN_LABEL。
訂閱方案下 total_cost_usd 只是 SDK 依牌價估的 API 等價費用，不是實際扣款。
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

import auth_store  # noqa: E402
import metrics  # noqa: E402

try:
    import claude_agent_sdk as sdk
except ImportError:
    sdk = None

ALLOWED_TOOLS = ["Skill", "Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"]
DEFAULT_MAX_TURNS = 60  # 還沒量過實際回合數；跑幾天看 metrics 的 turns 再調
EXIT_AUTH, EXIT_QUOTA = 5, 6  # 授權失敗／額度用完：和一般的 agent 失敗（1）分開，讓排程知道該做什麼
CHECK_PROMPT = "Reply with the single word: ok"

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
        self.error: str | None = None       # AssistantMessage.error：authentication_failed / rate_limit ...
        self.error_text: str = ""           # 帶著 error 的那則訊息的文字（CLI 產生的說明）
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
            if msg.error and self.error is None:
                self.error = msg.error
                self.error_text = " ".join(b.text for b in msg.content if isinstance(b, sdk.TextBlock))[:300]
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

    def failure_kind(self) -> str:
        """失敗的種類：'auth'（該重新貼 token）、'quota'（額度用完）、'other'。
        只看 CLI 回報的錯誤類型與 HTTP 401／429；403 不算授權失敗（沙箱或代理擋網路也會是 403）。"""
        status = self.result.api_error_status if self.result is not None else None
        if self.error == "authentication_failed" or status == 401:
            return "auth"
        if self.error in ("rate_limit", "billing_error") or status == 429:
            return "quota"
        return "other"

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


def agent_env(token: str) -> dict[str, str]:
    """傳給 claude CLI 的環境（疊在行程環境上；要移除的變數得先 scrub_environ，這裡只能新增／覆寫）。"""
    return {
        auth_store.TOKEN_ENV: token,
        "NEWSLETTER_RUNNER": "sdk",  # metrics.py claude 看到這個就略過，用量由本程式記錄
    }


def pick_auth() -> auth_store.Auth | None:
    """選出這次的認證並整理環境；回傳 None 表示沒有可用的 OAuth token（呼叫端要結束，不退回別的方式）。"""
    auth = auth_store.resolve(os.environ, auth_store.token_path())
    if auth is None:
        print(f"[agent] 沒有可用的 OAuth token。請在自己的電腦跑 `claude setup-token`，把 token 貼到 web.py 的 /auth 頁面"
              f"（只能從本機開），或設環境變數 {auth_store.TOKEN_ENV}。本專案不使用 API key，也不退回本機 claude 的登入。",
              file=sys.stderr)
        return None
    removed = auth_store.scrub_environ(os.environ)
    if removed:
        print(f"[agent] 已移除環境變數 {', '.join(removed)}：本專案只用 OAuth 授權，不使用 API key", file=sys.stderr)
    print(f"[agent] 認證來源：{auth.source}", file=sys.stderr)
    if auth.source == "stored-token":
        st = auth_store.status(auth_store.token_path())
        if st["state"] in ("expiring", "expired"):
            left = "已過期" if st["state"] == "expired" else "剩 %d 天" % st["days_left"]
            print(f"[agent] 警告：token 預計 {st['expires_at']} 到期（{left}），請到 /auth 重新貼上新的 token",
                  file=sys.stderr)
    return auth


async def probe(token: str) -> dict:
    """用 token 做一次最小的呼叫：不載入 skill、不開任何工具、只一個回合。回傳 {ok, kind, message}。"""
    options = sdk.ClaudeAgentOptions(
        cwd=ROOT, setting_sources=[], tools=[], max_turns=1, permission_mode="dontAsk",
        env={auth_store.TOKEN_ENV: token},
    )
    usage, error = Usage(), None
    try:
        async for msg in sdk.query(prompt=CHECK_PROMPT, options=options):
            usage.feed(msg)
    except Exception as exc:  # noqa: BLE001  錯誤結果會在 ResultMessage 之後丟 ResultError，內容已在 usage 裡
        error = f"{type(exc).__name__}: {exc}"
    r = usage.result
    if r is not None and not r.is_error and r.subtype == "success":
        return {"ok": True, "kind": "ok", "message": "驗證通過"}
    message = usage.error_text or (r.result if r is not None else None) or error or "沒有收到結果"
    return {"ok": False, "kind": usage.failure_kind(), "message": message.replace(token, "***")}


def auth_check(from_env: bool) -> int:
    """--auth-check：stdout 一行 JSON。from_env＝只看環境變數（驗證尚未儲存的候選 token）。"""
    def emit(result: dict) -> None:
        print(json.dumps(result, ensure_ascii=False))

    if from_env:
        token = os.environ.get(auth_store.TOKEN_ENV, "").strip()
        auth = auth_store.Auth("env-token", token) if token else None
    else:
        auth = auth_store.resolve(os.environ, auth_store.token_path())
    if auth is None:
        emit({"ok": False, "kind": "env", "message": "沒有可驗證的 OAuth token", "source": None})
        return 2
    scrubbed = auth_store.scrub_environ(os.environ)
    if scrubbed:
        print(f"[agent] 已移除環境變數 {', '.join(scrubbed)}", file=sys.stderr)
    result = asyncio.run(probe(auth.token))
    emit({**result, "source": auth.source})
    return 0 if result["ok"] else {"auth": EXIT_AUTH, "quota": EXIT_QUOTA}.get(result["kind"], 1)


async def run_agent(prompt: str, max_turns: int, usage: Usage, token: str) -> None:
    options = sdk.ClaudeAgentOptions(
        cwd=ROOT,
        setting_sources=["project"],  # 只載入專案的 .claude/skills，不吃使用者層級的同名 skill
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="dontAsk",    # 沒預先允許的工具直接拒絕，不要卡在沒人回答的提示
        max_turns=max_turns,
        env=agent_env(token),
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
    ap.add_argument("--auth-check", action="store_true", help="只驗證 OAuth token（最小的一次呼叫），不跑晨報")
    ap.add_argument("--token-from-env", action="store_true",
                    help=f"搭配 --auth-check：只用環境變數 {auth_store.TOKEN_ENV}（驗證尚未儲存的 token）")
    args = ap.parse_args(argv)
    if sdk is None:
        print("缺 claude-agent-sdk：請用 `uv run src/agent_run.py` 執行（或先 `uv sync`）", file=sys.stderr)
        return 2
    if args.selftest:
        selftest()
        return 0
    if args.auth_check:
        return auth_check(args.token_from_env)
    auth = pick_auth()
    if auth is None:
        return 2

    stamp = f"{datetime.now(metrics.TZ):%Y-%m-%d}"
    started, t0, usage = time.time(), time.monotonic(), Usage()
    sdk_error = None
    try:
        asyncio.run(run_agent(PROMPT.format(stamp=stamp, root=ROOT), args.max_turns, usage, auth.token))
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
    if code:
        kind = usage.failure_kind()
        if kind == "auth":
            code, status = EXIT_AUTH, "auth_failed"
            reason = f"授權失敗：{usage.error_text or reason}。請到 web.py 的 /auth 重新貼上 `claude setup-token` 的 token"
        elif kind == "quota":
            code, status = EXIT_QUOTA, "quota_exhausted"
            reason = f"額度用完：{usage.error_text or reason}。不會改用別的認證方式，等訂閱額度恢復再跑"
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
        cost = "-" if r.total_cost_usd is None else f"${r.total_cost_usd:.2f}"  # 訂閱額度下只是估算，不是扣款
        print(f"[agent] {status} turns={r.num_turns} est_usd={cost} {rec['seconds']}s "
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

    selftest_auth()
    print("ok")


def selftest_auth() -> None:
    import contextlib
    import io
    from unittest import mock

    def failed(error, text="", http=None) -> Usage:
        u = Usage()
        u.feed(sdk.AssistantMessage(content=[sdk.TextBlock(text)], model="<synthetic>", error=error))
        u.feed(sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0, is_error=True, num_turns=1,
                                 session_id="s", api_error_status=http))
        return u

    # 失敗種類：授權失敗與額度用完要和一般失敗分開（對應 exit 5／6）
    u = failed("authentication_failed", "Failed to authenticate. API Error: 401 OAuth access token is invalid.", 401)
    assert u.failure_kind() == "auth" and "401" in u.error_text
    assert failed(None, http=401).failure_kind() == "auth"
    assert failed("rate_limit").failure_kind() == "quota" and failed("billing_error").failure_kind() == "quota"
    assert failed(None, http=429).failure_kind() == "quota"
    assert failed(None, http=403).failure_kind() == "other"  # 代理或沙箱擋網路也是 403，不能叫人重貼 token
    assert failed("server_error", http=529).failure_kind() == "other" and failed(None).failure_kind() == "other"

    env = agent_env("tok")
    # 不要加 CLAUDE_CODE_SUBPROCESS_ENV_SCRUB：它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗
    assert env == {auth_store.TOKEN_ENV: "tok", "NEWSLETTER_RUNNER": "sdk"}, env
    assert not set(env) & set(auth_store.SHADOWING_ENV)

    secret = "sk-ant-oat01-" + "Qw7" * 12
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
        os.environ[auth_store.TOKEN_FILE_ENV] = str(Path(d) / "oauth_token.json")

        def run(argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
            return code, out.getvalue(), err.getvalue()

        # 只有 API key 時不算有認證：直接以環境問題結束，不使用 key、不退回本機登入
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-must-not-be-used"
        code, out, err = run([])
        assert code == 2 and "OAuth token" in err and "must-not-be-used" not in err + out, (code, err)
        assert os.environ["ANTHROPIC_API_KEY"]  # 沒走到 OAuth 就沒動環境（只是不會呼叫 SDK）
        code, out, err = run(["--auth-check"])
        assert code == 2 and json.loads(out) == {"ok": False, "kind": "env", "message": "沒有可驗證的 OAuth token",
                                                  "source": None}, out

        # 有 token：排在它前面的來源被移除，其他變數保留
        os.environ.update({auth_store.TOKEN_ENV: secret, "ANTHROPIC_AUTH_TOKEN": "t", "KEEP_ME": "1"})
        auth = pick_auth()
        assert auth == auth_store.Auth("env-token", secret)
        assert "ANTHROPIC_API_KEY" not in os.environ and "ANTHROPIC_AUTH_TOKEN" not in os.environ
        assert os.environ["KEEP_ME"] == "1" and os.environ[auth_store.TOKEN_ENV] == secret
        # 儲存的 token 優先於環境變數
        auth_store.save("stored-" + "z" * 30, auth_store.token_path())
        assert pick_auth().source == "stored-token"

        # --auth-check：假的 probe 驗證輸出與 exit code，token 不能出現在 stdout／stderr
        async def fake(result):
            return result
        for result, want in (({"ok": True, "kind": "ok", "message": "驗證通過"}, 0),
                             ({"ok": False, "kind": "auth", "message": "401"}, EXIT_AUTH),
                             ({"ok": False, "kind": "quota", "message": "429"}, EXIT_QUOTA),
                             ({"ok": False, "kind": "other", "message": "boom"}, 1)):
            with mock.patch.object(sys.modules[__name__], "probe", lambda token, r=result: fake(r)):
                code, out, err = run(["--auth-check", "--token-from-env"])
            assert code == want and json.loads(out) == {**result, "source": "env-token"}, (code, out)
            assert secret not in out + err
        with mock.patch.object(sys.modules[__name__], "probe", lambda token: fake({"ok": True, "kind": "ok", "message": ""})):
            code, out, _ = run(["--auth-check"])  # 不帶 --token-from-env：走實際會用的來源（儲存的優先）
        assert json.loads(out)["source"] == "stored-token"

    # probe 本身：成功、失敗訊息要遮掉 token、錯誤結果後 SDK 丟的例外不能蓋掉分類
    def stream(*msgs, raises=None):
        async def q(prompt, options):
            assert options.tools == [] and options.max_turns == 1 and options.setting_sources == []
            assert options.env == {auth_store.TOKEN_ENV: secret}
            for m in msgs:
                yield m
            if raises:
                raise raises
        return q

    ok = sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                           session_id="s", result="ok")
    bad = sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0, is_error=True, num_turns=1,
                            session_id="s", api_error_status=401)
    auth_msg = sdk.AssistantMessage(content=[sdk.TextBlock(f"Failed to authenticate: {secret}")],
                                    model="<synthetic>", error="authentication_failed")
    with mock.patch.object(sdk, "query", stream(ok)):
        assert asyncio.run(probe(secret)) == {"ok": True, "kind": "ok", "message": "驗證通過"}
    with mock.patch.object(sdk, "query", stream(auth_msg, bad, raises=RuntimeError("ResultError"))):
        res = asyncio.run(probe(secret))
    assert res["ok"] is False and res["kind"] == "auth" and secret not in res["message"] and "***" in res["message"], res
    with mock.patch.object(sdk, "query", stream(raises=RuntimeError("spawn failed"))):
        res = asyncio.run(probe(secret))
    assert res == {"ok": False, "kind": "other", "message": "RuntimeError: spawn failed"}, res


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
