"""晨報與回饋：讀 reports/、data/curated/，寫 state/feedback.jsonl。"""
from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Generic, TypeVar

from newsletter_shared import feedback, kg_flags, lock, report_data
from newsletter_shared import today as shared_today

from .errors import ApiError, NotFound

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
UID_RE = re.compile(r"[0-9a-f]{16}")
MARKS = {"+", "-", ""}  # "" ＝ 取消
SEARCH_LIMIT = 50
SEARCH_MAX_QUERY = 100
PARSED_CACHE = 40  # 解析好的晨報留幾份：離線副本一次同步今天＋最近 30 份
CURATED_FIELDS = ("title", "source", "topic", "score", "matched_keywords")  # feedback.build_row 用到的欄位
_COMMENT = re.compile(r"<!--.*?-->")
_LINE_PREFIX = re.compile(r"^[\s>*-]*(?:#+\s*)?")

T = TypeVar("T")


def level(mark: str) -> str:
    """feedback.jsonl 裡可能有 feedback.py 收進來的 ++ / --，網頁只分兩級。"""
    return mark[:1] if mark[:1] in ("+", "-") else ""


class FileCache(Generic[T]):
    """path → load(path)。檔案的大小與修改時間都沒變就回上次的結果，不重讀、不重新解析。
    limit 有值時只留最近用到的 limit 個檔。ThreadingHTTPServer 的各執行緒共用，回傳值呼叫端不能改。
    檔案不存在時照樣拋 FileNotFoundError（load 也可以自己處理）。"""

    def __init__(self, load: Callable[[Path], T], limit: int | None = None):
        self._load = load
        self._limit = limit
        self._entries: OrderedDict[Path, tuple[tuple[int, int, int], T]] = OrderedDict()
        self._mu = threading.Lock()

    def get(self, path: Path) -> T:
        try:
            signature = _signature(path)
        except FileNotFoundError:
            with self._mu:
                self._entries.pop(path, None)
            raise
        with self._mu:
            hit = self._entries.get(path)
            if hit is not None and hit[0] == signature:
                self._entries.move_to_end(path)
                return hit[1]
        value = self._load(path)  # 不在鎖裡：讀大檔時不擋別的請求；同時兩個請求各讀一次也無妨
        try:
            unchanged = _signature(path) == signature
        except FileNotFoundError:
            unchanged = False
        if not unchanged:  # 讀的期間檔案被換掉：這次的結果照用，但不存（簽章與內容可能對不上）
            return value
        with self._mu:
            self._entries[path] = (signature, value)
            self._entries.move_to_end(path)
            while self._limit is not None and len(self._entries) > self._limit:
                self._entries.popitem(last=False)
        return value


def _signature(path: Path) -> tuple[int, int, int]:
    """大小、修改時間、inode：atomic.write_text 換檔時 inode 一定變，同大小、時間戳粒度內的覆寫也認得出來。"""
    st = path.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


@dataclass(frozen=True)
class Digest:
    """一份晨報給列表與搜尋用的摘要：頭條，與每行去掉 Markdown 與註解後的純文字（含 casefold 版本）。"""
    headline: str
    lines: tuple[tuple[str, str], ...]


def _digest(path: Path) -> Digest:
    text = path.read_text(encoding="utf-8")
    plain = (report_data.plain(_LINE_PREFIX.sub("", _COMMENT.sub("", line))) for line in text.splitlines())
    return Digest(report_data.extract_headline(text), tuple((p, p.casefold()) for p in plain))


