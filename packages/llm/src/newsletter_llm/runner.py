#!/usr/bin/env python3
"""用 OAuth（訂閱額度）跑 Claude Agent SDK 的共用執行器：認證、執行、失敗分類、用量紀錄。collect 的 search 與 writer 共用。

用法：uv run --project packages/llm newsletter-llm --auth-check [--token-from-env]
      uv run --project packages/llm newsletter-llm --selftest

--auth-check 的結果是 stdout 的一行 JSON，其他訊息走 stderr。
exit code：0 成功／1 失敗／2 環境問題（沒 SDK、沒 token）／5 授權失敗／6 額度用完

只用 OAuth，不支援 API key：環境裡優先序高於 OAuth 的來源會先被移除，否則 CLI 會悄悄用 key 計費。
--auth-check 的 api_key_source 不是 none 就算失敗，因為呼叫成功本身分不出走的是哪種認證。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback

from newsletter_shared import ROOT, auth_store, metrics

try:
    import claude_agent_sdk as sdk
except ImportError:
    sdk = None

EXIT_AUTH, EXIT_QUOTA = 5, 6  # 授權失敗／額度用完：和一般的失敗（1）分開，讓排程知道該做什麼
CHECK_PROMPT = "Reply with the single word: ok"


class ApiKeyInUse(RuntimeError):
    pass


def uses_api_key(usage: "Usage") -> bool:
    return usage.api_key_source not in (None, "none")  # None＝沒收到 init，沒有證據就不亂判


class Usage:
    """逐則累積訊息串流裡的工具耗時與模型，結束時併入 ResultMessage 的 token 與費用。"""

    def __init__(self) -> None:
        self.result = None
        self.tools: dict[str, dict] = {}
        self.calls: list[dict] = []          # 每次工具呼叫的名稱與輸入，給稽核用（例如 search 的 query）
        self.models: dict[str, int] = {}
        self.error: str | None = None       # AssistantMessage.error：authentication_failed / rate_limit ...
        self.error_text: str = ""           # 帶著 error 的那則訊息的文字（CLI 產生的說明）
        self.api_key_source: str | None = None  # init 訊息的 apiKeySource：none＝沒用 API key，否則是來源（如 ANTHROPIC_API_KEY）
        self._pending: dict[str, tuple[str, float]] = {}
        self._seen: set[str] = set()

    def feed(self, msg, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if isinstance(msg, sdk.ResultMessage):
            self.result = msg
            return
        if isinstance(msg, sdk.SystemMessage) and msg.subtype == "init":
            self.api_key_source = msg.data.get("apiKeySource")
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
                self.calls.append({"tool": block.name, "input": block.input})
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
        "NEWSLETTER_RUNNER": "sdk",  # newsletter-metrics claude 看到這個就略過，用量由本程式記錄
    }


def pick_auth(tag: str) -> auth_store.Auth | None:
    """選出這次的認證並整理環境；回傳 None 表示沒有可用的 OAuth token（呼叫端要結束，不退回別的方式）。"""
    auth = auth_store.resolve(auth_store.token_path())
    if auth is None:
        print(f"[{tag}] 沒有可用的 OAuth token。請在自己的電腦跑 `claude setup-token`，把 token 貼到 newsletter-web 的 /auth 頁面"
              "（只能從本機開；Docker 的做法見 deploy/README.md）。本專案不使用 API key，也不退回本機 claude 的登入。",
              file=sys.stderr)
        if os.environ.get(auth_store.TOKEN_ENV):
            print(f"[{tag}] 注意：環境變數 {auth_store.TOKEN_ENV} 已不再讀取（token 要存在 /auth，換新才不用重啟或重新部署）",
                  file=sys.stderr)
        return None
    removed = auth_store.scrub_environ(os.environ)
    if removed:
        print(f"[{tag}] 已移除環境變數 {', '.join(removed)}：本專案只用 OAuth 授權，不使用 API key", file=sys.stderr)
    print(f"[{tag}] 認證來源：{auth.source}", file=sys.stderr)
    st = auth_store.status(auth_store.token_path())
    if st["state"] in ("expiring", "expired"):
        left = "已過期" if st["state"] == "expired" else "剩 %d 天" % st["days_left"]
        print(f"[{tag}] 警告：token 預計 {st['expires_at']} 到期（{left}），請到 /auth 重新貼上新的 token", file=sys.stderr)
    return auth


async def probe(token: str) -> dict:
    """用 token 做一次最小的呼叫：不載入 skill、不開任何工具、只一個回合。回傳 {ok, kind, message, api_key_source}。
    api_key_source 是 CLI 自己回報的（init 訊息）：不是 none 就代表 SDK 實際在用 API key，不管呼叫成不成功都算失敗
    （kind: api_key）——光是「呼叫成功」分不出是 OAuth 還是悄悄走了 API key 計費。"""
    options = sdk.ClaudeAgentOptions(
        cwd=ROOT, setting_sources=[], tools=[], max_turns=1, permission_mode="dontAsk",
        env={auth_store.TOKEN_ENV: token},
    )
    usage, error = Usage(), None
    try:
        async for msg in sdk.query(prompt=CHECK_PROMPT, options=options):
            usage.feed(msg)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    r, src = usage.result, usage.api_key_source
    if uses_api_key(usage):
        return {"ok": False, "kind": "api_key", "api_key_source": src,
                "message": f"SDK 正在使用 API key（來源：{src}），不是 OAuth 訂閱額度"}
    if r is not None and not r.is_error and r.subtype == "success":
        return {"ok": True, "kind": "ok", "message": "驗證通過", "api_key_source": src}
    message = usage.error_text or (r.result if r is not None else None) or error or "沒有收到結果"
    return {"ok": False, "kind": usage.failure_kind(), "message": message.replace(token, "***"), "api_key_source": src}


def auth_check(from_env: bool) -> int:
    """--auth-check：stdout 一行 JSON。from_env＝只看環境變數（驗證尚未儲存的候選 token）。"""
    def emit(result: dict) -> None:
        print(json.dumps(result, ensure_ascii=False))

    if from_env:
        token = os.environ.get(auth_store.TOKEN_ENV, "").strip()
        auth = auth_store.Auth("env-token", token) if token else None
    else:
        auth = auth_store.resolve(auth_store.token_path())
    if auth is None:
        emit({"ok": False, "kind": "env", "message": "沒有可驗證的 OAuth token", "source": None})
        return 2
    scrubbed = auth_store.scrub_environ(os.environ)
    if scrubbed:
        print(f"[llm] 已移除環境變數 {', '.join(scrubbed)}", file=sys.stderr)
    result = asyncio.run(probe(auth.token))
    emit({**result, "source": auth.source})
    return 0 if result["ok"] else {"auth": EXIT_AUTH, "quota": EXIT_QUOTA}.get(result["kind"], 1)


async def _query(prompt: str, options, usage: Usage, tag: str) -> None:
    async for msg in sdk.query(prompt=prompt, options=options):
        usage.feed(msg)
        if uses_api_key(usage):  # 一開始就停，不要讓整次執行用 API key 計費
            raise ApiKeyInUse(f"SDK 正在使用 API key（來源：{usage.api_key_source}）")
        if isinstance(msg, sdk.AssistantMessage):
            if msg.error:
                print(f"[{tag}] API 錯誤：{msg.error}", file=sys.stderr)
            for block in msg.content if isinstance(msg.content, list) else []:
                if isinstance(block, sdk.ToolUseBlock):
                    print(f"[{tag}] {brief(block)}", file=sys.stderr)


def run(prompt: str, *, tools: list[str], max_turns: int, token: str, tag: str) -> tuple[Usage, str | None, float]:
    """跑一次 agent，回傳 (usage, SDK 例外, 耗時秒數)。只開 tools 裡的工具，其餘的 agent 看不到也用不了。"""
    options = sdk.ClaudeAgentOptions(
        cwd=ROOT,
        # 用 Skill 才載入專案設定（.claude/skills），且不吃使用者層級的同名 skill；其他情況什麼設定都不載入
        setting_sources=["project"] if "Skill" in tools else [],
        tools=tools,
        allowed_tools=tools,
        permission_mode="dontAsk",    # 沒預先允許的直接拒絕，不要卡在沒人回答的提示
        max_turns=max_turns,
        env=agent_env(token),
    )
    usage, error, t0 = Usage(), None, time.monotonic()
    try:
        asyncio.run(_query(prompt, options, usage, tag))
    except Exception as exc:
        # 結果是錯誤時（例如超過 max_turns）SDK 會在吐出 ResultMessage 之後丟一般 Exception，
        # 所以不能只接 ClaudeSDKError；已收到的用量照樣留著。
        error = f"{type(exc).__name__}: {exc}"
        if usage.result is None and not isinstance(exc, ApiKeyInUse):  # 連結果都沒有＝非預期的失敗，留下 traceback 方便查
            traceback.print_exc()
    return usage, error, time.monotonic() - t0


def failure(usage: Usage, sdk_error: str | None) -> tuple[int, str, str] | None:
    """agent 本身失敗時回傳 (exit code, metrics 的 status, 原因)；成功回傳 None（產出由呼叫端驗收）。"""
    if uses_api_key(usage):
        return EXIT_AUTH, "api_key", f"SDK 正在使用 API key（來源：{usage.api_key_source}），不是 OAuth 訂閱額度；已中止"
    r = usage.result
    if r is None:
        reason = f"沒有收到結果（{sdk_error or '串流意外中止'}）"
    elif r.is_error or r.subtype != "success":
        reason = f"{r.subtype}：{'; '.join(r.errors or []) or r.result or sdk_error or ''}"
    else:
        return None
    kind = usage.failure_kind()
    if kind == "auth":
        return EXIT_AUTH, "auth_failed", f"授權失敗：{usage.error_text or reason}。請到 newsletter-web 的 /auth 重新貼上 `claude setup-token` 的 token"
    if kind == "quota":
        return EXIT_QUOTA, "quota_exhausted", f"額度用完：{usage.error_text or reason}。不會改用別的認證方式，等訂閱額度恢復再跑"
    return 1, "agent_failed", reason


def log_usage(usage: Usage, wall: float, status: str, stage: str, tag: str) -> None:
    r = usage.result
    rec = usage.record(wall)
    metrics.record(stage, **rec, status=status)
    tk = rec["tokens"]
    cost = "-" if r.total_cost_usd is None else f"${r.total_cost_usd:.2f}"  # 訂閱額度下只是估算，不是扣款
    print(f"[{tag}] {status} turns={r.num_turns} est_usd={cost} {rec['seconds']}s "
          f"tokens in={tk['input'] + tk['cache_write'] + tk['cache_read']:,} out={tk['output']:,}",
          file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Claude Agent SDK 共用執行器")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--auth-check", action="store_true", help="只驗證 OAuth token（最小的一次呼叫）")
    ap.add_argument("--token-from-env", action="store_true",
                    help=f"搭配 --auth-check：只用環境變數 {auth_store.TOKEN_ENV}（驗證尚未儲存的 token）")
    args = ap.parse_args(argv)
    if sdk is None:
        print("缺 claude-agent-sdk：請用 `uv run --project packages/llm newsletter-llm` 執行（或先 `uv sync`）", file=sys.stderr)
        return 2
    if args.selftest:
        selftest()
        return 0
    if args.auth_check:
        return auth_check(args.token_from_env)
    ap.print_usage(sys.stderr)
    return 2


def selftest() -> None:
    u = Usage()
    u.feed(sdk.AssistantMessage(content=[sdk.ToolUseBlock("t1", "WebSearch", {"query": "x"})], model="m", message_id="a1"), now=10)
    u.feed(sdk.UserMessage(content=[sdk.ToolResultBlock("t1", "ok")]), now=18)
    u.feed(sdk.AssistantMessage(content=[sdk.TextBlock("done")], model="m", message_id="a1"), now=19)
    u.feed(sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
                             session_id="s", total_cost_usd=0.5, result="ok",
                             usage={"input_tokens": 5, "cache_creation_input_tokens": 7,
                                    "cache_read_input_tokens": 100, "output_tokens": 9}))
    rec = u.record(30.0)
    assert rec["tokens"] == {"input": 5, "cache_write": 7, "cache_read": 100, "output": 9}, rec
    assert rec["tools"] == {"WebSearch": {"calls": 1, "seconds": 8.0}}, rec
    assert u.calls == [{"tool": "WebSearch", "input": {"query": "x"}}], u.calls
    assert rec["models"] == {"m": 1} and rec["api_turns"] == 3 and rec["total_cost_usd"] == 0.5, rec
    assert rec["tool_seconds"] == 8.0 and rec["non_tool_seconds"] == 22.0 and rec["runner"] == "sdk", rec
    assert failure(u, None) is None
    selftest_run()
    selftest_auth()
    selftest_probe()
    print("ok")


def selftest_run() -> None:
    import contextlib
    import io
    from unittest import mock

    ok_result = sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                  session_id="s", result="ok")
    # run()：只開指定的工具，其他看不到；SDK 在結果之後丟的例外要回報但保留用量
    def tool_stream(*msgs, raises=None):
        async def q(prompt, options):
            assert options.tools == ["WebSearch"] and options.allowed_tools == ["WebSearch"], options
            assert options.setting_sources == [] and options.env[auth_store.TOKEN_ENV] == "tok"
            for m in msgs:
                yield m
            if raises:
                raise raises
        return q
    with mock.patch.object(sdk, "query", tool_stream(ok_result, raises=RuntimeError("after result"))), \
            contextlib.redirect_stderr(io.StringIO()):
        usage, error, _ = run("p", tools=["WebSearch"], max_turns=3, token="tok", tag="t")
    assert usage.result is ok_result and error == "RuntimeError: after result", error

    async def skills(prompt, options):
        assert options.setting_sources == ["project"], options
        yield ok_result
    with mock.patch.object(sdk, "query", skills), contextlib.redirect_stderr(io.StringIO()):
        assert run("p", tools=["Skill", "Read"], max_turns=3, token="tok", tag="t")[0].result is ok_result

    # 執行中 init 說用了 API key：馬上中止、不再讀後面的訊息，判成需要人處理的失敗
    async def keyed(prompt, options):
        yield sdk.SystemMessage(subtype="init", data={"apiKeySource": "ANTHROPIC_API_KEY"})
        raise AssertionError("不該讀到這裡")
    with mock.patch.object(sdk, "query", keyed), contextlib.redirect_stderr(io.StringIO()):
        usage, error, _ = run("p", tools=["Read"], max_turns=3, token="tok", tag="t")
    assert error.startswith("ApiKeyInUse") and failure(usage, error)[:2] == (EXIT_AUTH, "api_key"), error
    u = Usage()
    u.feed(sdk.SystemMessage(subtype="init", data={"apiKeySource": "none"}))
    u.feed(ok_result)
    assert failure(u, None) is None


def selftest_auth() -> None:
    import contextlib
    import io
    import tempfile
    from pathlib import Path
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
    assert failure(u, None)[:2] == (EXIT_AUTH, "auth_failed")
    assert failed(None, http=401).failure_kind() == "auth"
    assert failed("rate_limit").failure_kind() == "quota" and failed("billing_error").failure_kind() == "quota"
    assert failure(failed(None, http=429), None)[:2] == (EXIT_QUOTA, "quota_exhausted")
    assert failed(None, http=403).failure_kind() == "other"  # 代理或沙箱擋網路也是 403，不能叫人重貼 token
    assert failed("server_error", http=529).failure_kind() == "other" and failed(None).failure_kind() == "other"
    assert failure(Usage(), "boom")[:2] == (1, "agent_failed")

    env = agent_env("tok")
    # 不要加 CLAUDE_CODE_SUBPROCESS_ENV_SCRUB：它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗
    assert env == {auth_store.TOKEN_ENV: "tok", "NEWSLETTER_RUNNER": "sdk"}, env
    assert not set(env) & set(auth_store.SHADOWING_ENV)

    secret = "sk-ant-oat01-" + "Qw7" * 12
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
        os.environ[auth_store.TOKEN_FILE_ENV] = str(Path(d) / "oauth_token.json")

        def run_main(argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
            return code, out.getvalue(), err.getvalue()

        def quiet_pick():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                return pick_auth("t"), err.getvalue()

        # 只有 API key 時不算有認證：不使用 key、不退回本機登入，也不動環境
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-must-not-be-used"
        auth, err = quiet_pick()
        assert auth is None and "OAuth token" in err and "must-not-be-used" not in err, err
        assert os.environ["ANTHROPIC_API_KEY"]
        code, out, err = run_main(["--auth-check"])
        assert code == 2 and json.loads(out) == {"ok": False, "kind": "env", "message": "沒有可驗證的 OAuth token",
                                                  "source": None}, out

        # 環境變數裡的 OAuth token 不算來源：沒有儲存的檔就是沒有認證，並提示它已不再讀取
        os.environ.update({auth_store.TOKEN_ENV: secret, "KEEP_ME": "1"})
        auth, err = quiet_pick()
        assert auth is None and "已不再讀取" in err and secret not in err, err
        code, out, err = run_main(["--auth-check"])
        assert code == 2 and json.loads(out)["kind"] == "env", out

        # 有儲存的 token：排在它前面的來源被移除，其他變數保留；環境裡的 token 不影響選擇
        os.environ.update({"ANTHROPIC_AUTH_TOKEN": "t"})
        stored = "stored-" + "z" * 30
        auth_store.save(stored, auth_store.token_path())
        auth, _ = quiet_pick()
        assert auth == auth_store.Auth("stored-token", stored)
        assert "ANTHROPIC_API_KEY" not in os.environ and "ANTHROPIC_AUTH_TOKEN" not in os.environ
        assert os.environ["KEEP_ME"] == "1"

        # --auth-check：假的 probe 驗證輸出與 exit code，token 不能出現在 stdout／stderr
        async def fake(result):
            return result
        this = sys.modules[__name__]
        for result, want in (({"ok": True, "kind": "ok", "message": "驗證通過"}, 0),
                             ({"ok": False, "kind": "auth", "message": "401"}, EXIT_AUTH),
                             ({"ok": False, "kind": "quota", "message": "429"}, EXIT_QUOTA),
                             ({"ok": False, "kind": "other", "message": "boom"}, 1)):
            with mock.patch.object(this, "probe", lambda token, r=result: fake(r)):
                code, out, err = run_main(["--auth-check", "--token-from-env"])
            assert code == want and json.loads(out) == {**result, "source": "env-token"}, (code, out)
            assert secret not in out + err
        with mock.patch.object(this, "probe", lambda token: fake({"ok": True, "kind": "ok", "message": ""})):
            code, out, _ = run_main(["--auth-check"])  # 不帶 --token-from-env：驗證實際會用的來源（儲存的 token）
        assert json.loads(out)["source"] == "stored-token"



def selftest_probe() -> None:
    from unittest import mock

    secret = "sk-ant-oat01-" + "Qw7" * 12

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
        assert asyncio.run(probe(secret)) == {"ok": True, "kind": "ok", "message": "驗證通過", "api_key_source": None}
    with mock.patch.object(sdk, "query", stream(auth_msg, bad, raises=RuntimeError("ResultError"))):
        res = asyncio.run(probe(secret))
    assert res["ok"] is False and res["kind"] == "auth" and secret not in res["message"] and "***" in res["message"], res
    with mock.patch.object(sdk, "query", stream(raises=RuntimeError("spawn failed"))):
        res = asyncio.run(probe(secret))
    assert res == {"ok": False, "kind": "other", "message": "RuntimeError: spawn failed", "api_key_source": None}, res

    # apiKeySource：init 說沒用 API key（none）才算通過；用了 API key 就算呼叫成功也是失敗
    def init(src):
        return sdk.SystemMessage(subtype="init", data={"apiKeySource": src})
    with mock.patch.object(sdk, "query", stream(init("none"), ok)):
        assert asyncio.run(probe(secret)) == {"ok": True, "kind": "ok", "message": "驗證通過", "api_key_source": "none"}
    with mock.patch.object(sdk, "query", stream(init("ANTHROPIC_API_KEY"), ok)):
        res = asyncio.run(probe(secret))
    assert res["ok"] is False and res["kind"] == "api_key" and res["api_key_source"] == "ANTHROPIC_API_KEY", res
    with mock.patch.object(sdk, "query", stream(init("none"), auth_msg, bad, raises=RuntimeError("ResultError"))):
        res = asyncio.run(probe(secret))  # 認證失敗但沒用 API key：照原本分類，並帶出 apiKeySource
    assert res["kind"] == "auth" and res["api_key_source"] == "none", res


if __name__ == "__main__":
    raise SystemExit(main())
