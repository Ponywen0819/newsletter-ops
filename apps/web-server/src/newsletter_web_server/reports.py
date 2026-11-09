"""晨報與回饋：讀 reports/、data/curated/，寫 state/feedback.jsonl。"""
from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from newsletter_shared import feedback, lock, report_data
from newsletter_shared import today as shared_today

from .errors import ApiError, NotFound

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
UID_RE = re.compile(r"[0-9a-f]{16}")
MARKS = {"+", "-", ""}  # "" ＝ 取消
SEARCH_LIMIT = 50
SEARCH_MAX_QUERY = 100
_COMMENT = re.compile(r"<!--.*?-->")
_LINE_PREFIX = re.compile(r"^[\s>*-]*(?:#+\s*)?")


def level(mark: str) -> str:
    """feedback.jsonl 裡可能有 feedback.py 收進來的 ++ / --，網頁只分兩級。"""
    return mark[:1] if mark[:1] in ("+", "-") else ""


class Reports:
    def __init__(self, root: Path, today: Callable[[], str] | None = None):
        self.root = root
        self.today = today or (lambda: shared_today(root))
        self.feedback_path = root / "state" / "feedback.jsonl"


    def report_dates(self) -> list[str]:
        """reports/*.md 的日期，新到舊。"""
        stems = (p.stem for p in (self.root / "reports").glob("*.md"))
        return sorted((s for s in stems if DATE_RE.fullmatch(s)), reverse=True)

    def _report_path(self, date: str) -> Path:
        return self.root / "reports" / f"{date}.md"

    def marks(self) -> dict[str, str]:
        """uid → 目前的標記（'+' / '-'；沒標或已取消的不在裡面）。"""
        if not self.feedback_path.exists():
            return {}
        with lock.locked(self.feedback_path):
            rows = feedback.read_feedback(self.feedback_path)
        return {uid: m for uid, row in rows.items() if (m := level(row.get("mark", "")))}

    def report_payload(self, date: str) -> dict:
        try:
            text = self._report_path(date).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NotFound(date) from None
        try:
            report = report_data.parse_report(text)
        except ValueError as exc:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, f"報告格式不符：{exc}") from None
        shown = set(report_data.uids_of(report))
        dates = self.report_dates()  # 新到舊
        i = dates.index(date)
        return {"date": date, "report": report, "marks": {u: m for u, m in self.marks().items() if u in shown},
                "prev": dates[i + 1] if i + 1 < len(dates) else None, "next": dates[i - 1] if i else None}

    def run_record(self, date: str) -> dict | None:
        """worker 寫的 state/last_run.json，只回當天的；沒有或壞掉是 None。"""
        try:
            rec = json.loads((self.root / "state" / "last_run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(rec, dict) or rec.get("date") != date:
            return None
        return {k: rec.get(k) for k in ("trigger", "started_at", "finished_at", "exit_code", "step")}

    def today_payload(self) -> dict:
        date = self.today()
        latest = self.report_dates()
        try:
            payload = self.report_payload(date)
        except NotFound:
            payload = {"date": date, "report": None, "marks": {}}
        return {**payload, "latest": latest[0] if latest else None, "run": self.run_record(date)}

    def list_payload(self) -> dict:
        rows = []
        for date in self.report_dates():
            text = self._report_path(date).read_text(encoding="utf-8")
            line = next((ln for ln in text.splitlines() if "今日頭條" in ln), "")
            headline = report_data.plain(re.sub(r"^[>\s]*\**今日頭條[：:]\**\s*", "", line))
            rows.append({"date": date, "headline": headline})
        return {"reports": rows}

    def search(self, q: str) -> dict:
        """所有晨報裡含 q 的行（不分大小寫，去掉 Markdown 語法與 mark 註解），新到舊，最多 SEARCH_LIMIT 筆。
        q 用空白分成多個詞時，一行要每個詞都有才算。"""
        q = q.strip()
        if len(q) > SEARCH_MAX_QUERY:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"搜尋字串最多 {SEARCH_MAX_QUERY} 字")
        results: list[dict] = []
        if q:
            terms = q.casefold().split()
            for date in self.report_dates():
                for line in self._report_path(date).read_text(encoding="utf-8").splitlines():
                    text = report_data.plain(_LINE_PREFIX.sub("", _COMMENT.sub("", line)))
                    if all(t in text.casefold() for t in terms):
                        results.append({"date": date, "text": text})
                        if len(results) > SEARCH_LIMIT:
                            return {"q": q, "results": results[:SEARCH_LIMIT], "truncated": True}
        return {"q": q, "results": results, "truncated": False}

    def feedback_stats(self) -> dict:
        """newsletter-feedback --stats 的網頁版：依來源／主題／關鍵字的正負面則數，則數多的在前。"""
        if self.feedback_path.exists():
            with lock.locked(self.feedback_path):
                rows = feedback.read_feedback(self.feedback_path)
        else:
            rows = {}
        groups = {group: [{"name": name, "pos": pos, "neg": neg}
                          for name, (pos, neg) in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))]
                  for group, counts in feedback.stats(rows).items()}
        return {"total": sum(1 for r in rows.values() if r.get("mark") in feedback.VALID_MARKS), "groups": groups}

    def _find_report(self, uid: str) -> str | None:
        """含有這個 uid 的最新一份報告（日期）。"""
        needle = f"uid={uid}"
        for date in self.report_dates():
            if needle in self._report_path(date).read_text(encoding="utf-8"):
                return date
        return None

    def _curated_item(self, date: str, uid: str) -> dict:
        try:
            payload = json.loads((self.root / "data" / "curated" / f"{date}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        found = [*payload.get("items", []), *payload.get("near_misses", [])]  # writer 可能撈回 near_misses
        return next((i for i in found if i.get("uid") == uid), {})

    def feedback_target(self, uid: str) -> dict:
        """email 連結的確認頁要顯示的資料。任何一份報告裡都沒有這個 uid ＝ NotFound。"""
        date = self._find_report(uid) if UID_RE.fullmatch(uid) else None
        if date is None:
            raise NotFound(uid)
        return {"uid": uid, "date": date, "title": self._curated_item(date, uid).get("title", ""),
                "mark": self.marks().get(uid, "")}

    def record(self, uid: str, mark: str) -> dict:
        """append 一列到 feedback.jsonl，回傳寫入的那一列。mark '' ＝ 取消。"""
        if mark not in MARKS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "mark 只能是 +、- 或空字串（取消）")
        if not UID_RE.fullmatch(uid):
            raise ApiError(HTTPStatus.BAD_REQUEST, "uid 格式不符")
        date = self._find_report(uid)
        if date is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "任何一份報告裡都找不到這個 uid")
        row = feedback.build_row(uid, mark, f"{date}.md", self._curated_item(date, uid))
        with lock.locked(self.feedback_path):
            feedback.append_rows(self.feedback_path, [row])
        return row
