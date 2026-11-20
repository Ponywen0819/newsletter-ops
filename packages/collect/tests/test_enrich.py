from newsletter_collect.enrich import (
    dropped_note, enrich, lacks_detail, settle, targets, text_of, unwall, urls_for, weight,
)

FAST = {"request": {"delay_seconds": 0}}  # 測試不等同網域的間隔
LONG = "A sentence that is long enough to count as body text. " * 5
PAGE = ("<html><head><title>x</title><script>var a=1</script></head><body><nav>Menu Home</nav>"
        "<p>Main &amp; story</p><footer>Copyright</footer></body></html>")
ARTICLE = f"<article><p>{LONG}</p><p>{LONG}</p></article>"
# 2026-11-13 起 Nature 對非瀏覽器回的頁面（存進 fulltext 的就是這段）
NATURE = ("<body><p>JavaScript is disabled in your browser. Please enable JavaScript to proceed. A required part of this "
          "site couldn’t load. This may be due to a browser extension, network issues, or browser settings. Please "
          "check your connection, disable any ad blockers, or try using a different browser.</p></body>")
# 經濟日報的付費牆：開頭一段，接著是訂閱按鈕與一長串推薦文章
LEAD = "台積電日前董事會核准以不超過5億美元的資金設立一投資公司，以進行半導體產業及相關生態系的策略性投資。" * 4
UDN = (f"<main>原本--> 扶植下一個輝達？ {LEAD} 本文共1070字 輝達 ', ' ', ' ', ' ', ' 00:00 經濟日報 "
       "展開全文 免費看獨家內容 登入會員 立即訂閱 免費註冊，立即解鎖全文 猜你喜歡 台積電季配息升到9元</main>")


def it(uid, topic="t", rank=1.0, **kw):
    return {"uid": uid, "topic": topic, "rank": rank, "source": "Site", "url": f"https://s.example/{uid}",
            "title": f"Title {uid}", **kw}


def items():
    return [it(f"n{n}", rank=10 - n) for n in range(5)] + [
        it("s1", rank=0.0, origin="search"),
        it("p_main", "ai-research", 9, source="arXiv cs.CL", url="http://arxiv.org/abs/1", venue={"status": "main"}),
        *[it(f"p{n}", "ai-research", 8 - n, source="arXiv cs.LG", url=f"http://arxiv.org/abs/{n}v1") for n in range(4)],
    ]


def fake(pages: dict):
    def get(url: str) -> str:
        if url not in pages:
            raise RuntimeError(f"GET failed: {url}: HTTP Error 403: Forbidden")
        return pages[url]
    return get


def test_targets():
    got = items()
    # 新聞全部都抓（不再只抓每主題前 3 則：沒抓的那則被選成頭條，報告就只剩標題）
    assert {i["uid"] for i in targets(got)} == {"n0", "n1", "n2", "n3", "n4", "s1", "p0", "p1", "p2"}
    assert urls_for(got[-1]) == ["http://arxiv.org/html/3v1", "http://arxiv.org/abs/3v1"]


def test_text_of():
    assert text_of(PAGE) == "Main & story"
    assert text_of("<body><div>Subscribe now</div><article><h1>T</h1><p>Body</p></article></body>") == "T Body"
    # 段落夠多：只接段落，丟掉按鈕與太短的圖說
    assert text_of(f"<article><div>Share Copy link</div><p>Image credit</p><p>{LONG}</p><p>{LONG}</p></article>") == \
        f"{LONG.strip()} {LONG.strip()}"
    paper = f"<article><div class='ltx_authors'>MIT CSAIL</div><p>{LONG}</p></article>"
    assert text_of(paper, paragraphs=False).startswith("MIT CSAIL")
    assert "', '" not in text_of(UDN)  # 外漏的 JS 陣列清掉


