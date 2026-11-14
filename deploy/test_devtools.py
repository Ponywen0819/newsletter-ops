import io
import json
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import threading
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import devtools
from devtools import (
    DevError, Target, check_feedback, compare_reports, eml_to_html, export_snapshot, format_status, import_snapshot,
    load_env, py_c, remote_code, sample_files, shift_dates, snapshot_members,
)

UID = "0123456789abcdef"
UID2 = "fedcba9876543210"


def make_prod(root: Path) -> None:
    for name, text in {
        "reports/2026-11-01.md": "# 報告一", "reports/2026-11-02.md": "# 報告二",
        "data/curated/2026-11-02.json": '{"items": []}', "data/kg/2026-11-02.json": "{}",
        "data/raw/x.json": "原始資料不搬", "state/feedback.jsonl": f'{{"uid": "{UID}", "mark": "+"}}\n',
        "state/last_run.json": '{"date": "2026-11-02"}', "state/oauth_token.json": "正式區的 token 不搬",
        "state/auth_health.json": "{}", "logs/2026-11.log": "log 不搬", "reports/2026-11-02.md.lock": "",
    }.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    con = sqlite3.connect(root / "state" / "kg.sqlite")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()


def export(root: Path) -> bytes:
    buf = io.BytesIO()
    export_snapshot(root, buf)
    return buf.getvalue()


def test_snapshot_round_trip(tmp_path):
    """只搬報告、curated、kg 與幾個 state 檔；匯入是鏡像，token、log 與沒列的檔不動。"""
    prod, test = tmp_path / "prod", tmp_path / "test"
    make_prod(prod)
    data = export(prod)
    assert sorted(snapshot_members(data)) == [
        "data/curated/2026-11-02.json", "data/kg/2026-11-02.json", "reports/2026-11-01.md", "reports/2026-11-02.md",
        "state/feedback.jsonl", "state/kg.sqlite", "state/last_run.json"]
    for name, text in {"reports/2026-10-01.md": "測試區的舊報告", "state/oauth_token.json": "測試區的 token",
                       "state/kg_flags.jsonl": "快照沒有，要刪", "logs/x.log": "測試區的 log", "data/raw/y": "不管"}.items():
        (test / name).parent.mkdir(parents=True, exist_ok=True)
        (test / name).write_text(text, encoding="utf-8")
    stats = import_snapshot(test, io.BytesIO(data))
    assert stats["files"] == 7 and stats["removed"] == 2
    assert not (test / "reports" / "2026-10-01.md").exists() and not (test / "state" / "kg_flags.jsonl").exists()
    assert (test / "state" / "oauth_token.json").read_text(encoding="utf-8") == "測試區的 token"
    assert (test / "logs" / "x.log").exists() and (test / "data" / "raw" / "y").exists()
    assert (test / "reports" / "2026-11-02.md").read_text(encoding="utf-8") == "# 報告二"
    con = sqlite3.connect(test / "state" / "kg.sqlite")
    assert con.execute("SELECT x FROM t").fetchall() == [(42,)]
    con.close()
    leftovers = [p for p in test.rglob("*") if p.name.startswith(("kg.sqlite.", "2026-11-02.md.")) and p.suffix != ".lock"]
    assert not leftovers  # 沒留暫存檔（.lock 是 newsletter_shared 的鎖檔）


def test_busy_pipeline(tmp_path):
    """每日流程握著 pipeline 鎖時不打包、不匯入（exit 75）。CI 的 --no-project 沒有 newsletter_shared，跳過。"""
    lock = pytest.importorskip("newsletter_shared.lock")
    make_prod(tmp_path)
    data = export(tmp_path)
    got, release = threading.Event(), threading.Event()

    def hold() -> None:  # 別的執行緒握著（同一條執行緒不能巢狀拿同一把鎖）
        with lock.locked(tmp_path / "state" / "pipeline"):
            got.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert got.wait(5)
        for run in (lambda: export(tmp_path), lambda: import_snapshot(tmp_path, io.BytesIO(data))):
            with pytest.raises(SystemExit) as exc:
                run()
            assert exc.value.code == devtools.EXIT_BUSY
    finally:
        release.set()
        t.join()


