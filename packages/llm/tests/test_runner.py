import asyncio
import json
import os

import pytest

from newsletter_llm import runner
from newsletter_llm.runner import EXIT_AUTH, EXIT_QUOTA, Usage, agent_env, failure, main, pick_auth, probe, run
from newsletter_shared import auth_store

sdk = runner.sdk
SECRET = "sk-ant-oat01-" + "Qw7" * 12
OK = sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                       session_id="s", result="ok")


def init(src):
    return sdk.SystemMessage(subtype="init", data={"apiKeySource": src})


def failed(error, text="", http=None) -> Usage:
    u = Usage()
    u.feed(sdk.AssistantMessage(content=[sdk.TextBlock(text)], model="<synthetic>", error=error))
    u.feed(sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0, is_error=True, num_turns=1,
                             session_id="s", api_error_status=http))
    return u


def test_usage():
    u = Usage()
    u.feed(sdk.AssistantMessage(content=[sdk.ToolUseBlock("t1", "WebSearch", {"query": "x"})], model="m", message_id="a1"), now=10)
    u.feed(sdk.UserMessage(content=[sdk.ToolResultBlock("t1", "ok")]), now=18)
    u.feed(sdk.AssistantMessage(content=[sdk.TextBlock("done")], model="m", message_id="a1"), now=19)
    u.feed(sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
                             session_id="s", total_cost_usd=0.5, result="ok",
                             usage={"input_tokens": 5, "cache_creation_input_tokens": 7,
                                    "cache_read_input_tokens": 100, "output_tokens": 9}))
    rec = u.record(30.0)
    assert rec["tokens"] == {"input": 5, "cache_write": 7, "cache_read": 100, "output": 9}
    assert rec["tools"] == {"WebSearch": {"calls": 1, "seconds": 8.0}}
    assert u.calls == [{"tool": "WebSearch", "input": {"query": "x"}}]
    assert rec["models"] == {"m": 1} and rec["api_turns"] == 3 and rec["total_cost_usd"] == 0.5
    assert rec["tool_seconds"] == 8.0 and rec["non_tool_seconds"] == 22.0 and rec["runner"] == "sdk"
    assert failure(u, None) is None


# run()


def test_run_only_given_tools_and_keeps_usage_after_error(monkeypatch):
    """只開指定的工具，其他看不到；SDK 在結果之後丟的例外要回報但保留用量。"""
    async def q(prompt, options):
        assert options.tools == ["WebSearch"] and options.allowed_tools == ["WebSearch"]
        assert options.setting_sources == [] and options.env[auth_store.TOKEN_ENV] == "tok"
        yield OK
        raise RuntimeError("after result")
    monkeypatch.setattr(sdk, "query", q)
    usage, error, _ = run("p", tools=["WebSearch"], max_turns=3, token="tok", tag="t")
    assert usage.result is OK and error == "RuntimeError: after result"


def test_run_skills_load_project_settings(monkeypatch):
    async def q(prompt, options):
        assert options.setting_sources == ["project"]
        yield OK
    monkeypatch.setattr(sdk, "query", q)
    assert run("p", tools=["Skill", "Read"], max_turns=3, token="tok", tag="t")[0].result is OK


def test_run_aborts_on_api_key(monkeypatch):
    """執行中 init 說用了 API key：馬上中止、不再讀後面的訊息，判成需要人處理的失敗。"""
    async def q(prompt, options):
        yield init("ANTHROPIC_API_KEY")
        raise AssertionError("不該讀到這裡")
    monkeypatch.setattr(sdk, "query", q)
    usage, error, _ = run("p", tools=["Read"], max_turns=3, token="tok", tag="t")
    assert error.startswith("ApiKeyInUse") and failure(usage, error)[:2] == (runner.EXIT_AUTH, "api_key")
    u = Usage()
    u.feed(init("none"))
    u.feed(OK)
    assert failure(u, None) is None


# 失敗分類與認證


def test_failure_kinds():
    """授權失敗與額度用完要和一般失敗分開（對應 exit 5／6）。"""
    u = failed("authentication_failed", "Failed to authenticate. API Error: 401 OAuth access token is invalid.", 401)
    assert u.failure_kind() == "auth" and "401" in u.error_text
    assert failure(u, None)[:2] == (EXIT_AUTH, "auth_failed")
    assert failed(None, http=401).failure_kind() == "auth"
    assert failed("rate_limit").failure_kind() == "quota" and failed("billing_error").failure_kind() == "quota"
    assert failure(failed(None, http=429), None)[:2] == (EXIT_QUOTA, "quota_exhausted")
    assert failed(None, http=403).failure_kind() == "other"  # 代理或沙箱擋網路也是 403，不能叫人重貼 token
    assert failed("server_error", http=529).failure_kind() == "other" and failed(None).failure_kind() == "other"
    assert failure(Usage(), "boom")[:2] == (1, "agent_failed")


def test_agent_env():
    env = agent_env("tok")
    # 不要加 CLAUDE_CODE_SUBPROCESS_ENV_SCRUB：它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗
    assert env == {auth_store.TOKEN_ENV: "tok", "NEWSLETTER_RUNNER": "sdk"}
    assert not set(env) & set(auth_store.SHADOWING_ENV)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in list(os.environ):
        monkeypatch.delenv(k)
    monkeypatch.setenv(auth_store.TOKEN_FILE_ENV, str(tmp_path / "oauth_token.json"))


