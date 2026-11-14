#!/usr/bin/env python3
"""報告資料層：reports/<date>.md → 結構化資料，網頁與 email 共用這一個解析器。

用法：uv run --project packages/shared newsletter-report-check [YYYY-MM-DD]

回傳 {"title", "date", "subject", "headline", "blocks", "sources"}；blocks：
  {"type": "title", "title", "date"}
  {"type": "heading" | "callout" | "paragraph", "inline": [...]}
  {"type": "list", "items": [{"inline", "children"?, "uids"?}], "votable"}
  {"type": "mark", "uid", "votable"}   # 沒掛在清單項目底下的 mark
inline：text / strong / link / code 節點。

votable：只有主要新聞（標題段落＋清單）可投票，緊接在 ## 後面的單行清單（「其餘收錄」）不行。
規則只在這裡，網頁與 email 只讀這個旗標。格式不符時 raise ValueError。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from newsletter_shared.feedback import MARK_RE
from newsletter_shared import ROOT, today

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


def _restore(s: str, held: list) -> str:
    return SLOT.sub(lambda m: held[int(m[1])][2], s)


def _texts(s: str, held: list) -> list[dict]:
    nodes: list[dict] = []
    pos = 0
    for m in SLOT.finditer(s):
        if m.start() > pos:
            nodes.append({"type": "text", "text": s[pos:m.start()]})
        label, url, _ = held[int(m[1])]
        nodes.append({"type": "link", "href": url, "children": _bold(label, held)})
        pos = m.end()
    if pos < len(s):
        nodes.append({"type": "text", "text": s[pos:]})
    return nodes


def _code(s: str, held: list) -> list[dict]:
    nodes: list[dict] = []
    pos = 0
    for m in CODE.finditer(s):
        nodes += _texts(s[pos:m.start()], held)
        nodes.append({"type": "code", "text": _restore(m[1], held)})  # 行內碼裡的連結不轉成連結，原樣還原
        pos = m.end()
    return nodes + _texts(s[pos:], held)


def _bold(s: str, held: list) -> list[dict]:
    nodes: list[dict] = []
    pos = 0
    for m in BOLD.finditer(s):
        nodes += _code(s[pos:m.start()], held)
        nodes.append({"type": "strong", "children": _code(m[1], held)})
        pos = m.end()
    return nodes + _code(s[pos:], held)


def parse_inline(text: str, links: list[tuple[str, str]] | None = None) -> list[dict]:
    """一行文字 → inline 節點。links 傳入的話，會把這行裡的 (標題, 網址) 依序加進去（資料來源清單用）。"""
    held: list[tuple[str, str, str]] = []  # (連結文字, 網址, 原始寫法)

    def hold(m: re.Match) -> str:
        held.append((m[1], m[2], m[0]))
        return f"{SLOT_OPEN}{len(held) - 1}{SLOT_CLOSE}"

    work = LINK.sub(hold, text.replace(SLOT_OPEN, "").replace(SLOT_CLOSE, ""))
    if links is not None:
        links.extend((label.strip("*"), url) for label, url, _ in held)
    return _bold(work, held)


class _ReportParser:
    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.links: list[tuple[str, str]] = []
        self.subject = self.headline = self.title = ""
        self.current: dict | None = None   # 目前開著的 list block
        self.stack: list[list[dict]] = []  # stack[d - 1] ＝ 第 d 層的項目清單

    def comment(self, line: str, body: str) -> None:
        if body.strip().startswith("subject:"):
            self.subject = body.strip()[len("subject:"):].strip()
        mark = MARK_RE.search(line)
        if not mark:
            return
        if self.current is not None:
            self.current["items"][-1].setdefault("uids", []).append(mark[2])
        else:
            self.blocks.append({"type": "mark", "uid": mark[2], "votable": votable_after(self.blocks, standalone_mark=True)})

    def bullet(self, indent: str, text: str) -> None:
        level = len(indent) // 2 + 1
        if self.current is None:  # 新清單一定從第一層開始，開頭就縮排也一樣
            self.current = {"type": "list", "items": [], "votable": votable_after(self.blocks)}
            self.blocks.append(self.current)
            self.stack = [self.current["items"]]
            level = 1
        level = min(level, len(self.stack) + 1)  # 一次只往下一層
        if level > len(self.stack):
            parent = self.stack[-1][-1]
            self.stack.append(parent.setdefault("children", []))
        else:
            del self.stack[level:]
        self.stack[level - 1].append({"inline": parse_inline(text, self.links)})

    def block(self, line: str) -> None:
        if line.startswith("# "):
            title = line[2:].strip()
            date = TITLE_DATE.search(title)
            if date:
                title = title[:date.start()].strip()
            self.title = title
            self.blocks.append({"type": "title", "title": title, "date": date[0] if date else None})
        elif line.startswith("## "):
            self.blocks.append({"type": "heading", "inline": parse_inline(line[3:].strip(), self.links)})
        elif line.startswith(">"):
            text = line.lstrip("> ").strip()
            if "今日頭條" in text:
                self.headline = plain(re.sub(r"^\**今日頭條[：:]\**\s*", "", text))
            self.blocks.append({"type": "callout", "inline": parse_inline(text, self.links)})
        else:
            self.blocks.append({"type": "paragraph", "inline": parse_inline(line.strip(), self.links)})

    def feed(self, line: str) -> None:
        if comment := COMMENT.match(line):
            self.comment(line, comment[1])
            return  # 其餘註解不進頁面，也不打斷清單
        if bullet := BULLET.match(line):
            self.bullet(bullet[1], bullet[2])
            return
        self.current = None
        if line.strip():
            self.block(line)


def parse_report(markdown: str) -> dict:
    p = _ReportParser()
    for line in markdown.splitlines():
        p.feed(line)
    if not p.headline:
        raise ValueError("找不到「> **今日頭條：** …」那一行")
    if not p.subject:
        raise ValueError("找不到 <!-- subject: … --> 註解")

    seen: set[str] = set()
    sources = [{"title": t, "url": u} for t, u in p.links if not (u in seen or seen.add(u))]
    return {"title": p.title, "subject": p.subject, "headline": p.headline, "blocks": p.blocks, "sources": sources}


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


def check(argv: list[str] | None = None, root: Path = ROOT) -> int:
    """newsletter-report-check：報告格式正確 exit 0；找不到檔或格式不符 exit 1，原因印到 stderr。"""
    args = sys.argv[1:] if argv is None else argv
    stamp = args[0] if args else today(root)
    src = root / "reports" / f"{stamp}.md"
    if not src.exists():
        print(f"找不到 {src}", file=sys.stderr)
        return 1
    try:
        parse_report(src.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"{src.name} 格式不符：{exc}", file=sys.stderr)
        return 1
    print(f"{src.name} 格式正確")
    return 0
