#!/usr/bin/env python3
"""開發工具：正式區 → 測試區的資料快照、重寫報告比較、測試區寄出的信、本機範例資料、兩區狀態、回饋備份還原演練。

用法（repo 根目錄）：npm run devtools -- <指令>，或 uv run --project packages/shared python deploy/devtools.py <指令>

  snapshot [--to-local] [--from FILE] [--no-import]   正式區的資料複製到測試區（或本機）；快照留一份在 .devtools/snapshots/
  compare DATE [--no-run]                           測試區用正式區那天的素材重寫報告，和正式區那份比較
  outbox [--name NAME]                              測試區「寄出」的信（logs/outbox/），存成可以開的 HTML
  seed [--root DIR] [--keep-dates] [--force]        本機開發用的範例資料（deploy/sample-data/）
  status                                            兩區的 worker、token、最近一次執行
  restore-feedback FILE [--to-local]                回饋備份信的附件還原到測試區（或本機），順便檢查備份能不能用

設定（環境變數，或 repo 根目錄 .env 裡同名的行）：
  NEWSLETTER_PROD_SSH       正式機的 ssh 目的地（user@host 或 ~/.ssh/config 的別名）；用到正式區的指令才需要
  NEWSLETTER_PROD_RUNTIME   正式機的容器指令，預設 docker
  NEWSLETTER_PROD_PROJECT   正式區的 compose 專案名，預設 newsletter-ops
  NEWSLETTER_TEST_SSH       測試機的 ssh 目的地；沒設就是這台
  NEWSLETTER_TEST_RUNTIME   測試機的容器指令，預設這台有 podman 就用 podman，否則 docker
  NEWSLETTER_TEST_PROJECT   測試區的 compose 專案名，預設 newsletter-ops-test

做法：在 worker 容器裡跑一小段 Python（這個檔裡標「容器內」的函式，經 `python -c` 送進去），所以正式區不用先部署新版。
對正式區只讀：打包時拿 pipeline 鎖（每日流程在跑就不打包），kg.sqlite 用 SQLite 的 backup 複製，不會抄到寫一半的檔。
不搬 oauth_token.json、auth_health.json、last_job.json、logs/：兩區的 token 本來就分開，執行紀錄各記各的。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import difflib
import hashlib
import inspect
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = REPO / ".devtools"  # 快照、比較結果、信件；不進版控（.gitignore 的白名單沒放行）
SAMPLE = REPO / "deploy" / "sample-data"

# 快照的內容（相對資料根目錄）。目錄整個鏡像（測試區多出來的檔會刪掉），檔案逐一取代
SNAP_DIRS = ("reports", "data/curated", "data/kg", "data/search")
SNAP_FILES = ("state/feedback.jsonl", "state/kg_flags.jsonl", "state/kg.sqlite", "state/seen.json", "state/last_run.json")
LOCKED = ("state/feedback.jsonl", "state/kg_flags.jsonl", "state/kg.sqlite")  # 寫入者會拿 newsletter_shared.lock 的檔
FEEDBACK = "state/feedback.jsonl"
EXIT_BUSY = 75  # 和 worker 的 EXIT_BUSY 一樣：pipeline 在跑
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
UID_RE = re.compile(r"[0-9a-f]{16}")
MARK_RE = re.compile(r"<!--\s*mark:\s*[+-]{0,2}\s*uid=([0-9a-f]{16})\s*-->")
JOB_TIMEOUT = 75 * 60  # 秒；重寫報告的上限（worker 那邊是 60 分鐘）


class DevError(Exception):
    """給使用者看的錯誤，印出訊息就結束。"""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# ---- 容器內：這幾個函式會原樣送進 worker 容器執行，只能用標準庫與 newsletter_shared，也只能用上面的常數 ----

def _locked(path, timeout=None):
    """newsletter_shared.lock 的鎖；沒有 newsletter_shared（例如 CI 的 --no-project）就不鎖。"""
    try:
        from newsletter_shared import lock
    except ImportError:
        return contextlib.nullcontext()
    return lock.locked(path, timeout=timeout)


@contextlib.contextmanager
def _pipeline_lock(root):
    """握著 pipeline 鎖；拿不到（每日流程或維運工作在跑）就以 EXIT_BUSY 結束。"""
    try:
        from newsletter_shared import lock
    except ImportError:
        yield
        return
    try:
        with lock.locked(root / "state" / "pipeline", timeout=0):
            yield
    except lock.LockBusy:
        print("pipeline 正在跑（state/pipeline.lock 被占用），等它結束再試", file=sys.stderr)
        raise SystemExit(EXIT_BUSY) from None


def _allowed(name):
    """快照裡只能有 SNAP_DIRS 底下的檔與 SNAP_FILES。"""
    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
        return False
    return name in SNAP_FILES or any(name.startswith(d + "/") for d in SNAP_DIRS)


def _add_bytes(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
    tar.addfile(info, io.BytesIO(data))


def export_snapshot(root, out):
    """把快照（tar.gz）寫到 out。全程握著 pipeline 鎖；會被寫的檔各自再拿它的鎖讀。"""
    import sqlite3
    import tempfile
    with _pipeline_lock(root), tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=out, mode="w|gz") as tar:
        for d in SNAP_DIRS:
            base = root / d
            for p in sorted(base.rglob("*")) if base.is_dir() else []:
                if p.is_file() and not p.name.endswith(".lock"):
                    _add_bytes(tar, p.relative_to(root).as_posix(), p.read_bytes())
        for name in SNAP_FILES:
            p = root / name
            if not p.is_file():
                continue
            with _locked(p) if name in LOCKED else contextlib.nullcontext():
                if p.suffix == ".sqlite":
                    copy = Path(tmp) / p.name
                    src, dst = sqlite3.connect(p), sqlite3.connect(copy)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    data = copy.read_bytes()
                else:
                    data = p.read_bytes()
            _add_bytes(tar, name, data)
    out.flush()


def _read_snapshot(src, only):
    """快照（tar.gz 串流）→ {路徑: 內容}。有不在白名單的路徑就整份拒絕（exit 65）。"""
    files = {}
    with tarfile.open(fileobj=src, mode="r|gz") as tar:
        for m in tar:
            if not m.isfile():
                continue
            if not _allowed(m.name):
                print(f"快照裡有不該出現的路徑：{m.name}，不匯入", file=sys.stderr)
                raise SystemExit(65)
            if only is None or m.name in only:
                files[m.name] = tar.extractfile(m).read()
    if not files:
        print("快照裡沒有要匯入的檔", file=sys.stderr)
        raise SystemExit(65)
    return files


def _write_file(root, name, data):
    """寫暫存檔再 os.replace；會被寫的檔先拿它的鎖。"""
    import tempfile
    dest = root / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _locked(dest) if name in LOCKED else contextlib.nullcontext():
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise


def _remove_stale(root, keep):
    """SNAP_DIRS 底下與 SNAP_FILES 裡、快照沒有的檔刪掉（鏡像），回傳刪了幾個。"""
    stale = [p for d in SNAP_DIRS if (root / d).is_dir() for p in (root / d).rglob("*")
             if p.is_file() and not p.name.endswith(".lock")]
    stale += [root / n for n in SNAP_FILES if (root / n).is_file()]
    removed = 0
    for p in stale:
        name = p.relative_to(root).as_posix()
        if name not in keep:
            with _locked(p) if name in LOCKED else contextlib.nullcontext():
                p.unlink()
            removed += 1
    return removed


def import_snapshot(root, src, only=None):
    """讀 src 的快照寫進 root，回傳統計。only 是要匯入的檔名集合（還原單一檔）；沒給就整份鏡像。"""
    files = _read_snapshot(src, only)
    with _pipeline_lock(root):
        removed = _remove_stale(root, files) if only is None else 0
        for name, data in sorted(files.items()):
            _write_file(root, name, data)
    return {"files": len(files), "removed": removed,
            "sha256": {n: hashlib.sha256(d).hexdigest() for n, d in files.items() if n == FEEDBACK}}


def read_file(root, name):
    """把資料根目錄下的一個檔原樣寫到 stdout（不存在就 exit 66）。"""
    p = root / name
    if not p.is_file():
        print(f"找不到 {name}", file=sys.stderr)
        raise SystemExit(66)
    sys.stdout.buffer.write(p.read_bytes())
    sys.stdout.buffer.flush()


def list_dir(root, name):
    p = root / name
    rows = [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
            for f in sorted(p.iterdir()) if f.is_file()] if p.is_dir() else []
    print(json.dumps(rows))


def env_status(root):
    """worker 的 /status、token 狀態、最近的報告。"""
    import urllib.request
    out = {}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8790/status", timeout=5) as resp:
            out["worker"] = json.loads(resp.read())
    except Exception as exc:
        out["worker"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from newsletter_shared import auth_store
        out["token"] = auth_store.status(auth_store.token_path(root))
    except Exception as exc:
        out["token"] = {"state": f"error: {exc}"}
    reports = sorted(p.stem for p in (root / "reports").glob("*.md")) if (root / "reports").is_dir() else []
    out["reports"] = {"count": len(reports), "latest": reports[-1] if reports else None}
    fb = root / FEEDBACK
    out["feedback_rows"] = sum(1 for line in fb.read_text(encoding="utf-8").splitlines() if line.strip()) if fb.is_file() else 0
    print(json.dumps(out, ensure_ascii=False, default=str))


def start_job(kind, query):
    """對同一個容器裡的 worker 發 POST /job，印出回應。"""
    import urllib.error
    import urllib.parse
    import urllib.request
    url = "http://127.0.0.1:8790/job?" + urllib.parse.urlencode({"kind": kind, **query})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10) as resp:
            print(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"worker 回 {exc.code}：{exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        raise SystemExit(EXIT_BUSY if exc.code == 409 else 1) from None


# ---- 送進容器 ----

REMOTE_FUNCS = (_locked, _pipeline_lock, _allowed, _add_bytes, export_snapshot, _read_snapshot, _write_file,
                _remove_stale, import_snapshot, read_file, list_dir, env_status, start_job)


def remote_code(call: str) -> str:
    """容器內要跑的完整程式：import、常數、上面的函式、最後一行呼叫。ROOT 用容器裡 newsletter_shared 的。"""
    head = ["import contextlib, hashlib, io, json, os, sys, tarfile, time", "from pathlib import Path",
            "try:\n    from newsletter_shared import ROOT\nexcept ImportError:\n    ROOT = Path.cwd()"]
    consts = [f"{k} = {globals()[k]!r}" for k in ("SNAP_DIRS", "SNAP_FILES", "LOCKED", "FEEDBACK", "EXIT_BUSY")]
    return "\n".join(head + consts + [inspect.getsource(f) for f in REMOTE_FUNCS] + [call, ""])


def py_c(code: str) -> str:
    """`python -c` 的參數：base64 包起來，經 ssh 的遠端 shell 也不用跳脫。"""
    return f"import base64;exec(base64.b64decode('{base64.b64encode(code.encode('utf-8')).decode()}'))"


@dataclass
class Target:
    name: str  # prod / test
    ssh: str | None
    runtime: str
    project: str

    def label(self) -> str:
        return f"{self.name}（{self.ssh or '本機'}，{self.runtime}，{self.project}）"

    def argv(self, code: str) -> list[str]:
        filters = (f"--filter label=com.docker.compose.project={self.project} "
                   "--filter label=com.docker.compose.service=worker")
        if self.ssh is None:
            cid = self.local_container(filters)
            return [self.runtime, "exec", "-i", cid, "python", "-c", py_c(code)]
        rt = shlex.quote(self.runtime)
        remote = (f"cid=$({rt} ps -q {filters} | head -n1); "
                  f"[ -n \"$cid\" ] || {{ echo '找不到 {self.project} 的 worker 容器（沒在跑？）' >&2; exit 69; }}; "
                  f"exec {rt} exec -i \"$cid\" python -c {shlex.quote(py_c(code))}")
        return ["ssh", "-T", self.ssh, remote]

    def local_container(self, filters: str) -> str:
        try:
            out = subprocess.run([self.runtime, "ps", "-q", *filters.split()], capture_output=True, text=True,
                                 check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DevError(f"{self.runtime} ps 失敗：{exc}") from None
        if not out:
            raise DevError(f"找不到 {self.project} 的 worker 容器（沒在跑？）", 69)
        return out[0]

    def run(self, call: str, data: bytes | None = None) -> bytes:
        """在 worker 容器裡跑 call，回傳 stdout；stderr 直接給使用者看。"""
        try:
            proc = subprocess.run(self.argv(remote_code(call)), input=data, stdout=subprocess.PIPE,
                                  stdin=None if data is not None else subprocess.DEVNULL, check=False)
        except OSError as exc:
            raise DevError(f"執行失敗（{self.label()}）：{exc}") from None
        if proc.returncode:
            raise DevError(f"{self.label()} 失敗，exit {proc.returncode}", proc.returncode)
        return proc.stdout


def load_env(environ: dict | None = None, dotenv: Path = REPO / ".env") -> dict:
    """環境變數優先；沒有的從 .env 補（只讀 NEWSLETTER_PROD_*、NEWSLETTER_TEST_*）。"""
    env = dict(os.environ if environ is None else environ)
    with contextlib.suppress(OSError):
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.startswith(("NEWSLETTER_PROD_", "NEWSLETTER_TEST_")) and key not in env:
                env[key] = value.strip().strip("'\"")
    return env


def target(name: str, env: dict) -> Target:
    if name == "prod":
        ssh = env.get("NEWSLETTER_PROD_SSH", "").strip()
        if not ssh:
            raise DevError("沒設 NEWSLETTER_PROD_SSH（正式機的 ssh 目的地），見 deploy/README.md「開發工具」", 2)
        return Target("prod", ssh, env.get("NEWSLETTER_PROD_RUNTIME") or "docker",
                      env.get("NEWSLETTER_PROD_PROJECT") or "newsletter-ops")
    ssh = env.get("NEWSLETTER_TEST_SSH", "").strip() or None
    runtime = env.get("NEWSLETTER_TEST_RUNTIME") or ("podman" if ssh is None and shutil.which("podman") else "docker")
    return Target("test", ssh, runtime, env.get("NEWSLETTER_TEST_PROJECT") or "newsletter-ops-test")


# ---- 快照 ----

def snapshot_members(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}


def describe(files: dict[str, bytes]) -> str:
    reports = sorted(DATE_RE.search(n)[0] for n in files if n.startswith("reports/") and DATE_RE.search(n))
    size = sum(len(d) for d in files.values())
    fb = files.get(FEEDBACK, b"")
    parts = [f"{len(files)} 個檔、{size / 1e6:.1f} MB",
             f"報告 {len(reports)} 份" + (f"（{reports[0]} ～ {reports[-1]}）" if reports else ""),
             f"回饋 {sum(1 for x in fb.splitlines() if x.strip())} 列",
             "有 kg.sqlite" if "state/kg.sqlite" in files else "沒有 kg.sqlite"]
    return "，".join(parts)


def latest_snapshot() -> Path:
    snaps = sorted((WORK / "snapshots").glob("*.tar.gz"))
    if not snaps:
        raise DevError("還沒有快照：先跑 devtools snapshot", 2)
    return snaps[-1]


def local_import(root: Path, data: bytes, only: set[str] | None = None) -> dict:
    try:
        return import_snapshot(root, io.BytesIO(data), only)
    except SystemExit as exc:
        raise DevError("匯入失敗", int(exc.code or 1)) from None


def cmd_snapshot(args, env) -> int:
    if args.from_file:
        data = Path(args.from_file).read_bytes()
        print(f"使用快照 {args.from_file}")
    else:
        prod = target("prod", env)
        print(f"從 {prod.label()} 打包…")
        data = prod.run("export_snapshot(ROOT, sys.stdout.buffer)")
        path = WORK / "snapshots" / f"prod-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"快照存在 {path.relative_to(REPO).as_posix()}")
    print("內容：" + describe(snapshot_members(data)))
    if args.no_import:
        return 0
    if args.to_local:
        stats = local_import(Path(args.root).resolve(), data)
        where = f"本機 {Path(args.root).resolve()}"
    else:
        test = target("test", env)
        stats = json.loads(test.run("print(json.dumps(import_snapshot(ROOT, sys.stdin.buffer)))", data))
        where = test.label()
    print(f"已匯入 {where}：{stats['files']} 個檔，刪掉 {stats['removed']} 個快照裡沒有的檔")
    return 0


# ---- 重寫報告比較 ----

def sections(md: str) -> dict[str, list[str]]:
    """{段落標題: [uid, …]}；第一個 ## 之前的算「(開頭)」。"""
    out: dict[str, list[str]] = {}
    current = "(開頭)"
    for line in md.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        for uid in MARK_RE.findall(line):
            out.setdefault(current, []).append(uid)
    return out


