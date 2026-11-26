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


def test_file_cache_atomic_replace_same_size(tmp_path):
    """atomic.write_text 式的換檔（新檔、os.replace）大小相同、時間戳又在同一個粒度內：靠 inode 認得出來。"""
    path = tmp_path / "a.json"
    path.write_text("v1", encoding="utf-8")
    cache = FileCache(lambda p: p.read_text(encoding="utf-8"))
    assert cache.get(path) == "v1"
    st = path.stat()
    fresh = tmp_path / "a.json.tmp"
    fresh.write_text("v2", encoding="utf-8")
    os.utime(fresh, ns=(st.st_atime_ns, st.st_mtime_ns))  # 刻意做成同樣的大小與修改時間
    os.replace(fresh, path)
    assert cache.get(path) == "v2"


def test_file_cache_evicts_deleted(tmp_path):
    path = tmp_path / "a"
    path.write_text("a", encoding="utf-8")
    cache = FileCache(lambda p: p.read_text(encoding="utf-8"))
    cache.get(path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        cache.get(path)
    assert path not in cache._entries  # 刪掉的檔案不留在記憶體


def test_curated_read_error_not_cached(tmp_path, monkeypatch):
    """讀 curated 暫時失敗（OSError）不能把「沒有資料」快取起來；檔案沒變、下次讀得到就要拿到。"""
    from pathlib import Path

    (tmp_path / "data" / "curated").mkdir(parents=True)
    curated = tmp_path / "data" / "curated" / "2026-01-02.json"
    curated.write_text('{"items": [{"uid": "aaaaaaaaaaaaaaaa", "title": "T", "fulltext": "很長"}]}', encoding="utf-8")
    reports = Reports(tmp_path, today=lambda: "2026-01-02")
    real = Path.read_text
    fail = {"on": True}

    def flaky(self, *a, **k):
        if fail["on"] and self == curated:
            raise PermissionError("busy")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    assert reports._curated_item("2026-01-02", "aaaaaaaaaaaaaaaa") == {}
    fail["on"] = False
    assert reports._curated_item("2026-01-02", "aaaaaaaaaaaaaaaa") == {"title": "T"}  # 不留原文


def test_file_cache_replaced_while_loading(tmp_path):
    path = tmp_path / "a"
    path.write_text("v1", encoding="utf-8")

    def load(p):
        text = p.read_text(encoding="utf-8")
        if text == "v1":  # 讀完、存進快取之前，檔案被換成新版
            fresh = tmp_path / "a.tmp"
            fresh.write_text("v2", encoding="utf-8")
            os.replace(fresh, p)
        return text

    cache = FileCache(load)
    assert cache.get(path) == "v1"
    assert path not in cache._entries  # 簽章與內容可能對不上：不存
    assert cache.get(path) == "v2"


def test_file_cache_recheck_oserror_still_returns(tmp_path, monkeypatch):
    """讀完後確認簽章時遇到 PermissionError（Windows 換檔的一瞬間）：已讀到的照樣回傳，只是不存。"""
    from newsletter_web_server import reports as mod

    path = tmp_path / "a"
    path.write_text("v1", encoding="utf-8")
    real = mod._signature
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError("busy")
        return real(p)

    monkeypatch.setattr(mod, "_signature", flaky)
    cache = FileCache(lambda p: p.read_text(encoding="utf-8"))
    assert cache.get(path) == "v1"
    assert path not in cache._entries
