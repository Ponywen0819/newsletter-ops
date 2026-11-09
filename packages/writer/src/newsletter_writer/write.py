#!/usr/bin/env python3
"""寫報告：用 Claude Agent SDK 跑 news-digest skill，依當日 curated JSON 寫 reports/<date>.md，並驗收產出。

用法：uv run --project packages/writer newsletter-write [--max-turns N]
      uv run --project packages/writer newsletter-write --selftest

不上網、沒有 Bash：curated JSON 含有網頁原文（不受信任），agent 只能讀 ROOT 內（state/ 除外）、只能寫 reports/，
格式檢查用程式內的 report_check 工具。報告的內容只能來自 curated JSON（搜尋與原文都由 collect 先存好）。訊息一律走 stderr。
exit code：0 成功／1 agent 失敗／2 環境問題（沒 SDK、沒 token）／3 報告沒產出／4 格式不符／5 授權失敗／6 額度用完
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from newsletter_llm import runner
from newsletter_shared import ROOT, metrics
from newsletter_shared.report_data import parse_report, uids_of

TOOLS = ["Skill", "Read", "Write", "Edit"]
CHECK_TOOL = "mcp__newsletter__report_check"


def scoped(tool: str, rel: str, root: Path) -> list[str]:
    """`./rel/**` 加上解析 symlink 後的絕對路徑：容器裡 data/、reports/、state/ 連到 volume，
    在 cwd 之外，只寫相對規則對不上（cwd 外的讀寫預設拒絕）。"""
    rules = [f"{tool}(./{rel}/**)"]
    real = (root / rel).resolve()
    if real != root.resolve() / rel:
        rules.append(f"{tool}(/{real}/**)")  # 規則裡 // 開頭是絕對路徑
    return rules


def permissions(root: Path) -> tuple[list[str], list[str]]:
    """(allow, deny)。Edit 規則涵蓋 Write；config/、.claude/ 在 cwd 內，讀取預設放行。"""
    allow = ["Skill", CHECK_TOOL, *scoped("Edit", "reports", root), *scoped("Read", "reports", root),
             *scoped("Read", "data/curated", root)]
    deny = [*scoped("Read", "state", root), "Read(./.env)"]  # state/ 有 OAuth token
    return allow, deny
DEFAULT_MAX_TURNS = 30  # 正式機實測 8～11 回合（2026-11）；約 3 倍餘裕，也是失控時的花費上限

PROMPT = """請使用 news-digest skill，產出今天（{stamp}）的晨間簡報。專案根目錄 ROOT 就是目前的工作目錄（{root}）。
這是無人值守執行，沒有人可以回答問題：不確定的地方依 skill 的規則自行判斷，不要停下來問。
當日 curated JSON 已由 newsletter-collect 產好，不要重跑抓取；它不存在或沒有收錄項目時，照 skill 的說明停止、不要寫報告，最後一則回覆說明原因。
完成時最後一則回覆只寫報告路徑與今日頭條。"""


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


def check_format(root: Path, stamp: str) -> str | None:
    """回傳格式不符的原因（shared 的 parse_report 拒絕）；None 表示格式正確。呼叫前 verify() 已確認檔案存在。"""
    try:
        parse_report((root / "reports" / f"{stamp}.md").read_text(encoding="utf-8"))
    except ValueError as exc:
        return f"{stamp}.md 格式不符：{exc}"
    return None


def coverage(root: Path, stamp: str) -> tuple[list[str], list[str], list[str]]:
    """(curated 有但報告漏掉的 uid, 報告有但 curated 沒有的 uid, 報告寫進去的 near_misses uid)。
    漏掉的收不到回饋；只警告，不擋寄信。near_misses 是選用的候選：寫了不算多出來，沒寫也不算漏。"""
    payload = json.loads((root / "data" / "curated" / f"{stamp}.json").read_text(encoding="utf-8"))
    want = [i["uid"] for i in payload["items"]]
    optional = {i["uid"] for i in payload.get("near_misses", [])}
    got = uids_of(parse_report((root / "reports" / f"{stamp}.md").read_text(encoding="utf-8")))
    return ([u for u in want if u not in set(got)], sorted(set(got) - set(want) - optional),
            sorted(set(got) & optional))


def report_check(root: Path, stamp: str) -> str | None:
    """agent 的格式檢查（同 newsletter-report-check）：None 表示通過。"""
    if not (root / "reports" / f"{stamp}.md").exists():
        return f"找不到 reports/{stamp}.md"
    return check_format(root, stamp)


def report_check_server(stamp: str):
    """把 report_check 包成 agent 的工具，取代 Bash。"""
    @runner.sdk.tool("report_check", f"檢查 reports/{stamp}.md 的格式；通過回 ok，否則回原因（照原因修正後再檢查）", {})
    async def tool(args: dict) -> dict:
        reason = report_check(ROOT, stamp)
        return {"content": [{"type": "text", "text": reason or "ok"}], "is_error": reason is not None}
    return runner.sdk.create_sdk_mcp_server("newsletter", tools=[tool])


def judge(usage: runner.Usage, sdk_error: str | None, stamp: str, started: float) -> tuple[int, str, str | None]:
    """回傳 (exit code, metrics 的 status, 失敗原因)。"""
    if (fail := runner.failure(usage, sdk_error)) is not None:
        return fail
    if (reason := verify(ROOT, stamp, started)) is not None:
        return 3, "no_report", reason
    if (reason := check_format(ROOT, stamp)) is not None:
        return 4, "bad_format", reason
    return 0, "ok", None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="依當日 curated JSON 寫晨報")
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if runner.sdk is None:
        print("缺 claude-agent-sdk：請用 `uv run --project packages/writer newsletter-write` 執行（或先 `uv sync`）", file=sys.stderr)
        return 2
    if args.selftest:
        selftest()
        return 0
    auth = runner.pick_auth("writer")
    if auth is None:
        return 2

    stamp = f"{datetime.now(metrics.TZ):%Y-%m-%d}"
    started = time.time()
    allow, deny = permissions(ROOT)
    usage, sdk_error, wall = runner.run(PROMPT.format(stamp=stamp, root=ROOT), tools=TOOLS, allowed_tools=allow, disallowed_tools=deny,
                                        mcp_servers={"newsletter": report_check_server(stamp)},
                                        max_turns=args.max_turns, token=auth.token, tag="writer")
    code, status, reason = judge(usage, sdk_error, stamp, started)
    if usage.result is not None:
        runner.log_usage(usage, wall, status, "claude", "writer")
        if usage.result.result:
            print(f"[writer] 最後回覆：{usage.result.result[:500]}", file=sys.stderr)
    if code:
        print(f"[writer] 失敗（exit {code}）：{reason}", file=sys.stderr)
        return code
    missing, unknown, used = coverage(ROOT, stamp)
    metrics.record("coverage", missing=len(missing), unknown=len(unknown), near_misses_used=len(used))
    if used:
        print(f"[writer] 寫進 {len(used)} 則 near_misses：{', '.join(used)}", file=sys.stderr)
    if missing:
        print(f"[writer] ! 報告漏掉 {len(missing)} 則 curated 項目（收不到回饋）：{', '.join(missing)}", file=sys.stderr)
    if unknown:
        print(f"[writer] ! 報告有 {len(unknown)} 個 curated 裡沒有的 uid：{', '.join(unknown)}", file=sys.stderr)
    return 0


def selftest() -> None:
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
        assert "格式不符" in check_format(root, stamp)
        report.write_text("> **今日頭條：** x\n<!-- subject: s -->\n")
        assert check_format(root, stamp) is None and report_check(root, stamp) is None
        assert "找不到" in report_check(root, "2026-01-02")
        a, b, c = "a" * 16, "b" * 16, "c" * 16
        curated.write_text(json.dumps({"items": [{"uid": a}, {"uid": b}], "near_misses": [{"uid": "d" * 16}]}))
        report.write_text(f"> **今日頭條：** x\n<!-- subject: s -->\n\n## 段\n\n- 一\n<!-- mark:    uid={a} -->\n"
                          f"- 二\n<!-- mark:    uid={c} -->\n- 三\n<!-- mark:    uid={'d' * 16} -->\n")
        assert coverage(root, stamp) == ([b], [c], ["d" * 16]), coverage(root, stamp)
    assert not {"WebSearch", "WebFetch", "Bash"} & set(TOOLS)  # 上網是 collect 的事；原文不受信任，不給 Bash
    with tempfile.TemporaryDirectory() as d:  # 容器的樣子：data、reports、state 是連到 volume 的 symlink
        root, vol = Path(d) / "app", Path(d) / "vol"
        for sub in ("data/curated", "reports", "state"):
            (vol / sub).mkdir(parents=True)
        root.mkdir()
        for sub in ("data", "reports", "state"):
            (root / sub).symlink_to(vol / sub)
        allow, deny = permissions(root)
        real = vol.resolve()
        assert f"Edit(/{real}/reports/**)" in allow and f"Read(/{real}/data/curated/**)" in allow, allow
        assert "Edit(./reports/**)" in allow and f"Read(/{real}/state/**)" in deny and "Read(./state/**)" in deny, deny
        assert not {"Read", "Write", "Edit", "Bash"} & set(allow)  # 不帶範圍的規則會放行 cwd 外
        plain = Path(d) / "plain"
        (plain / "reports").mkdir(parents=True)
        assert permissions(plain)[0] == ["Skill", CHECK_TOOL, "Edit(./reports/**)", "Read(./reports/**)",
                                         "Read(./data/curated/**)"]
    selftest_main()
    print("ok")


def selftest_main() -> None:
    """main() 在 agent 成功之後依序驗收：報告有更新（exit 3）、格式正確（exit 4）；stdout 一律是空的。"""
    import contextlib
    import io
    from unittest import mock

    from newsletter_shared import auth_store

    this = sys.modules[__name__]
    sdk = runner.sdk
    secret = "sk-ant-oat01-" + "Qw7" * 12
    good = "> **今日頭條：** x\n<!-- subject: s -->\n"
    result = sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                               session_id="s", total_cost_usd=0.1, result="ok",
                               usage={"input_tokens": 1, "output_tokens": 1})
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        stamp = f"{datetime.now(metrics.TZ):%Y-%m-%d}"
        (root / "data" / "curated").mkdir(parents=True)
        (root / "reports").mkdir()
        (root / "data" / "curated" / f"{stamp}.json").write_text('{"items": [{"uid": "x"}]}')
        report = root / "reports" / f"{stamp}.md"
        auth_store.save(secret, root / "oauth_token.json")

        def run(written: str | None) -> tuple[int, str, str]:
            def fake_run(prompt, *, tools, max_turns, token, tag, **options):  # 取代真正呼叫 SDK 的那一步
                assert tools == TOOLS and token == secret and set(options) == {"allowed_tools", "disallowed_tools", "mcp_servers"}
                assert (options["allowed_tools"], options["disallowed_tools"]) == permissions(root)
                if written is not None:
                    report.write_text(written, encoding="utf-8")
                usage = runner.Usage()
                usage.feed(result)
                return usage, None, 1.0
            env = {auth_store.TOKEN_FILE_ENV: str(root / "oauth_token.json")}
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(this, "ROOT", root), \
                    mock.patch.object(runner, "run", fake_run), mock.patch.object(metrics, "record"), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([])
            return code, out.getvalue(), err.getvalue()

        code, out, err = run(good)
        assert (code, out) == (0, "") and "漏掉 1 則" in err, (code, out, err)
        code, out, err = run("# 沒有 subject 也沒有今日頭條\n")
        assert (code, out) == (4, "") and "格式不符" in err, (code, out, err)
        report.unlink()
        code, out, err = run(None)  # agent 回報成功，卻沒寫報告
        assert (code, out) == (3, "") and "沒有產出" in err, (code, out, err)

    # 沒有 token：環境問題，不呼叫 SDK
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {auth_store.TOKEN_FILE_ENV: f"{d}/none.json"}, clear=True), \
            mock.patch.object(runner, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該呼叫"))), \
            contextlib.redirect_stderr(io.StringIO()):
        assert main([]) == 2


if __name__ == "__main__":
    raise SystemExit(main())