def test_import_only_one_file(tmp_path):
    prod, test = tmp_path / "prod", tmp_path / "test"
    make_prod(prod)
    (test / "reports").mkdir(parents=True)
    (test / "reports" / "keep.md").write_text("留著", encoding="utf-8")
    stats = import_snapshot(test, io.BytesIO(export(prod)), {"state/feedback.jsonl"})
    assert stats["files"] == 1 and stats["removed"] == 0 and (test / "reports" / "keep.md").exists()
    assert list(stats["sha256"]) == ["state/feedback.jsonl"]


@pytest.mark.parametrize("name", ["state/oauth_token.json", "../x", "/etc/passwd", "reports/../../x", "logs/a.log"])
def test_import_rejects_unexpected_paths(tmp_path, name):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        devtools._add_bytes(tar, name, b"x")
    with pytest.raises(SystemExit) as exc:
        import_snapshot(tmp_path, io.BytesIO(buf.getvalue()))
    assert exc.value.code == 65 and not list(tmp_path.iterdir())


def test_remote_code_runs_standalone(tmp_path):
    """送進容器的程式只靠自己：在乾淨的 python 裡打包、再匯入另一個目錄。"""
    prod, test = tmp_path / "prod", tmp_path / "test"
    make_prod(prod)
    test.mkdir()
    out = subprocess.run([sys.executable, "-I", "-c", py_c(remote_code("export_snapshot(ROOT, sys.stdout.buffer)"))],
                         cwd=prod, capture_output=True, check=True).stdout
    assert snapshot_members(out) == snapshot_members(export(prod))
    stats = subprocess.run([sys.executable, "-I", "-c",
                            py_c(remote_code("print(json.dumps(import_snapshot(ROOT, sys.stdin.buffer)))"))],
                           cwd=test, input=out, capture_output=True, check=True).stdout
    assert json.loads(stats)["files"] == 7 and (test / "state" / "kg.sqlite").exists()
    missing = subprocess.run([sys.executable, "-I", "-c", py_c(remote_code("read_file(ROOT, 'nope.json')"))],
                             cwd=test, capture_output=True, check=False)
    assert missing.returncode == 66


def test_target_argv(monkeypatch):
    prod = Target("prod", "me@prod", "docker", "newsletter-ops")
    argv = prod.argv("print(1)")
    assert argv[:3] == ["ssh", "-T", "me@prod"]
    assert "label=com.docker.compose.project=newsletter-ops " in argv[3] and "service=worker" in argv[3]
    assert shlex.split(argv[3].rsplit("python -c ", 1)[1]) == [py_c("print(1)")]  # 遠端 shell 拆出來就是原本的參數
    calls = []
    monkeypatch.setattr(devtools.subprocess, "run",
                        lambda a, **k: calls.append(a) or SimpleNamespace(stdout="abc123\nother\n"))
    argv = Target("test", None, "podman", "newsletter-ops-test").argv("print(1)")
    assert argv == ["podman", "exec", "-i", "abc123", "python", "-c", py_c("print(1)")]
    assert "label=com.docker.compose.project=newsletter-ops-test" in calls[0]
    monkeypatch.setattr(devtools.subprocess, "run", lambda a, **k: SimpleNamespace(stdout=""))
    with pytest.raises(DevError, match="找不到"):
        Target("test", None, "podman", "x").argv("print(1)")