def _parse(path: Path) -> tuple[dict, frozenset[str]] | ValueError:
    """(解析好的晨報, 裡面的 uid)；格式不符時回傳 ValueError（也快取起來，檔案沒改就不重試）。"""
    try:
        report = report_data.parse_report(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return exc
    return report, frozenset(report_data.uids_of(report))


def _feedback_rows(path: Path) -> dict[str, dict]:
    with lock.locked(path):
        return feedback.read_feedback(path)


def _curated_index(path: Path) -> dict[str, dict]:
    """uid → 回饋紀錄要的欄位（不留原文，整份 curated 有數百 KB）。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # 壞掉的檔案：檔案沒變就一直是壞的，可以快取
        return {}
    # 讀檔失敗（OSError）往外拋，不要把暫時的錯誤當成「沒有資料」存起來；_curated_item 接住
    found = [*payload.get("items", []), *payload.get("near_misses", [])]  # writer 可能撈回 near_misses
    index: dict[str, dict] = {}
    for item in found:
        if isinstance(item, dict) and isinstance(item.get("uid"), str):
            index.setdefault(item["uid"], {k: item[k] for k in CURATED_FIELDS if k in item})
    return index


class Reports:
    def __init__(self, root: Path, today: Callable[[], str] | None = None):
        self.root = root
        self.today = today or (lambda: shared_today(root))
        self.feedback_path = root / "state" / "feedback.jsonl"
        self.flags_path = root / "state" / "kg_flags.jsonl"
        self._uid_index: tuple[tuple, dict[str, str]] = ((), {})
        self._digests: FileCache[Digest] = FileCache(_digest)
        self._parsed: FileCache[tuple[dict, frozenset[str]] | ValueError] = FileCache(_parse, PARSED_CACHE)
        self._feedback: FileCache[dict[str, dict]] = FileCache(_feedback_rows)
        self._flags: FileCache[dict[str, str]] = FileCache(kg_flags.read)
        self._curated: FileCache[dict[str, dict]] = FileCache(_curated_index, PARSED_CACHE)

    def report_dates(self) -> list[str]:
        """reports/*.md 的日期，新到舊。"""
        stems = (p.stem for p in (self.root / "reports").glob("*.md"))
        return sorted((s for s in stems if DATE_RE.fullmatch(s)), reverse=True)

    def _report_path(self, date: str) -> Path:
        return self.root / "reports" / f"{date}.md"

    def uid_dates(self) -> dict[str, str]:
        """uid → 收錄它的最新一份晨報日期。報告檔沒變（檔名、大小、修改時間）就用上次的結果。"""
        dates = self.report_dates()
        stats = []
        for date in dates:
            try:
                stats.append((date, *_signature(self._report_path(date))))
            except OSError:
                continue
        signature = tuple(stats)
        cached_sig, cached = self._uid_index
        if signature == cached_sig:
            return cached
        index: dict[str, str] = {}
        for date, *_ in stats:  # 新到舊：先寫進來的就是最新的
            try:
                text = self._report_path(date).read_text(encoding="utf-8")
            except OSError:
                continue
            for uid in report_data.extract_mark_uids(text):
                index.setdefault(uid, date)
        self._uid_index = (signature, index)
        return index

    def _feedback_rows(self) -> dict[str, dict]:
        try:
            return self._feedback.get(self.feedback_path)
        except FileNotFoundError:
            return {}

    def flags(self) -> dict[str, str]:
        """uid → wrong／trusted（state/kg_flags.jsonl；取消的不在裡面）。"""
        try:
            return self._flags.get(self.flags_path)
        except FileNotFoundError:
            return {}

    def marks(self) -> dict[str, str]:
        """uid → 目前的標記（'+' / '-'；沒標或已取消的不在裡面）。"""
        return {uid: m for uid, row in self._feedback_rows().items() if (m := level(row.get("mark", "")))}

    def report_payload(self, date: str) -> dict:
        try:
            parsed = self._parsed.get(self._report_path(date))
        except FileNotFoundError:
            raise NotFound(date) from None
        if isinstance(parsed, ValueError):
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, f"報告格式不符：{parsed}")
        report, shown = parsed
        dates = self.report_dates()  # 新到舊
        i = dates.index(date)
        flags = self.flags()
        return {"date": date, "report": report, "marks": {u: m for u, m in self.marks().items() if u in shown},
                "flags": {u: f for u, f in flags.items() if u in shown and f == "wrong"},
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
            # 今天的還沒產出：附上最新一份，首頁直接顯示，不用再點一次
            payload = {"date": date, "report": None, "marks": {}, "flags": {}}
            if latest:
                payload["fallback"] = self.report_payload(latest[0])
        return {**payload, "latest": latest[0] if latest else None, "run": self.run_record(date)}

    def _digest(self, date: str) -> Digest | None:
        try:
            return self._digests.get(self._report_path(date))
        except FileNotFoundError:  # 列出日期之後才被刪掉
            return None

    def list_payload(self) -> dict:
        return {"reports": [{"date": date, "headline": digest.headline} for date in self.report_dates()
                            if (digest := self._digest(date)) is not None]}

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
                digest = self._digest(date)
                for text, folded in digest.lines if digest else ():
                    if all(t in folded for t in terms):
                        results.append({"date": date, "text": text})
                        if len(results) > SEARCH_LIMIT:
                            return {"q": q, "results": results[:SEARCH_LIMIT], "truncated": True}
        return {"q": q, "results": results, "truncated": False}

    def feedback_stats(self) -> dict:
        """newsletter-feedback --stats 的網頁版：依來源／主題／關鍵字的正負面則數，則數多的在前。"""
        rows = self._feedback_rows()
        groups = {group: [{"name": name, "pos": pos, "neg": neg}
                          for name, (pos, neg) in sorted(counts.items(), key=lambda kv: (-sum(kv[1]), kv[0]))]
                  for group, counts in feedback.stats(rows).items()}
        return {"total": sum(1 for r in rows.values() if r.get("mark") in feedback.VALID_MARKS), "groups": groups}

    def _find_report(self, uid: str) -> str | None:
        """含有這個 uid 的最新一份報告（日期）。"""
        return self.uid_dates().get(uid)

    def _curated_item(self, date: str, uid: str) -> dict:
        try:
            return self._curated.get(self.root / "data" / "curated" / f"{date}.json").get(uid, {})
        except OSError:
            return {}

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

    def record_flag(self, uid: str, flag: str) -> dict:
        """事實判定：'wrong'＝事實有誤、'trusted'＝確認可信（不被自動判定失效），'' ＝ 取消。只收報告裡出現過的 uid。"""
        if flag not in ("wrong", "trusted", ""):
            raise ApiError(HTTPStatus.BAD_REQUEST, "flag 只能是 wrong、trusted 或空字串（取消）")
        if not UID_RE.fullmatch(uid):
            raise ApiError(HTTPStatus.BAD_REQUEST, "uid 格式不符")
        if self._find_report(uid) is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "任何一份報告裡都找不到這個 uid")
        return kg_flags.append(uid, flag, self.flags_path)
