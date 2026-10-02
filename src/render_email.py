#!/usr/bin/env python3
"""email 層：reports/<date>.md（news-digest 的條列版）→ reports/<date>.html。

用法：python3 src/render_email.py [YYYY-MM-DD] [--selftest]
stdout 印一行 JSON：{"subject", "headline", "html_path"}，給排程 prompt 寄信用。

版型寫死在這裡而不是讓 Claude 每天手寫 HTML：每天長得一樣、不花 token。
email client 會剝掉 <style>，所以 CSS 全部 inline。
只認 SKILL.md 規定的 Markdown 子集（# / ## / > / - / 兩格縮排的 - / 粗體 / 連結 / <!-- -->）。
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402

FONT = ("-apple-system,BlinkMacSystemFont,'PingFang TC','Noto Sans TC',"
        "'Microsoft JhengHei','Helvetica Neue',Arial,sans-serif")
S = {
    "body": f"margin:0;padding:24px 12px;background:#f3f4f6;font-family:{FONT};color:#1f2937;",
    "card": "max-width:640px;margin:0 auto;background:#ffffff;border-radius:10px;padding:28px 28px 20px;",
    "h1": "margin:0;font-size:24px;line-height:1.3;color:#111827;",
    "date": "margin:4px 0 20px;font-size:13px;color:#6b7280;",
    "callout": ("margin:0 0 8px;padding:14px 16px;background:#fff7ed;border-left:4px solid #f97316;"
                "border-radius:6px;font-size:15px;line-height:1.6;"),
    "h2": ("margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid #e5e7eb;"
           "font-size:18px;color:#111827;"),
    "p": "margin:14px 0 6px;font-size:15px;line-height:1.6;",
    "ul": "margin:4px 0 10px;padding-left:22px;",
    "li": "margin:3px 0;font-size:14px;line-height:1.6;",
    "a": "color:#2563eb;text-decoration:none;",
    "code": "font-family:Menlo,Consolas,monospace;font-size:13px;background:#f3f4f6;padding:1px 4px;border-radius:3px;",
    "src_li": "margin:2px 0;font-size:12px;line-height:1.5;color:#6b7280;",
}
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
COMMENT = re.compile(r"^\s*<!--(.*?)-->\s*$")


def inline(text: str, links: list[tuple[str, str]]) -> str:
    for title, url in LINK.findall(text):
        links.append((title.strip("*"), url))
    out = html.escape(text, quote=False)
    out = LINK.sub(lambda m: f'<a href="{html.escape(m[2])}" style="{S["a"]}">{m[1]}</a>', out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`([^`]+)`", lambda m: f'<code style="{S["code"]}">{m[1]}</code>', out)


def plain(text: str) -> str:
    return re.sub(r"\*\*|`", "", LINK.sub(r"\1", text)).strip()


def render_body(markdown: str) -> tuple[str, str, str]:
    """回傳 (卡片內文 html, subject 短語, 今日頭條)。格式不符時 raise ValueError。"""
    body: list[str] = []
    links: list[tuple[str, str]] = []
    subject = headline = ""
    depth = 0  # 目前開著幾層 <ul>

    def close_lists(to: int = 0) -> None:
        nonlocal depth
        while depth > to:
            body.append("</li></ul>")
            depth -= 1

    for line in markdown.splitlines():
        comment = COMMENT.match(line)
        if comment:
            if comment[1].strip().startswith("subject:"):
                subject = comment[1].strip()[len("subject:"):].strip()
            continue  # 其餘註解（含 mark）不進頁面，也不打斷清單
        bullet = re.match(r"^( *)[-*] (.+)$", line)
        if bullet:
            level = len(bullet[1]) // 2 + 1
            if level > depth:
                body.append(f'<ul style="{S["ul"]}">' * (level - depth))
                depth = level
            else:
                close_lists(level)
                body.append("</li>")
            body.append(f'<li style="{S["li"]}">{inline(bullet[2], links)}')
            continue
        close_lists()
        if not line.strip():
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            date = re.search(r"\d{4}-\d{2}-\d{2}$", title)
            if date:
                title = title[:date.start()].strip()
            body.append(f'<h1 style="{S["h1"]}">{html.escape(title)}</h1>')
            if date:
                body.append(f'<p style="{S["date"]}">{date[0].replace("-", "/")}</p>')
        elif line.startswith("## "):
            body.append(f'<h2 style="{S["h2"]}">{inline(line[3:].strip(), links)}</h2>')
        elif line.startswith(">"):
            text = line.lstrip("> ").strip()
            if "今日頭條" in text:
                headline = plain(re.sub(r"^\**今日頭條[：:]\**\s*", "", text))
            body.append(f'<div style="{S["callout"]}">{inline(text, links)}</div>')
        else:
            body.append(f'<p style="{S["p"]}">{inline(line.strip(), links)}</p>')
    close_lists()

    if not headline:
        raise ValueError("找不到「> **今日頭條：** …」那一行")
    if not subject:
        raise ValueError("找不到 <!-- subject: … --> 註解")

    seen: set[str] = set()
    sources = [(t, u) for t, u in links if not (u in seen or seen.add(u))]
    if sources:
        body.append(f'<h2 style="{S["h2"]}">資料來源</h2><ol style="{S["ul"]}">')
        body += [f'<li style="{S["src_li"]}"><a href="{html.escape(u)}" style="{S["a"]}">'
                 f'{html.escape(t)}</a></li>' for t, u in sources]
        body.append("</ol>")

    return "".join(body), subject, headline


def render(markdown: str) -> tuple[str, str, str]:
    """回傳 (完整 email html, subject 短語, 今日頭條)。格式不符時 raise ValueError。"""
    body, subject, headline = render_body(markdown)
    page = (f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body style="{S["body"]}"><div style="{S["card"]}">{body}</div></body></html>')
    return page, subject, headline


def selftest() -> None:
    md = """# 每日晨間簡報 2026-09-28