def meta(md: str) -> dict[str, str]:
    subject = re.search(r"<!--\s*subject:\s*(.*?)\s*-->", md)
    headline = re.search(r"^>\s*\*\*今日頭條：\*\*\s*(.*)$", md, re.M)
    return {"subject": subject[1] if subject else "", "headline": headline[1].strip() if headline else ""}


def _section_diff(a: list[str], b: list[str], where_p: dict, where_t: dict, name) -> list[str]:
    """同一段在正式（a）與測試（b）收的則：測試多的 +、少的 -，並說另一邊放在哪段。"""
    lines = [f"  + {name(u)}" + (f"，正式放在「{where_p[u]}」" if u in where_p else "，正式沒寫") for u in b if u not in a]
    lines += [f"  - {name(u)}" + (f"，測試放在「{where_t[u]}」" if u in where_t else "，測試沒寫") for u in a if u not in b]
    return lines if lines else ["  （同樣幾則，順序不同）"]


def compare_reports(prod: str, test: str, titles: dict[str, str] | None = None) -> list[str]:
    """兩份報告的結構差異（一行一項）：主旨、頭條、各段收了哪些則、則數。"""
    titles = titles or {}
    name = lambda uid: f"{titles.get(uid, '?')[:40]}（{uid}）"  # noqa: E731
    lines = []
    for key, label in (("subject", "主旨"), ("headline", "頭條")):
        a, b = meta(prod)[key], meta(test)[key]
        lines.append(f"{label}：{'相同' if a == b else '不同'}")
        if a != b:
            lines += [f"  正式：{a}", f"  測試：{b}"]
    sp, st = sections(prod), sections(test)
    where_p = {u: s for s, us in sp.items() for u in us}
    where_t = {u: s for s, us in st.items() for u in us}
    for sec in list(dict.fromkeys([*sp, *st])):
        a, b = sp.get(sec, []), st.get(sec, [])
        lines.append(f"## {sec}：正式 {len(a)} 則、測試 {len(b)} 則" + ("" if a != b else "（相同）"))
        if a != b:
            lines += _section_diff(a, b, where_p, where_t, name)
    missing = sorted(set(titles) - set(where_t))
    if titles and missing:
        lines.append(f"測試版漏掉 {len(missing)} 則 curated：" + "、".join(name(u) for u in missing))
    return lines


