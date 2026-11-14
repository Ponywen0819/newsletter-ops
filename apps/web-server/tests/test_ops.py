"""/api/ops*：worker 狀態與手動執行、執行紀錄、設定。真的 HTTP 伺服器＋暫存目錄＋假的 worker。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from helpers import served

from newsletter_web_server.server import Site

UID = "0123456789abcdef"


class FakeWorker:
    """假的 worker：/status 回 status，POST 記下路徑並回 reply。"""

    def __init__(self):
        self.status = {"state": "idle", "scheduled": True, "run_at": "08:00",
                       "next_run_at": "2026-09-29T00:00:00+00:00", "current": None, "last": None, "job": None}
        self.reply: tuple[int, dict] = (202, {"started_at": "2026-09-28T01:00:00+00:00", "id": "abc"})
        self.posts: list[str] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, fake.status) if self.path == "/status" else self._send(404, {"error": "not found"})

            def do_POST(self):
                fake.posts.append(self.path)
                self._send(*fake.reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "2026-09-28.md").write_text(
        f"# 每日晨間簡報 2026-09-28\n\n- **[a](https://a.example/1)**\n<!-- mark:    uid={UID} -->\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def worker():
    fake = FakeWorker()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def req(root: Path, worker: FakeWorker):
    with served(Site(root, today=lambda: "2026-09-28", worker_url=worker.url)) as r:
        yield r


def get(req, path: str) -> tuple[int, dict]:
    status, _, body = req("GET", path)
    return status, json.loads(body)


def post(req, path: str, body) -> tuple[int, dict]:
    status, _, text = req("POST", path, body=body)
    return status, json.loads(text)


def test_status_empty(req) -> None:
    status, st = get(req, "/api/ops")
    assert status == 200 and st["job"] is None and st["log"] == [] and st["last_run"] is None
    assert st["worker"] == {"online": True, "scheduled": True, "run_at": "08:00",
                            "next_run_at": "2026-09-29T00:00:00+00:00", "state": "idle", "current": None}


def test_status(req, root: Path, worker: FakeWorker) -> None:
    worker.status.update(state="running", current={"kind": "run", "trigger": "manual", "started_at": "x"})
    (root / "state").mkdir()
    (root / "state" / "last_run.json").write_text(json.dumps(
        {"date": "2026-09-28", "trigger": "schedule", "started_at": "a", "finished_at": "b", "exit_code": 4, "step": "write"}))
    (root / "logs").mkdir()
    (root / "logs" / "2026-09.log").write_text("舊的一行\n=== 這次 ===\n第二行\n", encoding="utf-8")
    (root / "state" / "last_job.json").write_text(json.dumps(
        {"id": "j1", "kind": "run", "from": "write", "log": "logs/2026-09.log",
         "offset": len("舊的一行\n".encode()), "exit_code": None, "finished_at": None}), encoding="utf-8")
    st = get(req, "/api/ops")[1]
    assert st["worker"]["online"] is True and st["worker"]["state"] == "running"
    assert st["worker"]["current"]["kind"] == "run"
    assert st["last_run"]["exit_code"] == 4 and st["last_run"]["step"] == "write"
    assert st["job"]["id"] == "j1" and st["log"] == ["=== 這次 ===", "第二行"]  # 從 offset 起


def test_worker_down_and_bad_log_path(root: Path) -> None:
    """worker 連不上：狀態照樣回（離線），結果與 log 從 volume 讀；送出 503。"""
    (root / "state").mkdir()
    (root / "secret.txt").write_text("不能讀", encoding="utf-8")
    (root / "state" / "last_job.json").write_text(json.dumps(
        {"id": "j", "kind": "kg-rebuild", "log": "logs/../secret.txt", "offset": 0}), encoding="utf-8")
    with served(Site(root, today=lambda: "2026-09-28", worker_url="http://127.0.0.1:9")) as r:
        st = get(r, "/api/ops")[1]
        assert st["worker"] == {"online": False} and st["job"]["id"] == "j" and st["log"] == []
        assert post(r, "/api/ops/request", {"kind": "run"})[0] == 503


def test_request(req, worker: FakeWorker) -> None:
    status, body = post(req, "/api/ops/request", {"kind": "run"})
    assert status == 200 and body == {"started_at": "2026-09-28T01:00:00+00:00", "id": "abc"}
    assert post(req, "/api/ops/request", {"kind": "run", "from": "render"})[0] == 200
    assert post(req, "/api/ops/request", {"kind": "kg-rebuild"})[0] == 200
    assert post(req, "/api/ops/request", {"kind": "sources-check"})[0] == 200
    assert worker.posts == ["/run?from=collect", "/run?from=render", "/job?kind=kg-rebuild", "/job?kind=sources-check"]
    # worker 說正在忙：原因照轉
    worker.reply = (409, {"error": "已有工作在跑，等它結束再試"})
    assert post(req, "/api/ops/request", {"kind": "run"}) == (409, {"error": "已有工作在跑，等它結束再試"})
    worker.reply = (500, {"error": "boom"})
    assert post(req, "/api/ops/request", {"kind": "run"})[0] == 502


@pytest.mark.parametrize("body", [{}, {"kind": 1}, {"kind": "deploy"}, {"kind": "run", "from": "nope"},
                                  {"kind": "kg-rebuild", "from": "collect"}, ["run"]])
def test_request_bad(req, worker: FakeWorker, body) -> None:
    assert post(req, "/api/ops/request", body)[0] == 400
    assert worker.posts == []  # 不合法的不轉給 worker


def metric(day: str, hhmm: str, stage: str, **fields) -> str:
    return json.dumps({"ts": f"{day}T{hhmm}:00+08:00", "stage": stage, **fields})


def test_metrics(req, root: Path) -> None:
    d = root / "logs" / "metrics"
    d.mkdir(parents=True)
    (d / "2026-09-28.jsonl").write_text("\n".join([
        metric("2026-09-28", "08:00", "fetch", seconds=10.0, run_id="r1", label="prod"),
        metric("2026-09-28", "08:01", "curate", seconds=2.5, items=30, run_id="r1", label="prod"),
        metric("2026-09-28", "08:05", "claude", seconds=200.0, run_id="r1", label="prod", api_turns=12,
               total_cost_usd=0.5, tokens={"input": 10, "cache_write": 20, "cache_read": 30, "output": 40}),
        metric("2026-09-28", "08:06", "kg_extract", run_id="r1", label="prod", total_cost_usd=0.25),
        metric("2026-09-28", "08:07", "pipeline", seconds=420.0, run_id="r1", label="prod", trigger="schedule",
               exit_code=0, step=None),
        metric("2026-09-28", "09:00", "fetch", seconds=9.0, run_id="r2", label="ops"),
        '{"ts": "寫到一半',
    ]) + "\n", encoding="utf-8")
    (d / "2026-09-20.jsonl").write_text(metric("2026-09-20", "08:00", "pipeline", seconds=5.0, run_id="old",
                                               trigger="retry", exit_code=4, step="write") + "\n", encoding="utf-8")
    st = get(req, "/api/ops/metrics?days=7")[1]
    assert st["days"] == 7 and st["all"] is False and len(st["runs"]) == 1  # 9/20 超過 7 天、ops 標籤不算
    run = st["runs"][0]
    assert run == {"date": "2026-09-28", "time": "08:00", "label": "prod", "trigger": "schedule",
                   "result": {"exit_code": 0, "step": None},
                   "seconds": {"fetch": 10.0, "curate": 2.5, "claude": 200.0, "pipeline": 420.0},
                   "tokens": {"input": 60, "output": 40}, "turns": 12, "usd": 0.75, "items": 30}
    every = get(req, "/api/ops/metrics?days=14&all=1")[1]["runs"]
    assert [(r["date"], r["time"], r["label"]) for r in every] == [
        ("2026-09-28", "09:00", "ops"), ("2026-09-28", "08:00", "prod"), ("2026-09-20", "08:00", "prod")]  # 新到舊
    assert every[2]["result"] == {"exit_code": 4, "step": "write"} and every[2]["tokens"] is None
    assert get(req, "/api/ops/metrics?days=5")[0] == 400
    assert get(req, "/api/ops/metrics?days=x")[0] == 400


def test_config(req, root: Path) -> None:
    conf = root / "config"
    (conf / "sources.d").mkdir(parents=True)
    (conf / "config.json").write_text('{"debug": true, "timezone": "Asia/Taipei"}', encoding="utf-8")
    (conf / "interests.md").write_text("# 興趣\n- 半導體\n", encoding="utf-8")
    (conf / "sources.d" / "b.json").write_text(json.dumps({"topic": "b", "label": "B 類", "defaults": {"weight": 1.2},
        "feeds": [{"name": "F", "type": "rss", "url": "https://f.example/rss"}, "壞的"]}), encoding="utf-8")
    (conf / "sources.d" / "a.json").write_text("{壞掉", encoding="utf-8")
    st = get(req, "/api/ops/config")[1]
    assert st["config"] == {"debug": True, "timezone": "Asia/Taipei"} and st["interests"].startswith("# 興趣")
    assert st["sources"] == [
        {"file": "a.json", "error": "讀不到或不是合法的 JSON"},
        {"file": "b.json", "topic": "b", "label": "B 類", "defaults": {"weight": 1.2},
         "feeds": [{"name": "F", "type": "rss", "url": "https://f.example/rss"}]},
    ]
    assert get(req, "/api/ops/metrics?days=7")[1]["debug"] is True


def test_config_missing(req) -> None:
    assert get(req, "/api/ops/config")[1] == {"config": None, "sources": [], "interests": None}


def test_ops_pages(req) -> None:
    for path in ("/ops", "/ops/metrics", "/ops/config"):
        assert req("GET", path)[0] in (200, 503), path  # SPA 路由（沒建置前端時 503）
    assert req("GET", "/ops/nope")[0] in (404, 503)


def test_trusted_flag(req, root: Path) -> None:
    status, body = post(req, "/api/kg-flag", {"uid": UID, "flag": "trusted"})
    assert status == 200 and body == {"uid": UID, "flag": "trusted"}
    assert (root / "state" / "kg_flags.jsonl").read_text(encoding="utf-8").count('"trusted"') == 1
