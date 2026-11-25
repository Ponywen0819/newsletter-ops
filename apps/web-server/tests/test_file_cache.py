"""reports.FileCache：檔案沒變就不重讀；變了、被刪了都要看得出來。"""
import os

import pytest

from newsletter_web_server.reports import FileCache, Reports


def test_file_cache(tmp_path):
    loads: list[str] = []

    def load(path):
        loads.append(path.name)
        return path.read_text(encoding="utf-8")

    cache = FileCache(load, limit=2)
    a, b, c = (tmp_path / n for n in ("a", "b", "c"))
    for path in (a, b, c):
        path.write_text(path.name, encoding="utf-8")

    assert cache.get(a) == "a" and cache.get(a) == "a" and loads == ["a"]  # 第二次不重讀
    a.write_text("a2", encoding="utf-8")
    assert cache.get(a) == "a2" and loads == ["a", "a"]  # 內容變了（大小不同）

    st = a.stat()
    a.write_text("a3", encoding="utf-8")  # 同樣大小：靠修改時間分辨
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert cache.get(a) == "a3"

    cache.get(b)
    cache.get(c)  # 超過 limit：最久沒用的 a 被丟掉
    loads.clear()
    cache.get(a)
    assert loads == ["a"]

    with pytest.raises(FileNotFoundError):
        cache.get(tmp_path / "missing")


def test_reports_cache_sees_changes(tmp_path):
    """快取的列表、搜尋、標記都要跟上檔案的變動。"""
    (tmp_path / "reports").mkdir()
    report = tmp_path / "reports" / "2026-01-02.md"
    report.write_text("# 每日晨間簡報 2026-01-02\n\n> **今日頭條：** 第一版\n", encoding="utf-8")
    reports = Reports(tmp_path, today=lambda: "2026-01-02")

    assert reports.list_payload() == {"reports": [{"date": "2026-01-02", "headline": "第一版"}]}
    assert [r["text"] for r in reports.search("第一版")["results"]] == ["今日頭條： 第一版"]
    report.write_text("# 每日晨間簡報 2026-01-02\n\n> **今日頭條：** 改過的第二版\n", encoding="utf-8")
    assert reports.list_payload()["reports"][0]["headline"] == "改過的第二版"
    assert reports.search("第一版")["results"] == []

    assert reports.marks() == {} and reports.flags() == {}  # 檔案還不存在
    (tmp_path / "state").mkdir()
    reports.feedback_path.write_text('{"uid": "aaaaaaaaaaaaaaaa", "mark": "+"}\n', encoding="utf-8")
    assert reports.marks() == {"aaaaaaaaaaaaaaaa": "+"}
    with reports.feedback_path.open("a", encoding="utf-8") as fh:
        fh.write('{"uid": "aaaaaaaaaaaaaaaa", "mark": ""}\n')
    assert reports.marks() == {}

    report.unlink()
    assert reports.list_payload() == {"reports": []}