def wait_job(test: Target, job_id: str, timeout: float = JOB_TIMEOUT, every: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            job = json.loads(test.run("read_file(ROOT, 'state/last_job.json')"))
        except DevError as exc:
            if exc.code != 66:  # 66：還沒寫出來
                raise
            job = {}
        if job.get("id") == job_id and job.get("finished_at"):
            return job
        if time.monotonic() > deadline:
            raise DevError(f"等了 {timeout / 60:.0f} 分鐘還沒結束，之後用 --no-run 再比較", 124)
        time.sleep(every)


def cmd_compare(args, env) -> int:
    stamp = args.date
    if not DATE_RE.fullmatch(stamp):
        raise DevError("日期要是 YYYY-MM-DD", 2)
    snap = Path(args.snapshot) if args.snapshot else latest_snapshot()
    files = snapshot_members(snap.read_bytes())
    prod_md = files.get(f"reports/{stamp}.md")
    if prod_md is None:
        raise DevError(f"快照 {snap.name} 裡沒有 reports/{stamp}.md", 2)
    test = target("test", env)
    if not args.no_run:
        print(f"在 {test.label()} 用正式區 {stamp} 的素材重寫報告（會用到 Claude 額度，約幾分鐘到幾十分鐘）…")
        reply = json.loads(test.run(f"start_job('rewrite', {{'date': {stamp!r}}})"))
        job = wait_job(test, reply["id"])
        if job.get("exit_code"):
            with contextlib.suppress(DevError):
                tail = test.run(f"read_file(ROOT, {job.get('log', 'logs/ops-rewrite.log')!r})").decode("utf-8", "replace")
                print("\n".join(tail.splitlines()[-20:]), file=sys.stderr)
            raise DevError(f"重寫失敗，exit {job['exit_code']}", 1)
    test_md = test.run(f"read_file(ROOT, 'reports/{stamp}.md')").decode("utf-8")
    prod_text = prod_md.decode("utf-8")
    curated = json.loads(files.get(f"data/curated/{stamp}.json", b"{}") or b"{}")
    titles = {i["uid"]: i.get("title", "") for i in curated.get("items", []) if isinstance(i, dict) and i.get("uid")}
    out = WORK / "compare"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stamp}-prod.md").write_text(prod_text, encoding="utf-8")
    (out / f"{stamp}-test.md").write_text(test_md, encoding="utf-8")
    a, b = prod_text.splitlines(), test_md.splitlines()
    (out / f"{stamp}.diff").write_text("\n".join(difflib.unified_diff(a, b, "正式", "測試", lineterm="")) + "\n",
                                       encoding="utf-8")
    (out / f"{stamp}.html").write_text(difflib.HtmlDiff(wrapcolumn=70).make_file(a, b, "正式", "測試", charset="utf-8"),
                                       encoding="utf-8")
    print(f"\n{stamp}：正式（快照 {snap.name}） vs 測試")
    print("\n".join(compare_reports(prod_text, test_md, titles)))
    print(f"\n逐行比較：{(out / f'{stamp}.html').relative_to(REPO).as_posix()}（瀏覽器開）、{(out / f'{stamp}.diff').relative_to(REPO).as_posix()}")
    return 0


