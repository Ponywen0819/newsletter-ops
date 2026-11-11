import json
import os
import time
from datetime import datetime
from unittest import mock

import pytest

from newsletter_llm import runner
from newsletter_shared import auth_store, metrics
from newsletter_writer import kg, write
from newsletter_writer.write import CHECK_TOOL, TOOLS, check_format, coverage, main, permissions, report_check, verify

GOOD = "> **今日頭條：** x\n<!-- subject: s -->\n"
SECRET = "sk-ant-oat01-" + "Qw7" * 12


def test_verify_and_coverage(tmp_path):
    root, stamp, now = tmp_path, "2026-01-01", time.time()
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
    report.write_text(GOOD)
    assert check_format(root, stamp) is None and report_check(root, stamp) is None
    assert "找不到" in report_check(root, "2026-01-02")
    a, b, c = "a" * 16, "b" * 16, "c" * 16
    curated.write_text(json.dumps({"items": [{"uid": a}, {"uid": b}], "near_misses": [{"uid": "d" * 16}]}))
    report.write_text(f"{GOOD}\n## 段\n\n- 一\n<!-- mark:    uid={a} -->\n"
                      f"- 二\n<!-- mark:    uid={c} -->\n- 三\n<!-- mark:    uid={'d' * 16} -->\n")
    assert coverage(root, stamp) == ([b], [c], ["d" * 16])


def test_no_network_tools():
    assert not {"WebSearch", "WebFetch", "Bash"} & set(TOOLS)  # 上網是 collect 的事；原文不受信任，不給 Bash


def test_permissions_follow_symlinks(tmp_path):
    """容器的樣子：data、reports、state 是連到 volume 的 symlink。"""
    root, vol = tmp_path / "app", tmp_path / "vol"
    for sub in ("data/curated", "reports", "state"):
        (vol / sub).mkdir(parents=True)
    root.mkdir()
    for sub in ("data", "reports", "state"):
        (root / sub).symlink_to(vol / sub)
    allow, deny = permissions(root)
    real = vol.resolve()
    assert f"Edit(/{real}/reports/**)" in allow and f"Read(/{real}/data/curated/**)" in allow
    assert "Edit(./reports/**)" in allow and f"Read(/{real}/state/**)" in deny and "Read(./state/**)" in deny
    assert not {"Read", "Write", "Edit", "Bash"} & set(allow)  # 不帶範圍的規則會放行 cwd 外


def test_permissions_plain(tmp_path):
    (tmp_path / "reports").mkdir()
    assert permissions(tmp_path)[0] == ["Skill", CHECK_TOOL, "Edit(./reports/**)", "Read(./reports/**)",
                                        "Read(./data/curated/**)", "Read(./data/kg/**)"]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    stamp = f"{datetime.now(metrics.TZ):%Y-%m-%d}"
    (tmp_path / "data" / "curated").mkdir(parents=True)
    (tmp_path / "reports").mkdir()
    (tmp_path / "data" / "curated" / f"{stamp}.json").write_text('{"items": [{"uid": "x"}]}')
    auth_store.save(SECRET, tmp_path / "oauth_token.json")
    for k in list(os.environ):
        monkeypatch.delenv(k)
    monkeypatch.setenv(auth_store.TOKEN_FILE_ENV, str(tmp_path / "oauth_token.json"))
    monkeypatch.setattr(write, "ROOT", tmp_path)
    monkeypatch.setattr(metrics, "record", lambda *a, **k: None)
    prepare = mock.Mock()
    monkeypatch.setattr(kg, "prepare", prepare)
    return tmp_path, stamp, prepare


def run_main(setup, monkeypatch, capsys, written: str | None):
    """main() 在 agent 成功之後依序驗收：報告有更新（exit 3）、格式正確（exit 4）；stdout 一律是空的。"""
    root, stamp, prepare = setup
    result = runner.sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                      session_id="s", total_cost_usd=0.1, result="ok",
                                      usage={"input_tokens": 1, "output_tokens": 1})

    def fake_run(prompt, *, tools, max_turns, token, tag, **options):
        assert tools == TOOLS and token == SECRET and set(options) == {"allowed_tools", "disallowed_tools", "mcp_servers"}
        assert (options["allowed_tools"], options["disallowed_tools"]) == permissions(root)
        if written is not None:
            (root / "reports" / f"{stamp}.md").write_text(written, encoding="utf-8")
        usage = runner.Usage()
        usage.feed(result)
        return usage, None, 1.0
    monkeypatch.setattr(runner, "run", fake_run)
    code = main([])
    prepare.assert_called_once_with(stamp, SECRET)  # 寫報告前先產生 KG 背景
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_main_ok(setup, monkeypatch, capsys):
    code, out, err = run_main(setup, monkeypatch, capsys, GOOD)
    assert (code, out) == (0, "") and "漏掉 1 則" in err


def test_main_bad_format(setup, monkeypatch, capsys):
    code, out, err = run_main(setup, monkeypatch, capsys, "# 沒有 subject 也沒有今日頭條\n")
    assert (code, out) == (4, "") and "格式不符" in err


def test_main_no_report(setup, monkeypatch, capsys):
    """agent 回報成功，卻沒寫報告。"""
    code, out, err = run_main(setup, monkeypatch, capsys, None)
    assert (code, out) == (3, "") and "沒有產出" in err


def test_main_no_token(tmp_path, monkeypatch):
    """沒有 token：環境問題，不呼叫 SDK。"""
    for k in list(os.environ):
        monkeypatch.delenv(k)
    monkeypatch.setenv(auth_store.TOKEN_FILE_ENV, str(tmp_path / "none.json"))

    def boom(*a, **k):
        raise AssertionError("不該呼叫")
    monkeypatch.setattr(runner, "run", boom)
    assert main([]) == 2
