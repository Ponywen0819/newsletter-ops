#!/usr/bin/env python3
"""報告資料層：reports/<date>.md → 結構化資料（給 web 前端用）。

用法：python3 src/report_data.py [--selftest]

晨報只有這一個解析器：web 前端（web/，Vite + React）拿這裡解析好的結構自己排版，
email（render_email.py）也吃同一份結構、排成 inline-CSS HTML，所以格式（SKILL.md 規定的 Markdown 子集）或內容規則
（哪些條目可以投票）有改動時只改這裡，兩邊版型各自負責呈現。
{"title", "date", "subject", "headline", "blocks": [...], "sources": [...]}。
認的 Markdown 子集：# / ## / > / - / 兩格縮排的 - / 粗體 / 連結 / 行內碼 / <!-- -->。

blocks 的種類（依出現順序）：
  {"type": "title", "title", "date"}        # date 是標題尾端的 YYYY-MM-DD，沒有就是 None
  {"type": "heading", "inline": [...]}      # ## 段落標題
  {"type": "callout", "inline": [...]}      # > 引言（今日頭條）
  {"type": "paragraph", "inline": [...]}
  {"type": "list", "items": [item, ...], "votable"}   # item: {"inline", "children": [item...]?, "uids": [uid...]?}
  {"type": "mark", "uid", "votable"}        # 沒掛在任何清單項目底下的 mark 註解
inline 是 [{"type": "text", "text"} | {"type": "strong", "children"} | {"type": "link", "href", "children"}
          | {"type": "code", "text"}]。

votable：這張清單（或獨立 mark）要不要放有用／沒用。只有主要新聞——標題段落＋清單——才放；
緊接在 ## 後面的整張單行清單（「其餘收錄」）不放。被空行切開的同一則新聞（清單接在清單後面）沿用前一張的結果。
網頁與 email 都只讀這個旗標，規則不要在各自的版型裡再寫一次。

mark 註解（`<!-- mark: uid=... -->`）掛在它前面那份清單最外層的最後一個項目的 uids 上
（按鈕在該項目的巢狀子項目之後）。一則新聞併了多篇文章時會連著好幾行 mark，uids 就有好幾個（每篇一個）；
清單被空行或其他內容打斷後，mark 就是獨立的 block。
格式不符時 raise ValueError（找不到今日頭條或 subject 註解）。只用 stdlib。
"""
from __future__ import annotations

import re
import sys

from newsletter_shared.feedback import MARK_RE

LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
COMMENT = re.compile(r"^\s*<!--(.*?)-->\s*$")
BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`]+)`")
BULLET = re.compile(r"^( *)[-*] (.+)$")
TITLE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
# 連結先換成佔位符，粗體才能把整個連結包起來（`**[標題](url)**`）；用私人使用區字元，一般文字不會出現
SLOT_OPEN, SLOT_CLOSE = "", ""
SLOT = re.compile(f"{SLOT_OPEN}(\\d+){SLOT_CLOSE}")


def plain(text: str) -> str:
    """去掉粗體、行內碼、連結語法，只留文字（今日頭條的純文字版）。"""
    return re.sub(r"\*\*|`", "", LINK.sub(r"\1", text)).strip()


def votable_after(blocks: list[dict], standalone_mark: bool = False) -> bool:
    """接在 blocks 後面的新清單（或獨立 mark）要不要放回饋，規則見檔頭。"""
    prev = next((b for b in reversed(blocks) if b["type"] != "mark"), None)
    if prev is not None and prev["type"] == "list":
        return prev["votable"]
    return True if standalone_mark else prev is not None and prev["type"] == "paragraph"