# ---- 測試區的信 ----

def eml_to_html(raw: bytes) -> str:
    """.eml → 可以直接開的 HTML：信頭一段，再接 HTML 部分（純文字信就用 <pre>）。"""
    import email
    import email.policy
    from html import escape
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    head = "".join(f"<div><b>{k}:</b> {escape(str(msg[k] or ''))}</div>" for k in ("Subject", "From", "To", "Date"))
    body = msg.get_body(("html", "plain"))
    content = body.get_content() if body is not None else ""
    if body is None or body.get_content_type() != "text/html":
        content = f"<pre>{escape(content)}</pre>"
    files = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    if files:
        head += f"<div><b>附件:</b> {escape('、'.join(files))}</div>"
    banner = f'<div style="font:13px sans-serif;background:#fffbe6;border-bottom:1px solid #ddd;padding:8px">{head}</div>'
    return banner + content


def cmd_outbox(args, env) -> int:
    test = target("test", env)
    rows = json.loads(test.run("list_dir(ROOT, 'logs/outbox')"))
    if not rows:
        print("測試區的 logs/outbox/ 是空的（還沒寄過信，或沒設 NEWSLETTER_MAIL_OUTBOX）")
        return 0
    for r in rows[-10:]:
        print(f"{datetime.fromtimestamp(r['mtime']):%m-%d %H:%M}  {r['size'] / 1024:6.1f} KB  {r['name']}")
    name = args.name or rows[-1]["name"]
    if "/" in name or "\\" in name:
        raise DevError("--name 只要檔名", 2)
    raw = test.run(f"read_file(ROOT, {'logs/outbox/' + name!r})")
    out = WORK / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(raw)
    page = out / (Path(name).stem + ".html")
    page.write_text(eml_to_html(raw), encoding="utf-8")
    print(f"\n{name} 存在 {page.relative_to(REPO).as_posix()}（瀏覽器開）")
    return 0