def run_main(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_api_key_alone_is_no_auth(env, monkeypatch, capsys):
    """只有 API key 時不算有認證：不使用 key、不退回本機登入，也不動環境。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-must-not-be-used")
    assert pick_auth("t") is None
    err = capsys.readouterr().err
    assert "OAuth token" in err and "must-not-be-used" not in err
    assert os.environ["ANTHROPIC_API_KEY"]
    code, out, _ = run_main(["--auth-check"], capsys)
    assert code == 2 and json.loads(out) == {"ok": False, "kind": "env", "message": "沒有可驗證的 OAuth token",
                                              "source": None}


def test_env_token_is_ignored(env, monkeypatch, capsys):
    """環境變數裡的 OAuth token 不算來源：沒有儲存的檔就是沒有認證，並提示它已不再讀取。"""
    monkeypatch.setenv(auth_store.TOKEN_ENV, SECRET)
    assert pick_auth("t") is None
    err = capsys.readouterr().err
    assert "已不再讀取" in err and SECRET not in err
    code, out, _ = run_main(["--auth-check"], capsys)
    assert code == 2 and json.loads(out)["kind"] == "env"


def test_stored_token_scrubs_env(env, monkeypatch):
    """有儲存的 token：排在它前面的來源被移除，其他變數保留；環境裡的 token 不影響選擇。"""
    monkeypatch.setenv(auth_store.TOKEN_ENV, SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    monkeypatch.setenv("KEEP_ME", "1")
    stored = "stored-" + "z" * 30
    auth_store.save(stored, auth_store.token_path())
    assert pick_auth("t") == auth_store.Auth("stored-token", stored)
    assert "ANTHROPIC_API_KEY" not in os.environ and "ANTHROPIC_AUTH_TOKEN" not in os.environ
    assert os.environ["KEEP_ME"] == "1"


def fake_probe(monkeypatch, result):
    async def probe_(token):
        return result
    monkeypatch.setattr(runner, "probe", probe_)


@pytest.mark.parametrize(("result", "want"), [
    ({"ok": True, "kind": "ok", "message": "驗證通過"}, 0),
    ({"ok": False, "kind": "auth", "message": "401"}, EXIT_AUTH),
    ({"ok": False, "kind": "quota", "message": "429"}, EXIT_QUOTA),
    ({"ok": False, "kind": "other", "message": "boom"}, 1),
])
def test_auth_check_from_env(env, monkeypatch, capsys, result, want):
    """--auth-check：輸出與 exit code，token 不能出現在 stdout／stderr。"""
    monkeypatch.setenv(auth_store.TOKEN_ENV, SECRET)
    fake_probe(monkeypatch, result)
    code, out, err = run_main(["--auth-check", "--token-from-env"], capsys)
    assert code == want and json.loads(out) == {**result, "source": "env-token"}
    assert SECRET not in out + err


def test_auth_check_stored(env, monkeypatch, capsys):
    """不帶 --token-from-env：驗證實際會用的來源（儲存的 token）。"""
    auth_store.save("stored-" + "z" * 30, auth_store.token_path())
    fake_probe(monkeypatch, {"ok": True, "kind": "ok", "message": ""})
    _, out, _ = run_main(["--auth-check"], capsys)
    assert json.loads(out)["source"] == "stored-token"


# probe()

BAD = sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0, is_error=True, num_turns=1,
                        session_id="s", api_error_status=401)
AUTH_MSG = sdk.AssistantMessage(content=[sdk.TextBlock(f"Failed to authenticate: {SECRET}")],
                                model="<synthetic>", error="authentication_failed")


def stream(monkeypatch, *msgs, raises=None):
    async def q(prompt, options):
        assert options.tools == [] and options.max_turns == 1 and options.setting_sources == []
        assert options.env == {auth_store.TOKEN_ENV: SECRET}
        for m in msgs:
            yield m
        if raises:
            raise raises
    monkeypatch.setattr(sdk, "query", q)
    return asyncio.run(probe(SECRET))


def test_probe_ok(monkeypatch):
    assert stream(monkeypatch, OK) == {"ok": True, "kind": "ok", "message": "驗證通過", "api_key_source": None}


def test_probe_masks_token_and_keeps_kind(monkeypatch):
    """失敗訊息要遮掉 token、錯誤結果後 SDK 丟的例外不能蓋掉分類。"""
    res = stream(monkeypatch, AUTH_MSG, BAD, raises=RuntimeError("ResultError"))
    assert res["ok"] is False and res["kind"] == "auth" and SECRET not in res["message"] and "***" in res["message"]


def test_probe_spawn_failed(monkeypatch):
    res = stream(monkeypatch, raises=RuntimeError("spawn failed"))
    assert res == {"ok": False, "kind": "other", "message": "RuntimeError: spawn failed", "api_key_source": None}


def test_probe_api_key_source(monkeypatch):
    """init 說沒用 API key（none）才算通過；用了 API key 就算呼叫成功也是失敗。"""
    assert stream(monkeypatch, init("none"), OK) == {"ok": True, "kind": "ok", "message": "驗證通過", "api_key_source": "none"}
    res = stream(monkeypatch, init("ANTHROPIC_API_KEY"), OK)
    assert res["ok"] is False and res["kind"] == "api_key" and res["api_key_source"] == "ANTHROPIC_API_KEY"
    res = stream(monkeypatch, init("none"), AUTH_MSG, BAD, raises=RuntimeError("ResultError"))
    assert res["kind"] == "auth" and res["api_key_source"] == "none"