def parse_inline(text: str, links: list[tuple[str, str]] | None = None) -> list[dict]:
    """一行文字 → inline 節點。links 傳入的話，會把這行裡的 (標題, 網址) 依序加進去（資料來源清單用）。"""
    held: list[tuple[str, str, str]] = []  # (連結文字, 網址, 原始寫法)

    def hold(m: re.Match) -> str:
        held.append((m[1], m[2], m[0]))
        return f"{SLOT_OPEN}{len(held) - 1}{SLOT_CLOSE}"

    work = LINK.sub(hold, text.replace(SLOT_OPEN, "").replace(SLOT_CLOSE, ""))
    if links is not None:
        links.extend((label.strip("*"), url) for label, url, _ in held)

    def restore(s: str) -> str:  # 行內碼裡的連結不轉成連結，原樣還原
        return SLOT.sub(lambda m: held[int(m[1])][2], s)

    def texts(s: str) -> list[dict]:
        nodes: list[dict] = []
        pos = 0
        for m in SLOT.finditer(s):
            if m.start() > pos:
                nodes.append({"type": "text", "text": s[pos:m.start()]})
            label, url, _ = held[int(m[1])]
            nodes.append({"type": "link", "href": url, "children": bold(label)})
            pos = m.end()
        if pos < len(s):
            nodes.append({"type": "text", "text": s[pos:]})
        return nodes

    def code(s: str) -> list[dict]:
        nodes: list[dict] = []
        pos = 0
        for m in CODE.finditer(s):
            nodes += texts(s[pos:m.start()])
            nodes.append({"type": "code", "text": restore(m[1])})
            pos = m.end()
        return nodes + texts(s[pos:])

    def bold(s: str) -> list[dict]:
        nodes: list[dict] = []
        pos = 0
        for m in BOLD.finditer(s):
            nodes += code(s[pos:m.start()])
            nodes.append({"type": "strong", "children": code(m[1])})
            pos = m.end()
        return nodes + code(s[pos:])

    return bold(work)


def parse_report(markdown: str) -> dict:
    blocks: list[dict] = []
    links: list[tuple[str, str]] = []
    subject = headline = ""
    title = ""
    current: dict | None = None      # 目前開著的 list block
    stack: list[list[dict]] = []     # stack[d - 1] ＝ 第 d 層的項目清單

    for line in markdown.splitlines():
        comment = COMMENT.match(line)
        if comment:
            if comment[1].strip().startswith("subject:"):
                subject = comment[1].strip()[len("subject:"):].strip()
            mark = MARK_RE.search(line)
            if mark:
                if current is not None:
                    current["items"][-1].setdefault("uids", []).append(mark[2])
                else:
                    blocks.append({"type": "mark", "uid": mark[2], "votable": votable_after(blocks, standalone_mark=True)})
            continue  # 其餘註解不進頁面，也不打斷清單
        bullet = BULLET.match(line)
        if bullet:
            level = len(bullet[1]) // 2 + 1
            if current is None:  # 新清單一定從第一層開始，開頭就縮排也一樣
                current = {"type": "list", "items": [], "votable": votable_after(blocks)}
                blocks.append(current)
                stack = [current["items"]]
                level = 1
            level = min(level, len(stack) + 1)  # 一次只往下一層
            if level > len(stack):
                parent = stack[-1][-1]
                stack.append(parent.setdefault("children", []))
            else:
                del stack[level:]
            stack[level - 1].append({"inline": parse_inline(bullet[2], links)})
            continue
        current = None
        if not line.strip():
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            date = TITLE_DATE.search(title)
            if date:
                title = title[:date.start()].strip()
            blocks.append({"type": "title", "title": title, "date": date[0] if date else None})
        elif line.startswith("## "):
            blocks.append({"type": "heading", "inline": parse_inline(line[3:].strip(), links)})
        elif line.startswith(">"):
            text = line.lstrip("> ").strip()
            if "今日頭條" in text:
                headline = plain(re.sub(r"^\**今日頭條[：:]\**\s*", "", text))
            blocks.append({"type": "callout", "inline": parse_inline(text, links)})
        else:
            blocks.append({"type": "paragraph", "inline": parse_inline(line.strip(), links)})

    if not headline:
        raise ValueError("找不到「> **今日頭條：** …」那一行")
    if not subject:
        raise ValueError("找不到 <!-- subject: … --> 註解")

    seen: set[str] = set()
    sources = [{"title": t, "url": u} for t, u in links if not (u in seen or seen.add(u))]
    return {"title": title, "subject": subject, "headline": headline, "blocks": blocks, "sources": sources}


