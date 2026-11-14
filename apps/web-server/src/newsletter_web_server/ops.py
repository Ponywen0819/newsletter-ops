"""維運頁（/ops）的資料：worker 的狀態與手動執行、執行紀錄（metrics）、目前的設定。

狀態與手動執行直接呼叫 worker 的 HTTP 介面（GET /status、POST /run、POST /job；Docker 部署時 web 與 worker 同在 be 網路）。
最近一次手動執行的結果與上次每日流程從共用 volume 讀（state/last_job.json、state/last_run.json），worker 停了也看得到。
只限本機：nginx 在 Tunnel 那一側把 /ops 與 /api/ops* 擋掉（docker/nginx.conf），這裡不另外驗證。
設定是唯讀的：config/ 跟著映像走（也在版控裡），要改就改 repo 再部署。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from newsletter_shared.metrics import split_runs

from .errors import ApiError

WORKER_URL_ENV = "NEWSLETTER_WORKER_URL"
DEFAULT_WORKER_URL = "http://127.0.0.1:8790"  # systemd 版與本機開發；compose 設成 http://worker:8790
WORKER_TIMEOUT = 5  # 秒；/status 與 /run 都是馬上回（執行在背景）
RUN_FROM = ("collect", "write", "render", "send")
JOB_KINDS = ("kg-rebuild", "sources-check")
LOG_TAIL_LINES = 80
LOG_TAIL_BYTES = 64 * 1024
METRICS_DAYS = (7, 14, 30, 90)
STAGES = ("fetch", "curate", "search", "enrich", "claude", "render", "pipeline")
COST_STAGES = ("claude", "search", "kg_extract", "kg_relate")  # 有 total_cost_usd 的階段（只有 SDK 路線）


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Ops:
    def __init__(self, root: Path, today: Callable[[], str], worker_url: str | None = None):
        self.root = root
        self.today = today
        self.worker_url = (worker_url or os.environ.get(WORKER_URL_ENV) or DEFAULT_WORKER_URL).rstrip("/")

    # ---------- 狀態與手動執行 ----------

    def _call(self, method: str, path: str) -> tuple[int, dict]:
        """呼叫 worker；連不上丟 ConnectionError。回 (狀態碼, JSON)。"""
        req = urllib.request.Request(self.worker_url + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=WORKER_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read())
            except ValueError:
                body = {}
            return exc.code, body if isinstance(body, dict) else {}
        except (OSError, ValueError) as exc:  # URLError、逾時、回應不是 JSON
            raise ConnectionError(str(exc)) from None

    def _worker(self) -> dict:
        try:
            code, st = self._call("GET", "/status")
        except ConnectionError:
            return {"online": False}
        if code != HTTPStatus.OK:
            return {"online": False}
        return {"online": True, **{k: st.get(k) for k in ("scheduled", "run_at", "next_run_at", "state", "current")}}

    def _log_tail(self, job: dict) -> list[str]:
        """job 指的 log（只限 logs/ 底下），從 offset 起的最後 LOG_TAIL_LINES 行。"""
        logs = (self.root / "logs").resolve()
        rel = job.get("log")
        if not isinstance(rel, str):
            return []
        try:
            path = (self.root / rel).resolve()
            if not path.is_relative_to(logs):
                return []
            offset = job.get("offset") if isinstance(job.get("offset"), int) else 0
            with path.open("rb") as fh:
                size = fh.seek(0, 2)
                fh.seek(max(offset, size - LOG_TAIL_BYTES))
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = text.splitlines()
        if offset < size - LOG_TAIL_BYTES and lines:
            lines = lines[1:]  # 從中間切進來的第一行不完整
        return lines[-LOG_TAIL_LINES:]

    def status(self) -> dict:
        job = _read(self.root / "state" / "last_job.json")
        last = _read(self.root / "state" / "last_run.json")
        return {
            "worker": self._worker(),
            "job": job,
            "log": self._log_tail(job) if job else [],
            "last_run": {k: last.get(k) for k in ("date", "trigger", "started_at", "finished_at", "exit_code", "step")}
            if last else None,
        }

    def request(self, data) -> dict:
        """POST /api/ops/request：{"kind": "run", "from": "collect"} 或 {"kind": "kg-rebuild"} 等，轉給 worker。"""
        if not isinstance(data, dict) or not isinstance(data.get("kind"), str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "需要字串欄位 kind")
        kind, start = data["kind"], data.get("from")
        if kind == "run":
            start = "collect" if start is None else start
            if start not in RUN_FROM:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"from 只能是 {'、'.join(RUN_FROM)}")
            path = "/run?" + urlencode({"from": start})
        elif kind in JOB_KINDS:
            if start is not None:
                raise ApiError(HTTPStatus.BAD_REQUEST, "只有 run 可以指定 from")
            path = "/job?" + urlencode({"kind": kind})
        else:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"kind 只能是 run、{'、'.join(JOB_KINDS)}")
        try:
            code, body = self._call("POST", path)
        except ConnectionError:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "連不上 worker，送出也不會有人執行") from None
        if code == HTTPStatus.ACCEPTED:
            return {"started_at": body.get("started_at"), "id": body.get("id")}
        if code == HTTPStatus.CONFLICT:
            raise ApiError(HTTPStatus.CONFLICT, body.get("error") or "worker 正在執行別的工作，等它結束再試")
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"worker 回應 {code}：{body.get('error', '')}")

    # ---------- 執行紀錄（logs/metrics/*.jsonl） ----------

    def _debug_on(self) -> bool:
        try:
            return bool(json.loads((self.root / "config" / "config.json").read_text(encoding="utf-8")).get("debug"))
        except (OSError, ValueError, AttributeError):
            return False

    @staticmethod
    def _run_row(day: str, run: dict[str, dict]) -> dict:
        """newsletter-metrics summary 的一列，換成結構化資料。"""
        first = min(run.values(), key=lambda r: r["ts"])
        claude, pipeline = run.get("claude", {}), run.get("pipeline")
        tok = claude.get("tokens", {})
        costs = [r["total_cost_usd"] for r in (run.get(s, {}) for s in COST_STAGES) if r.get("total_cost_usd") is not None]
        return {
            "date": day,
            "time": first["ts"][11:16],
            "label": first.get("label", "prod"),
            "trigger": (pipeline or {}).get("trigger"),
            "result": None if pipeline is None else {"exit_code": pipeline.get("exit_code"), "step": pipeline.get("step")},
            "seconds": {s: run[s]["seconds"] for s in STAGES if isinstance(run.get(s, {}).get("seconds"), (int, float))},
            "tokens": {"input": tok.get("input", 0) + tok.get("cache_write", 0) + tok.get("cache_read", 0),
                       "output": tok.get("output", 0)} if tok else None,
            "turns": claude.get("api_turns"),
            "usd": round(sum(costs), 4) if costs else None,
            "items": run.get("curate", {}).get("items"),
        }

    def metrics(self, days: str, show_all: str) -> dict:
        try:
            span = int(days)
        except ValueError:
            span = 0
        if span not in METRICS_DAYS:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"days 只能是 {'、'.join(map(str, METRICS_DAYS))}")
        every = show_all == "1"
        end = date.fromisoformat(self.today())
        runs = []
        for n in range(span):  # 新到舊
            day = (end - timedelta(days=n)).isoformat()
            try:
                lines = (self.root / "logs" / "metrics" / f"{day}.jsonl").read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            rows = []
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # 寫到一半的最後一行
                if isinstance(row, dict) and isinstance(row.get("stage"), str) and isinstance(row.get("ts"), str):
                    rows.append(row)
            if not every:
                rows = [r for r in rows if r.get("label", "prod") == "prod"]  # 舊紀錄沒有 label，當 prod
            runs.extend(reversed([self._run_row(day, run) for run in split_runs(rows) if run]))
        return {"days": span, "all": every, "debug": self._debug_on(), "runs": runs}

    # ---------- 設定（唯讀） ----------

    def config(self) -> dict:
        conf_dir = self.root / "config"
        try:
            config = json.loads((conf_dir / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = None
        sources = []
        for path in sorted((conf_dir / "sources.d").glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sources.append({"file": path.name, "error": "讀不到或不是合法的 JSON"})
                continue
            if not isinstance(data, dict):
                sources.append({"file": path.name, "error": "格式不符"})
                continue
            feeds = [f for f in data.get("feeds", []) if isinstance(f, dict)]
            sources.append({"file": path.name, "topic": data.get("topic"), "label": data.get("label"),
                            "defaults": data.get("defaults") or {}, "feeds": feeds})
        try:
            interests = (conf_dir / "interests.md").read_text(encoding="utf-8")
        except OSError:
            interests = None
        return {"config": config, "sources": sources, "interests": interests}