# ---- 範例資料 ----

def sample_files(sample: Path = SAMPLE) -> dict[str, bytes]:
    return {p.relative_to(sample).as_posix(): p.read_bytes() for p in sorted(sample.rglob("*")) if p.is_file()}


def shift_dates(files: dict[str, bytes], last: date) -> dict[str, bytes]:
    """把範例資料裡出現的日期整組平移，讓最後一天變成 last（檔名與內容裡的 YYYY-MM-DD 都換）。"""
    found = sorted({m for n, d in files.items() for m in DATE_RE.findall(n + d.decode("utf-8"))})
    if not found:
        return files
    delta = last - date.fromisoformat(found[-1])
    mapping = {d: (date.fromisoformat(d) + delta).isoformat() for d in found}
    sub = lambda s: DATE_RE.sub(lambda m: mapping.get(m[0], m[0]), s)  # noqa: E731
    return {sub(n): sub(d.decode("utf-8")).encode("utf-8") for n, d in files.items()}


def cmd_seed(args, env) -> int:
    root = Path(args.root).resolve()
    files = sample_files()
    if not args.keep_dates:
        files = shift_dates(files, date.today())
    exists = [n for n in files if (root / n).exists()]
    if exists and not args.force:
        raise DevError(f"{root} 已經有資料（例如 {exists[0]}），要覆蓋加 --force", 2)
    for name, data in files.items():
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    print(f"範例資料寫進 {root}：{describe(files)}")
    return 0


