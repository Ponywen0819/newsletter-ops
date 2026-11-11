from zoneinfo import ZoneInfo

import pytest

from newsletter_shared import DEFAULT_TZ, tz
from newsletter_shared.report_data import check, parse_inline, parse_report, uids_of

UID_A, UID_B, UID_C, UID_D = "0123456789abcdef", "0123456789abcdee", "1111111111111111", "2222222222222222"
HEAD = "> **今日頭條：** x\n<!-- subject: s -->\n"
MD = f"""# 每日晨間簡報 2026-09-28

<!-- subject: 測試頭條 -->

> **今日頭條：** 某事發生，見 [來源](https://a.example/x?a=1&b=2)。

## 科技與 [AI](https://a.example/h2)

**[重點 <標題>](https://a.example/1)**

- 發生什麼：A
  - 細節
    - 更細
  - 細節二
- 背景：B
<!-- mark:    uid={UID_A} -->

## 其餘收錄

- **[其他](https://a.example/2)** — 半句
<!-- mark:    uid={UID_B} -->

<!-- mark:    uid={UID_C} -->
- **[其他二](https://a.example/1)** — 重複連結
<!-- 一般註解不打斷清單 -->
<!-- mark:    uid={UID_D} -->
"""


def flat(nodes: list[dict]) -> str:
    out = []
    for n in nodes:
        kind = n["type"]
        out.append(n["text"] if kind == "text" else f"`{n['text']}`" if kind == "code"
                   else f"<b>{flat(n['children'])}</b>" if kind == "strong"
                   else f"<a {n['href']}>{flat(n['children'])}</a>")
    return "".join(out)


def test_tz(tmp_path):
    """config.json 的 timezone，沒有檔、沒寫、壞掉都退回預設。"""
    assert tz(tmp_path) == ZoneInfo(DEFAULT_TZ)
    cfg = tmp_path / "config" / "config.json"
    cfg.parent.mkdir()
    cfg.write_text('{"timezone": "UTC"}', encoding="utf-8")
    assert tz(tmp_path) == ZoneInfo("UTC")
    for broken in ("{", "[]", "{}"):
        cfg.write_text(broken, encoding="utf-8")
        assert tz(tmp_path) == ZoneInfo(DEFAULT_TZ), broken


def test_inline():
    """連結、粗體、行內碼、跳脫；網址的 & 保持原樣（跳脫交給前端框架）。"""
    got: list[tuple[str, str]] = []
    assert flat(parse_inline("**[重點 <標題>](https://a.example/1?a=1&b=2)** — 半句 `x` 與 **粗**", got)) == \
        "<b><a https://a.example/1?a=1&b=2>重點 <標題></a></b> — 半句 `x` 與 <b>粗</b>"
    assert got == [("重點 <標題>", "https://a.example/1?a=1&b=2")]
    assert flat(parse_inline("[**粗連結**](https://a.example/2)")) == "<a https://a.example/2><b>粗連結</b></a>"
    assert flat(parse_inline("`[不是連結](https://a.example/3)`")) == "`[不是連結](https://a.example/3)`"
    assert flat(parse_inline("純文字   與 ** 未成對")) == "純文字   與 ** 未成對"
    assert parse_inline("") == []


def test_report():
    report = parse_report(MD)
    assert report["subject"] == "測試頭條" and report["headline"] == "某事發生，見 來源。"
    assert report["title"] == "每日晨間簡報" and report["blocks"][0] == {"type": "title", "title": "每日晨間簡報", "date": "2026-09-28"}
    assert [b["type"] for b in report["blocks"]] == ["title", "callout", "heading", "paragraph", "list", "heading",
                                                       "list", "mark", "list"]
    first = report["blocks"][4]["items"]
    # 巢狀：細節在「發生什麼」底下，mark 掛在最外層的最後一個項目（背景）
    assert [flat(i["inline"]) for i in first] == ["發生什麼：A", "背景：B"]
    assert [flat(i["inline"]) for i in first[0]["children"]] == ["細節", "細節二"]
    assert [flat(i["inline"]) for i in first[0]["children"][0]["children"]] == ["更細"]
    assert "uids" not in first[0] and first[1]["uids"] == [UID_A]
    assert report["blocks"][6]["items"][0]["uids"] == [UID_B]
    assert report["blocks"][7] == {"type": "mark", "uid": UID_C, "votable": False}
    assert report["blocks"][8]["items"][0]["uids"] == [UID_D]
    assert uids_of(report) == [UID_A, UID_B, UID_C, UID_D]
    # 資料來源：依出現順序、網址去重、標題去掉 **
    assert [s["url"] for s in report["sources"]] == ["https://a.example/x?a=1&b=2", "https://a.example/h2",
                                                      "https://a.example/1", "https://a.example/2"]
    # votable：主要新聞（標題段落＋清單）才有回饋；緊接在 ## 後面的整張單行清單沒有，之後的沿用前一張
    assert [b.get("votable") for b in report["blocks"]] == [None, None, None, None, True, None, False, False, False]


def test_odd_indent():
    """縮排跳兩層、開頭就縮排：一次只往下一層，不會產生沒有 <li> 的空層。"""
    odd = parse_report(HEAD + "    - 縮排開頭\n        - 跳三層\n")
    assert [flat(i["inline"]) for i in odd["blocks"][1]["items"]] == ["縮排開頭"]
    assert [flat(i["inline"]) for i in odd["blocks"][1]["items"][0]["children"]] == ["跳三層"]


@pytest.mark.parametrize(("bad", "why"), [("# x\n", "頭條"), ("> **今日頭條：** y\n", "subject")])
def test_bad_format(bad, why):
    with pytest.raises(ValueError, match=why):
        parse_report(bad)


@pytest.mark.parametrize(("body", "want"), [
    ("**[標題](https://a.example/1)**\n- a\n\n- b\n<!-- mark:    uid=%s -->\n", [True, True]),
    ("## 其餘收錄\n- a\n<!-- mark:    uid=%s -->\n", [False]),
    ("## X\n**[標題](https://a.example/1)**\n\n<!-- mark:    uid=%s -->\n", [True]),
    ("- a\n<!-- mark:    uid=%s -->\n", [False]),
])
def test_votable(body, want):
    assert [b["votable"] for b in parse_report(HEAD + body % UID_A)["blocks"] if "votable" in b] == want


def test_merged_marks():
    """併了多篇文章：連著好幾行 mark，掛在同一個項目的 uids 上（每篇一個）。"""
    merged = parse_report(HEAD + "**[標題](https://a.example/1)**\n- a\n<!-- mark:    uid=%s -->\n<!-- mark:    uid=%s -->\n" % (UID_A, UID_B))
    assert merged["blocks"][-1]["items"][-1]["uids"] == [UID_A, UID_B] and merged["blocks"][-1]["votable"] is True


@pytest.mark.parametrize(("name", "code", "msg"), [
    ("good", 0, ""), ("no_subject", 1, "subject"), ("no_headline", 1, "今日頭條"), ("missing", 1, "找不到"),
])
def test_check(tmp_path, capsys, name, code, msg):
    (tmp_path / "reports").mkdir()
    for n, text in {"good": HEAD, "no_subject": HEAD.replace("<!-- subject: s -->\n", ""),
                    "no_headline": HEAD.replace("> **今日頭條：** x\n", "")}.items():
        (tmp_path / "reports" / f"{n}.md").write_text(text, encoding="utf-8")
    assert check([name], tmp_path) == code
    err = capsys.readouterr().err
    assert msg in err if msg else err == ""