<!-- subject: 測試頭條 -->

> **今日頭條：** 某事發生，見 [來源](https://a.example/x?a=1&b=2)。

## 科技與 AI

**[重點 <標題>](https://a.example/1)**

- 發生什麼：A
  - 細節
- 背景：B
<!-- mark:    uid=0123456789abcdef -->

## 其餘收錄

- **[其他](https://a.example/2)** — 半句
<!-- mark:    uid=0123456789abcdee -->
- **[其他二](https://a.example/1)** — 重複連結
"""
    page, subject, headline = render(md)
    assert subject == "測試頭條" and headline == "某事發生，見 來源。", (subject, headline)
    assert page.count("<ul") == page.count("</ul>") == 3, page
    assert page.count("<li") - page.count("</li>") == 0
    assert "&lt;標題&gt;" in page and "mark:" not in page
    assert "a=1&amp;b=2" in page
    assert page.count("https://a.example/1") == 3  # 兩處內文 + 資料來源只列一次
    assert "2026/09/28" in page
    try:
        render("# x\n")
        raise AssertionError("缺頭條應該報錯")
    except ValueError:
        pass
    print("ok")


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        selftest()
        return 0
    stamp = argv[0] if argv else f"{datetime.now(ZoneInfo('Asia/Taipei')):%Y-%m-%d}"
    src = ROOT / "reports" / f"{stamp}.md"
    if not src.exists():
        print(f"找不到 {src}，先跑 news-digest 寫報告", file=sys.stderr)
        return 1
    try:
        with metrics.timed("render") as m:
            page, subject, headline = render(src.read_text(encoding="utf-8"))
            m.update(html_bytes=len(page.encode()))
    except ValueError as exc:
        print(f"{src.name} 格式不符：{exc}", file=sys.stderr)
        return 1
    out = src.with_suffix(".html")
    out.write_text(page, encoding="utf-8")
    print(json.dumps({"subject": f"每日晨間簡報 {stamp.replace('-', '/')} — {subject}",
                      "headline": headline, "html_path": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