# ---- 兩區狀態 ----

def format_status(name: str, st: dict) -> list[str]:
    w, tok, rec = st.get("worker", {}), st.get("token", {}), (st.get("worker") or {}).get("last") or {}
    lines = [f"[{name}]"]
    if "error" in w:
        lines.append(f"  worker：連不到（{w['error']}）")
    else:
        cur = w.get("current")
        lines.append(f"  worker：{w.get('state')}" + (f"（{cur.get('kind', 'run')}，{cur.get('started_at')} 開始）" if cur else "")
                     + (f"，下次 {w['next_run_at']}" if w.get("next_run_at") else "，不排程"))
    if rec:
        result = "還在跑" if not rec.get("finished_at") else ("成功" if rec.get("exit_code") == 0 else
                                                             f"失敗 exit {rec.get('exit_code')}（{rec.get('step')}）")
        lines.append(f"  最近一次執行：{rec.get('date')} {rec.get('trigger')}，{result}")
    left = f"，剩 {tok['days_left']} 天" if "days_left" in tok else ""
    lines.append(f"  token：{tok.get('state')}{left}")
    rep = st.get("reports", {})
    lines.append(f"  報告：{rep.get('count', 0)} 份，最新 {rep.get('latest') or '—'}；回饋 {st.get('feedback_rows', 0)} 列")
    return lines