def test_unwall():
    assert unwall(text_of(NATURE)) == ("", "被擋：頁面要求開 JavaScript、關掉廣告攔截或驗證身分")
    text, why = unwall(text_of(UDN))
    assert why == "付費牆" and text.endswith("經濟日報") and "猜你喜歡" not in text and LEAD in text
    # BBC 影片頁：播放器要 JavaScript，但說明文字是內文
    bbc = "To play this video you need to enable JavaScript in your browser. " + LONG
    assert unwall(bbc) == (bbc, None)
    # 長文裡提到廣告攔截（報導本身）不算擋板
    story = LONG * 8 + " Publishers ask readers to disable any ad blockers."
    assert unwall(story) == (story, None)


def test_lacks_detail():
    assert weight("abc") == 3 and weight("台積電") == 6
    # 摘要只是標題（2026-11-19 的 Anthropic IPO）
    ipo = it("a", title="Anthropic prices initial public offering", summary="Anthropic prices initial public offering")
    assert lacks_detail(ipo) == "摘要太短，原文也沒有內容"
    # 原文被擋、摘要一句話（OpenAI、WSJ）
    blocked = it("o", summary="Introducing GPT-6.2, with better coding and computer use.", fulltext_error="HTTP 403")
    assert lacks_detail(blocked) == "摘要太短，原文抓不到（HTTP 403）"
    assert lacks_detail({**blocked, "summary": LONG * 2}) is None  # 摘要本身夠長（IEEE Spectrum 的 RSS 有全文）
    assert lacks_detail({**blocked, "fulltext": LONG}) is None
    assert lacks_detail(it("z", summary="台積電" * 51)) is None  # 中文一字算兩個
    assert lacks_detail(it("z", summary="台積電" * 49)) is not None
    assert lacks_detail(it("p", source="arXiv cs.CV", summary="")) is None  # 論文的摘要就是內容
    assert lacks_detail(blocked, {"min_summary": 10}) is None  # 門檻可由 config.detail 調


def test_enrich():
    pages = {"https://s.example/n0": PAGE, "http://arxiv.org/abs/0v1": "<body>Authors: MIT</body>",
             "https://s.example/n1": NATURE, "https://s.example/n2": UDN}
    got = items()
    got[4]["fulltext"] = "already"  # 抓過的不重抓
    enrich(got, FAST, fake(pages))
    by = {i["uid"]: i for i in got}
    assert by["n0"]["fulltext"] == "Main & story" and "fulltext_error" not in by["n0"]
    assert by["p0"]["fulltext"] == "Authors: MIT"  # 沒有 HTML 版就退回 abs 頁
    assert "403" in by["n3"]["fulltext_error"] and by["n4"]["fulltext"] == "already"
    assert by["n1"]["fulltext_error"].startswith("被擋") and "fulltext" not in by["n1"]
    assert "猜你喜歡" not in by["n2"]["fulltext"] and by["n2"]["fulltext_note"] == "付費牆：只有開頭"
    # 牆前面太短就當抓不到
    short = [it("u", url="https://s.example/u")]
    enrich(short, FAST, fake({"https://s.example/u": "<main>一句話。 展開全文 立即訂閱</main>"}))
    assert short[0]["fulltext_error"] == "付費牆"


def test_settle():
    ok = {f"https://s.example/{u}": ARTICLE for u in ("a1", "a3", "r2", "b1")}
    batch = [it("a1", "a", 9), it("a2", "a", 8, summary="short"), it("a3", "a", 7),
             it("b1", "b", 9), it("b2", "b", 8), it("s", "b", 1, origin="search")]
    reserve = [it("r1", "a", 6), it("r2", "a", 5), it("r3", "a", 4), it("rb", "c", 9)]
    kept, dropped = settle(batch, reserve, FAST, fake(ok))
    # a2 被擋 → r1 也被擋 → r2 補上；b2 沒有同主題候補；搜尋補來的不補位
    assert [i["uid"] for i in kept] == ["a1", "b1", "a3", "r2"]
    assert {i["uid"] for i in dropped} == {"a2", "b2", "s"}
    assert all(i["dropped_reason"].startswith("摘要太短") for i in dropped)
    assert "fulltext" not in reserve[2] and "fulltext_error" not in reserve[2]  # 用不到的候補不抓
    assert dropped_note(dropped) == "3 則原文被擋或抓不到、摘要又太短，沒有收錄：Site 3"
    assert dropped_note([]) is None