def uids_of(report: dict) -> list[str]:
    """報告裡所有 mark 的 uid，依出現順序。"""
    found: list[str] = []

    def walk(items: list[dict]) -> None:
        for item in items:
            found.extend(item.get("uids", []))
            walk(item.get("children", []))

    for block in report["blocks"]:
        if block["type"] == "list":
            walk(block["items"])
        elif block["type"] == "mark":
            found.append(block["uid"])
    return found


def iter_items(report: dict):
    def walk(items: list[dict]):
        for item in items:
            yield item
            yield from walk(item.get("children", []))

    for block in report["blocks"]:
        if block["type"] == "list":
            yield from walk(block["items"])


def selftest() -> None:
    # inline：連結、粗體、行內碼、跳脫；網址的 & 保持原樣（跳脫交給前端框架）
    def flat(nodes: list[dict]) -> str:
        out = []
        for n in nodes:
            kind = n["type"]
            out.append(n["text"] if kind == "text" else f"`{n['text']}`" if kind == "code"
                       else f"<b>{flat(n['children'])}</b>" if kind == "strong"
                       else f"<a {n['href']}>{flat(n['children'])}</a>")
        return "".join(out)

    got: list[tuple[str, str]] = []
    assert flat(parse_inline("**[重點 <標題>](https://a.example/1?a=1&b=2)** — 半句 `x` 與 **粗**", got)) == \
        "<b><a https://a.example/1?a=1&b=2>重點 <標題></a></b> — 半句 `x` 與 <b>粗</b>"
    assert got == [("重點 <標題>", "https://a.example/1?a=1&b=2")]
    assert flat(parse_inline("[**粗連結**](https://a.example/2)")) == "<a https://a.example/2><b>粗連結</b></a>"
    assert flat(parse_inline("`[不是連結](https://a.example/3)`")) == "`[不是連結](https://a.example/3)`"
    assert flat(parse_inline("純文字   與 ** 未成對")) == "純文字   與 ** 未成對"  # 佔位符字元不會誤判
    assert parse_inline("") == []

    uid_a, uid_b, uid_c, uid_d = "0123456789abcdef", "0123456789abcdee", "1111111111111111", "2222222222222222"
    md = f"""# 每日晨間簡報 2026-09-28

<!-- subject: 測試頭條 -->

> **今日頭條：** 某事發生，見 [來源](https://a.example/x?a=1&b=2)。

## 科技與 [AI](https://a.example/h2)

**[重點 <標題>](https://a.example/1)**

- 發生什麼：A
  - 細節
    - 更細
  - 細節二
- 背景：B
<!-- mark:    uid={uid_a} -->

## 其餘收錄

- **[其他](https://a.example/2)** — 半句
<!-- mark:    uid={uid_b} -->

<!-- mark:    uid={uid_c} -->
- **[其他二](https://a.example/1)** — 重複連結
<!-- 一般註解不打斷清單 -->
<!-- mark:    uid={uid_d} -->
"""
    report = parse_report(md)
    assert report["subject"] == "測試頭條" and report["headline"] == "某事發生，見 來源。", report
    assert report["title"] == "每日晨間簡報" and report["blocks"][0] == {"type": "title", "title": "每日晨間簡報", "date": "2026-09-28"}
    assert [b["type"] for b in report["blocks"]] == ["title", "callout", "heading", "paragraph", "list", "heading",
                                                       "list", "mark", "list"], [b["type"] for b in report["blocks"]]
    first = report["blocks"][4]["items"]
    # 巢狀：細節在「發生什麼」底下，mark 掛在最外層的最後一個項目（背景）
    assert [flat(i["inline"]) for i in first] == ["發生什麼：A", "背景：B"]
    assert [flat(i["inline"]) for i in first[0]["children"]] == ["細節", "細節二"]
    assert [flat(i["inline"]) for i in first[0]["children"][0]["children"]] == ["更細"]
    assert "uids" not in first[0] and first[1]["uids"] == [uid_a]
    assert report["blocks"][6]["items"][0]["uids"] == [uid_b]
    assert report["blocks"][7] == {"type": "mark", "uid": uid_c, "votable": False}  # 空行打斷清單後，mark 是獨立 block
    assert report["blocks"][8]["items"][0]["uids"] == [uid_d]
    assert uids_of(report) == [uid_a, uid_b, uid_c, uid_d]
    # 資料來源：依出現順序、網址去重、標題去掉 **
    assert [s["url"] for s in report["sources"]] == ["https://a.example/x?a=1&b=2", "https://a.example/h2",
                                                      "https://a.example/1", "https://a.example/2"], report["sources"]
    # 縮排跳兩層、開頭就縮排：一次只往下一層，不會產生沒有 <li> 的空層
    odd = parse_report("> **今日頭條：** x\n<!-- subject: s -->\n    - 縮排開頭\n        - 跳三層\n")
    assert [flat(i["inline"]) for i in odd["blocks"][1]["items"]] == ["縮排開頭"]
    assert [flat(i["inline"]) for i in odd["blocks"][1]["items"][0]["children"]] == ["跳三層"]
    for bad, why in (("# x\n", "頭條"), ("> **今日頭條：** y\n", "subject")):
        try:
            parse_report(bad)
            raise AssertionError("格式不符應該報錯")
        except ValueError as exc:
            assert why in str(exc), exc

    # votable：主要新聞（標題段落＋清單）才有回饋；緊接在 ## 後面的整張單行清單沒有，
    # 它之後被空行切開的清單與獨立 mark 沿用前一張的結果
    assert [b.get("votable") for b in report["blocks"]] == [None, None, None, None, True, None, False, False, False], report["blocks"]
    cases = {
        "story_blank": "**[標題](https://a.example/1)**\n- a\n\n- b\n<!-- mark:    uid=%s -->\n",   # 同一則被空行切成兩張清單：沿用
        "list_after_heading": "## 其餘收錄\n- a\n<!-- mark:    uid=%s -->\n",
        "paragraph_only_mark": "## X\n**[標題](https://a.example/1)**\n\n<!-- mark:    uid=%s -->\n",  # 沒有清單的新聞：mark 照樣可投票
        "list_first": "- a\n<!-- mark:    uid=%s -->\n",
    }
    head = "> **今日頭條：** x\n<!-- subject: s -->\n"
    def votables(body: str) -> list[bool]:
        return [b["votable"] for b in parse_report(head + body % uid_a)["blocks"] if "votable" in b]
    assert votables(cases["story_blank"]) == [True, True]
    assert votables(cases["list_after_heading"]) == [False]
    assert votables(cases["paragraph_only_mark"]) == [True]
    assert votables(cases["list_first"]) == [False]  # 前面沒有標題段落
    # 併了多篇文章：連著好幾行 mark，掛在同一個項目的 uids 上（每篇一個）
    merged = parse_report(head + "**[標題](https://a.example/1)**\n- a\n<!-- mark:    uid=%s -->\n<!-- mark:    uid=%s -->\n" % (uid_a, uid_b))
    assert merged["blocks"][-1]["items"][-1]["uids"] == [uid_a, uid_b] and merged["blocks"][-1]["votable"] is True
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        print("用法：python3 src/report_data.py --selftest", file=sys.stderr)
        raise SystemExit(2)
