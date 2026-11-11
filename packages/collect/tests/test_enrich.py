from newsletter_collect.enrich import enrich, targets, text_of, urls_for

LONG = "A sentence that is long enough to count as body text. " * 5
PAGE = ("<html><head><title>x</title><script>var a=1</script></head><body><nav>Menu Home</nav>"
        "<p>Main &amp; story</p><footer>Copyright</footer></body></html>")


def it(uid, topic="t", rank=1.0, **kw):
    return {"uid": uid, "topic": topic, "rank": rank, "source": "Site", "url": f"https://s.example/{uid}", **kw}


def items():
    return [it(f"n{n}", rank=10 - n) for n in range(5)] + [
        it("s1", rank=0.0, origin="search"),
        it("p_main", "ai-research", 9, source="arXiv cs.CL", url="http://arxiv.org/abs/1", venue={"status": "main"}),
        *[it(f"p{n}", "ai-research", 8 - n, source="arXiv cs.LG", url=f"http://arxiv.org/abs/{n}v1") for n in range(4)],
    ]


def test_targets():
    got = items()
    assert {i["uid"] for i in targets(got)} == {"n0", "n1", "n2", "s1", "p0", "p1", "p2"}
    assert urls_for(got[-1]) == ["http://arxiv.org/html/3v1", "http://arxiv.org/abs/3v1"]


def test_text_of():
    assert text_of(PAGE) == "Main & story"
    assert text_of("<body><div>Subscribe now</div><article><h1>T</h1><p>Body</p></article></body>") == "T Body"
    # 段落夠多：只接段落，丟掉按鈕與太短的圖說
    assert text_of(f"<article><div>Share Copy link</div><p>Image credit</p><p>{LONG}</p><p>{LONG}</p></article>") == \
        f"{LONG.strip()} {LONG.strip()}"
    paper = f"<article><div class='ltx_authors'>MIT CSAIL</div><p>{LONG}</p></article>"
    assert text_of(paper, paragraphs=False).startswith("MIT CSAIL")


def test_enrich():
    pages = {"https://s.example/n0": PAGE, "http://arxiv.org/abs/0v1": "<body>Authors: MIT</body>"}

    def get(url: str) -> str:
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]

    got = items()
    enrich(got, {}, get)
    by = {i["uid"]: i for i in got}
    assert by["n0"]["fulltext"] == "Main & story" and "fulltext_error" not in by["n0"]
    assert by["p0"]["fulltext"] == "Authors: MIT"  # 沒有 HTML 版就退回 abs 頁
    assert "404" in by["n1"]["fulltext_error"] and "fulltext" not in by["n3"] and "fulltext_error" not in by["n3"]