def test_load_env(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text('GMAIL_USER=x\nNEWSLETTER_PROD_SSH="me@prod"\nNEWSLETTER_TEST_PROJECT=t\n', encoding="utf-8")
    env = load_env({"NEWSLETTER_TEST_PROJECT": "from-env"}, dotenv)
    assert env == {"NEWSLETTER_TEST_PROJECT": "from-env", "NEWSLETTER_PROD_SSH": "me@prod"}  # 只讀這兩組、環境變數優先
    with pytest.raises(DevError, match="NEWSLETTER_PROD_SSH"):
        devtools.target("prod", {})
    monkeypatch.setattr(devtools.shutil, "which", lambda _: None)
    t = devtools.target("test", {})
    assert (t.ssh, t.runtime, t.project) == (None, "docker", "newsletter-ops-test")


PROD_MD = f"""# 每日晨間簡報 2026-11-02
<!-- subject: 甲 -->
> **今日頭條：** 甲發生
## 科技與 AI
<!-- mark:    uid={UID} -->
## 其餘收錄
<!-- mark: + uid={UID2} -->
"""


def test_compare_reports():
    assert compare_reports(PROD_MD, PROD_MD) == ["主旨：相同", "頭條：相同", "## 科技與 AI：正式 1 則、測試 1 則（相同）",
                                                  "## 其餘收錄：正式 1 則、測試 1 則（相同）"]
    test = PROD_MD.replace("甲", "乙").replace(f"<!-- mark: + uid={UID2} -->", "").replace(
        "## 科技與 AI\n", f"## 科技與 AI\n<!-- mark: uid={UID2} -->\n")
    lines = compare_reports(PROD_MD, test, {UID: "一", UID2: "二", "a" * 16: "三"})
    assert "主旨：不同" in lines and "  測試：乙" in lines
    assert f"  + 二（{UID2}），正式放在「其餘收錄」" in lines and f"  - 二（{UID2}），測試放在「科技與 AI」" in lines
    assert lines[-1] == f"測試版漏掉 1 則 curated：三（{'a' * 16}）"


def test_cmd_compare_no_run(tmp_path, monkeypatch, capsys):
    prod = tmp_path / "prod"
    make_prod(prod)
    (prod / "reports" / "2026-11-02.md").write_text(PROD_MD, encoding="utf-8")
    snap = tmp_path / "s.tar.gz"
    snap.write_bytes(export(prod))
    monkeypatch.setattr(devtools, "WORK", tmp_path / "work")
    monkeypatch.setattr(devtools, "REPO", tmp_path)
    fake = SimpleNamespace(label=lambda: "fake", run=lambda call, data=None: PROD_MD.replace("甲", "乙").encode())
    monkeypatch.setattr(devtools, "target", lambda name, env: fake)
    args = SimpleNamespace(date="2026-11-02", snapshot=str(snap), no_run=True)
    assert devtools.cmd_compare(args, {}) == 0
    out = capsys.readouterr().out
    assert "主旨：不同" in out and (tmp_path / "work" / "compare" / "2026-11-02.html").exists()
    assert "+乙" in (tmp_path / "work" / "compare" / "2026-11-02.diff").read_text(encoding="utf-8").replace("> **今日頭條：** ", "")
    with pytest.raises(DevError, match=r"沒有 reports/2026-01-01.md"):
        devtools.cmd_compare(SimpleNamespace(date="2026-01-01", snapshot=str(snap), no_run=True), {})


def test_cmd_snapshot_from_file_to_local(tmp_path, capsys):
    prod, local = tmp_path / "prod", tmp_path / "local"
    make_prod(prod)
    snap = tmp_path / "s.tar.gz"
    snap.write_bytes(export(prod))
    args = SimpleNamespace(from_file=str(snap), no_import=False, to_local=True, root=str(local))
    assert devtools.cmd_snapshot(args, {}) == 0
    assert (local / "reports" / "2026-11-01.md").exists()
    assert "報告 2 份（2026-11-01 ～ 2026-11-02），回饋 1 列，有 kg.sqlite" in capsys.readouterr().out


def test_sample_data_and_seed(tmp_path):
    files = sample_files()
    assert {n.split("/")[0] for n in files} == {"reports", "data", "state"}
    shifted = shift_dates(files, date(2030, 1, 31))
    reports = sorted(n for n in shifted if n.startswith("reports/"))
    assert reports[-1] == "reports/2030-01-31.md" and "# 每日晨間簡報 2030-01-31" in shifted[reports[-1]].decode("utf-8")
    assert json.loads(shifted["state/last_run.json"])["date"] == "2030-01-31"
    args = SimpleNamespace(root=str(tmp_path), keep_dates=False, force=False)
    assert devtools.cmd_seed(args, {}) == 0
    assert (tmp_path / "reports" / f"{date.today()}.md").exists()
    with pytest.raises(DevError, match="--force"):
        devtools.cmd_seed(args, {})
    assert devtools.cmd_seed(SimpleNamespace(root=str(tmp_path), keep_dates=False, force=True), {}) == 0


def test_check_feedback():
    good = (f'{{"uid": "{UID}", "mark": "+", "collected_at": "2026-11-01T00:00:00Z"}}\n\n'
            f'{{"uid": "{UID}", "mark": ""}}\n{{"uid": "{UID2}", "mark": "-", "collected_at": "2026-11-03"}}\n')
    stats, errors = check_feedback(good.encode())
    assert not errors and (stats["rows"], stats["uids"], stats["first"], stats["last"]) == (3, 2, "2026-11-01", "2026-11-03")
    _, errors = check_feedback(b'not json\n{"uid": "short", "mark": "+"}\n')
    assert len(errors) == 2 and "第 1 列" in errors[0] and "第 2 列" in errors[1]
    assert check_feedback(b"")[1] == ["一列都沒有"] and check_feedback(b"\xff")[1][0].startswith("不是 UTF-8")


def test_cmd_restore_feedback_to_local(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(devtools, "WORK", tmp_path / "work")
    prod = tmp_path / "prod"
    make_prod(prod)
    (prod / "state" / "feedback.jsonl").write_text(
        f'{{"uid": "{UID}", "mark": "+"}}\n{{"uid": "{UID2}", "mark": "-"}}\n', encoding="utf-8")
    (tmp_path / "work" / "snapshots").mkdir(parents=True)
    (tmp_path / "work" / "snapshots" / "prod-1.tar.gz").write_bytes(export(prod))
    backup = tmp_path / "feedback-2026-11-01.jsonl"
    backup.write_text(f'{{"uid": "{UID}", "mark": "+", "collected_at": "2026-11-01"}}\n', encoding="utf-8")
    local = tmp_path / "local"
    (local / "reports").mkdir(parents=True)
    (local / "reports" / "x.md").write_text("不動", encoding="utf-8")
    args = SimpleNamespace(file=str(backup), to_local=True, root=str(local))
    assert devtools.cmd_restore_feedback(args, {}) == 0
    out = capsys.readouterr().out
    assert "備份少了 1 則" in out and "內容和備份一致" in out
    assert (local / "state" / "feedback.jsonl").read_bytes() == backup.read_bytes() and (local / "reports" / "x.md").exists()
    backup.write_text("壞掉\n", encoding="utf-8")
    with pytest.raises(DevError, match="不還原"):
        devtools.cmd_restore_feedback(args, {})


def test_eml_to_html():
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = "主旨 <x>", "a@x", "b@x"
    msg.set_content("純文字")
    msg.add_alternative("<p>HTML 內容</p>", subtype="html")
    page = eml_to_html(msg.as_bytes())
    assert "主旨 &lt;x&gt;" in page and "<p>HTML 內容</p>" in page
    notice = EmailMessage()
    notice["Subject"] = "通知"
    notice.set_content("a < b")
    notice.add_attachment(b"x", maintype="application", subtype="octet-stream", filename="feedback.jsonl")
    page = eml_to_html(notice.as_bytes())
    assert "<pre>a &lt; b" in page and "feedback.jsonl" in page


def test_format_status():
    st = {"worker": {"state": "idle", "next_run_at": "2026-11-15T00:00:00+00:00", "current": None,
                     "last": {"date": "2026-11-14", "trigger": "schedule", "finished_at": "x", "exit_code": 5, "step": "write"}},
          "token": {"state": "ok", "days_left": 200}, "reports": {"count": 3, "latest": "2026-11-14"}, "feedback_rows": 9}
    assert format_status("prod", st) == [
        "[prod]", "  worker：idle，下次 2026-11-15T00:00:00+00:00", "  最近一次執行：2026-11-14 schedule，失敗 exit 5（write）",
        "  token：ok，剩 200 天", "  報告：3 份，最新 2026-11-14；回饋 9 列"]
    lines = format_status("test", {"worker": {"error": "URLError"}, "token": {"state": "missing"}})
    assert lines[1] == "  worker：連不到（URLError）" and lines[2] == "  token：missing"