def cmd_status(args, env) -> int:
    code = 0
    for name in ("prod", "test"):
        try:
            t = target(name, env)
            st = json.loads(t.run("env_status(ROOT)"))
        except DevError as exc:
            print(f"[{name}]\n  {exc}")
            code = 1
            continue
        print("\n".join(format_status(f"{name}：{t.ssh or '本機'}", st)))
    return code


# ---- 回饋備份還原 ----

def check_feedback(data: bytes) -> tuple[dict, list[str]]:
    """檢查 feedback.jsonl：每列是有 uid 與 mark 的 JSON。回傳 (統計, 錯誤)。"""
    errors, uids, dates, rows = [], set(), [], 0
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {}, [f"不是 UTF-8：{exc}"]
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            errors.append(f"第 {n} 列不是 JSON：{exc}")
            continue
        if not isinstance(row, dict) or not UID_RE.fullmatch(str(row.get("uid", ""))) or not isinstance(row.get("mark"), str):
            errors.append(f"第 {n} 列缺 uid（16 位 hex）或 mark")
            continue
        rows += 1
        uids.add(row["uid"])
        if isinstance(row.get("collected_at"), str):
            dates.append(row["collected_at"][:10])
    stats = {"rows": rows, "uids": len(uids), "first": min(dates, default=None), "last": max(dates, default=None),
             "uid_set": uids}
    if not rows and not errors:
        errors.append("一列都沒有")
    return stats, errors


