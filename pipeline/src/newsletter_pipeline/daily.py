#!/usr/bin/env python3
"""每日流程：抓取 → agent 寫報告 → render → 寄信，整條包在一把防重疊的鎖裡。

用法：uv run newsletter-run [newsletter-fetch 的參數，如 --lookback 72]
      uv run newsletter-run --selftest

排程（systemd timer、cron、docker 的 scheduler）經 run_daily.sh 呼叫它；run_daily.sh 只負責補 PATH、載入機密。
四個步驟各自是獨立的程序（python -m …），由這裡依序啟動；鎖握在這個程序上，
所以從第一步到最後一步都不會有第二條 pipeline 插進來（手動補跑撞上排程時，後到的直接放棄）。

任一步失敗就停下，exit code 是該步的（agent 的 1～6 見 agent/README.md）；被訊號殺掉的算 128+訊號，和 shell 一致。
鎖被占用是 75（EX_TEMPFAIL）：另一條在跑，這次沒執行。
輸出（這裡的進度行加各步驟的 stdout／stderr）全寫到 log 參數；run_daily.sh 把它導向 logs/<YYYY-MM>.log。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import IO

from newsletter_shared import lock
from newsletter_shared.paths import ROOT

EXIT_BUSY = 75
LOCK = ROOT / "state" / "pipeline"  # 實際鎖的是 state/pipeline.lock
MODULES = {
    "fetch": "newsletter_agent.run",
    "agent": "newsletter_agent.agent_run",
    "render": "newsletter_notify.render_email",
    "send": "newsletter_notify.send_email",
}


def say(log: IO[str], message: str) -> None:
    print(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} === {message}", file=log, flush=True)


def step(log: IO[str], label: str, module: str, *args: str,
         input: str | None = None, capture: bool = False) -> tuple[int, str]:
    """跑一步，回傳 (exit code, stdout)。stdout 只有 capture 時才收；否則和 stderr 一起進 log。"""
    say(log, label)
    done = subprocess.run([sys.executable, "-m", module, *args], cwd=ROOT, input=input, encoding="utf-8",
                          stdout=subprocess.PIPE if capture else log, stderr=log)
    code = done.returncode if done.returncode >= 0 else 128 - done.returncode
    return code, done.stdout or ""


def run_pipeline(fetch_args: list[str], log: IO[str], modules: dict[str, str] = MODULES,
                 lock_path: Path = LOCK) -> int:
    try:
        with lock.locked(lock_path, timeout=0):
            return _run(fetch_args, log, modules)
    except lock.LockBusy:
        say(log, "已有另一條 pipeline 在跑（state/pipeline.lock 被占用），這次不執行")
        return EXIT_BUSY


def _run(fetch_args: list[str], log: IO[str], modules: dict[str, str]) -> int:
    def go(label: str, key: str, *args: str, input: str | None = None, capture: bool = False) -> tuple[int, str]:
        code, out = step(log, label, modules[key], *args, input=input, capture=capture)
        if code:
            say(log, f"失敗，exit {code}：{label}")
        return code, out

    code, _ = go(" ".join(["newsletter-fetch", "--no-report", *fetch_args]), "fetch", "--no-report", *fetch_args)
    if code:
        return code
    code, _ = go("newsletter-agent", "agent")
    if code:
        return code
    code, meta = go("newsletter-render", "render", capture=True)  # stdout 是一行 JSON，下一步的輸入
    if code:
        return code
    code, _ = go("newsletter-send", "send", input=meta)
    if code:
        return code
    say(log, "完成")
    return 0


# 自我檢查用的假步驟：記下收到的參數與 stdin，依 FAKE_FAIL=<步驟>:<exit code 或 kill> 失敗
FAKE_STEP = '''import os, sys
name = "NAME"
out = os.environ["FAKE_OUT"]
data = sys.stdin.read() if name == "send" else ""
open(f"{out}/{name}", "w").write(" ".join(sys.argv[1:]) + "|" + data)
print(name, "stderr", file=sys.stderr)
if name == "render":
    print('{"subject": "s"}')
fail = os.environ.get("FAKE_FAIL", "").split(":")
if fail[0] == name:
    if fail[1] == "kill":
        os.kill(os.getpid(), 9)
    sys.exit(int(fail[1]))
'''


def selftest() -> None:
    import os
    import tempfile
    import threading

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name in MODULES:
            (tmp / f"fake_{name}.py").write_text(FAKE_STEP.replace("NAME", name), encoding="utf-8")
        fakes = {name: f"fake_{name}" for name in MODULES}
        os.environ.update(FAKE_OUT=str(tmp), PYTHONPATH=str(tmp))
        lock_path = tmp / "state" / "pipeline"

        def run(*args: str, fail: str = "") -> tuple[int, str]:
            os.environ["FAKE_FAIL"] = fail
            for name in MODULES:
                (tmp / name).unlink(missing_ok=True)
            with tempfile.TemporaryFile("w+", encoding="utf-8") as log:
                code = run_pipeline(list(args), log, fakes, lock_path)
                log.seek(0)
                return code, log.read()

        def ran(name: str) -> bool:
            return (tmp / name).exists()

        # 全部成功：參數轉給 fetch、render 的 stdout 原封不動餵給 send、各步驟的 stderr 進 log
        code, text = run("--lookback", "72")
        assert code == 0, (code, text)
        assert (tmp / "fetch").read_text() == "--no-report --lookback 72|"
        assert (tmp / "send").read_text() == '|{"subject": "s"}\n'
        assert "agent stderr" in text and "render stderr" in text and text.rstrip().endswith("完成"), text
        assert '"subject"' not in text  # render 的 stdout 是給 send 的，不進 log

        # 任一步失敗：後面的不跑、exit code 是該步的、log 一行說明；每次都能重新拿到鎖（失敗也會放鎖）
        code, text = run(fail="agent:5")
        assert code == 5 and ran("fetch") and ran("agent") and not ran("render") and not ran("send")
        assert "失敗，exit 5：newsletter-agent" in text and "完成" not in text
        code, text = run(fail="fetch:3")
        assert code == 3 and ran("fetch") and not ran("agent")
        code, text = run(fail="render:kill")
        assert code == 128 + 9 and ran("render") and not ran("send"), code

        # 別的執行緒（等同別的程序）拿著鎖：這次不執行，一步都不跑；放掉之後恢復正常
        holding, release = threading.Event(), threading.Event()

        def hold() -> None:
            with lock.locked(lock_path):
                holding.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        assert holding.wait(5)
        try:
            code, text = run()
        finally:
            release.set()
            t.join()
        assert code == EXIT_BUSY and "已有另一條 pipeline" in text and not any(ran(n) for n in MODULES)
        assert run()[0] == 0
    print("ok")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        selftest()
        return 0
    return run_pipeline(argv, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