def cmd_restore_feedback(args, env) -> int:
    data = Path(args.file).read_bytes()
    stats, errors = check_feedback(data)
    if errors:
        raise DevError("備份檔有問題，不還原：\n  " + "\n  ".join(errors[:10]), 65)
    print(f"備份：{stats['rows']} 列、{stats['uids']} 則，{stats['first']} ～ {stats['last']}")
    with contextlib.suppress(DevError):
        snap = latest_snapshot()
        now, _ = check_feedback(snapshot_members(snap.read_bytes()).get(FEEDBACK, b""))
        if now:
            lost = len(now["uid_set"] - stats["uid_set"])
            print(f"和最新快照（{snap.name}，{now['rows']} 列）比：備份少了 {lost} 則"
                  + ("——備份之後才按的回饋，還原會失去這些" if lost else ""))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add_bytes(tar, FEEDBACK, data)
    if args.to_local:
        result = local_import(Path(args.root).resolve(), buf.getvalue(), {FEEDBACK})
        where = f"本機 {Path(args.root).resolve()}"
    else:
        test = target("test", env)
        result = json.loads(test.run(f"print(json.dumps(import_snapshot(ROOT, sys.stdin.buffer, {{{FEEDBACK!r}}})))",
                                     buf.getvalue()))
        where = test.label()
    ok = result["sha256"].get(FEEDBACK) == hashlib.sha256(data).hexdigest()
    print(f"已還原到 {where}：state/feedback.jsonl " + ("內容和備份一致" if ok else "內容和備份不一致！"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="newsletter-ops 開發工具", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="設定與說明見檔頭，或 deploy/README.md「開發工具」")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot", help="正式區的資料複製到測試區（或本機）")
    p.add_argument("--from", dest="from_file", help="用之前存的快照，不連正式區")
    p.add_argument("--no-import", action="store_true", help="只打包存檔")
    p.add_argument("--to-local", action="store_true", help="匯入本機（--root）而不是測試區")
    p.add_argument("--root", default=str(REPO), help="--to-local 的資料根目錄，預設 repo 根（本機開發的 ROOT）")
    p = sub.add_parser("compare", help="測試區用正式區某天的素材重寫報告，和正式區比較")
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("--no-run", action="store_true", help="不重寫，直接比較測試區現有的那份")
    p.add_argument("--snapshot", help="正式區那份報告取自哪個快照，預設最新的")
    p = sub.add_parser("outbox", help="測試區寄出的信")
    p.add_argument("--name", help="哪一封（檔名），預設最新的")
    p = sub.add_parser("seed", help="本機開發用的範例資料")
    p.add_argument("--root", default=str(REPO), help="資料根目錄，預設 repo 根")
    p.add_argument("--keep-dates", action="store_true", help="不把日期平移到今天")
    p.add_argument("--force", action="store_true", help="已有資料也覆蓋")
    sub.add_parser("status", help="兩區的 worker、token、最近一次執行")
    p = sub.add_parser("restore-feedback", help="回饋備份還原到測試區（或本機）")
    p.add_argument("file", help="備份信的附件 feedback-YYYY-MM-DD.jsonl")
    p.add_argument("--to-local", action="store_true")
    p.add_argument("--root", default=str(REPO))
    args = ap.parse_args(argv)
    handlers = {"snapshot": cmd_snapshot, "compare": cmd_compare, "outbox": cmd_outbox, "seed": cmd_seed,
                "status": cmd_status, "restore-feedback": cmd_restore_feedback}
    try:
        return handlers[args.cmd](args, load_env())
    except DevError as exc:
        print(f"[devtools] {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
