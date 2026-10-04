#!/usr/bin/env python3
"""email 層：reports/<date>.md（news-digest 的條列版）→ reports/<date>.html。

用法：python3 src/render_email.py [YYYY-MM-DD] [--selftest]
stdout 印一行 JSON：{"subject", "headline", "html_path"}，給排程 prompt 寄信用。

環境變數 NEWSLETTER_BASE_URL（對外網址，如 https://news.example.com）有設的話，每則主要新聞（有 mark 註解的）
底下加 👍／👎 兩個連結，指向 <base>/feedback/<uid>?v=…。連結只開確認頁，按了確認才寫入（信箱的安全掃描會自動開連結）。
沒設就不加按鈕，本機測試不受影響。

與網頁一致：只有主要新聞（標題段落＋清單）放連結，「其餘收錄」那種沒有標題段落的整張單行清單不放；
併了多篇文章的新聞會連著好幾行 mark 註解（每篇一個 uid），只放一組連結，uid 用逗號接起來：
<base>/feedback/<uid>,<uid>?v=…，確認頁一次對每個 uid 各投一票。

版型寫死在這裡而不是讓 Claude 每天手寫 HTML：每天長得一樣、不花 token。
email client 會剝掉 <style>，所以 CSS 全部 inline。
Markdown 不在這裡解析：報告交給 report_data.parse_report（網頁也用同一份結構），這裡只負責把結構排成 inline-CSS HTML。
內容規則（哪些條目可以投票、併了多篇的新聞有幾個 uid）都在那一份結構裡，版型改動只動這個檔案與網頁的 CSS。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from newsletter_shared import metrics
from newsletter_shared.report_data import parse_report

ROOT = Path(__file__).resolve().parent.parent

BASE_URL_ENV = "NEWSLETTER_BASE_URL"

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
    "fb": "margin:8px 0 4px;",
    "fb_btn": ("display:inline-block;margin:0 8px 4px 0;padding:8px 14px;border:1px solid #d1d5db;"
               "border-radius:16px;background:#f9fafb;font-size:13px;line-height:1.4;color:#374151;"
               "text-decoration:none;"),
}


def inline(nodes: list[dict]) -> str:
    """report_data 的 inline 節點 → HTML。文字 escape 一次；網址在 href 裡 escape（& → &amp;）。"""
    out: list[str] = []
    for node in nodes:
        kind = node["type"]
        if kind == "text":
            out.append(html.escape(node["text"], quote=False))
        elif kind == "code":
            out.append(f'<code style="{S["code"]}">{html.escape(node["text"], quote=False)}</code>')
        elif kind == "strong":
            out.append(f"<strong>{inline(node['children'])}</strong>")
        else:  # link
            out.append(f'<a href="{html.escape(node["href"])}" style="{S["a"]}">{inline(node["children"])}</a>')
    return "".join(out)


def feedback_buttons(uids: list[str], base_url: str) -> str:
    """👍／👎 連結。v 的 + 要寫成 %2B，否則 query 會把它當成空白。
    uids 有好幾個＝併了多篇文章的新聞：路徑用逗號接起來，確認頁對每個 uid 各投一票。"""
    path = quote(",".join(uids), safe=",")

    def link(vote: str, label: str) -> str:
        href = html.escape(f"{base_url}/feedback/{path}?v={quote(vote)}")
        return f'<a href="{href}" style="{S["fb_btn"]}">{label}</a>'
    return f'<div style="{S["fb"]}">{link("+", "👍 有用")}{link("-", "👎 沒用")}</div>'


def list_html(items: list[dict], votable: bool, base_url: str) -> str:
    """巢狀清單。按鈕放在項目的巢狀子清單之後、仍在這個 <li> 裡（與網頁一致）。
    base_url 空（本機測試）或這張清單不可投票（votable，見 report_data）就不放。"""
    out = [f'<ul style="{S["ul"]}">']
    for item in items:
        out.append(f'<li style="{S["li"]}">{inline(item["inline"])}')
        if item.get("children"):
            out.append(list_html(item["children"], votable, base_url))
        if votable and base_url and item.get("uids"):
            out.append(feedback_buttons(list(dict.fromkeys(item["uids"])), base_url))  # 重複的 uid 只算一次
        out.append("</li>")
    out.append("</ul>")
    return "".join(out)


def block_html(block: dict, base_url: str) -> str:
    kind = block["type"]
    if kind == "title":
        out = f'<h1 style="{S["h1"]}">{html.escape(block["title"])}</h1>'
        if block["date"]:
            out += f'<p style="{S["date"]}">{block["date"].replace("-", "/")}</p>'
        return out
    if kind == "heading":
        return f'<h2 style="{S["h2"]}">{inline(block["inline"])}</h2>'
    if kind == "callout":
        return f'<div style="{S["callout"]}">{inline(block["inline"])}</div>'
    if kind == "paragraph":
        return f'<p style="{S["p"]}">{inline(block["inline"])}</p>'
    if kind == "list":
        return list_html(block["items"], block["votable"], base_url)
    # mark：沒掛在任何清單項目底下的獨立 mark
    return feedback_buttons([block["uid"]], base_url) if block["votable"] and base_url else ""


def render_report(report: dict, base_url: str = "") -> tuple[str, str, str]:
    """report_data.parse_report 的結構 → (完整 email html, subject 短語, 今日頭條)。
    base_url 非空才加 👍／👎 連結（要是 http(s):// 開頭、結尾不帶斜線）。"""
    body = [block_html(block, base_url) for block in report["blocks"]]
    if report["sources"]:
        body.append(f'<h2 style="{S["h2"]}">資料來源</h2><ol style="{S["ul"]}">')
        body += [f'<li style="{S["src_li"]}"><a href="{html.escape(src["url"])}" style="{S["a"]}">'
                 f'{html.escape(src["title"])}</a></li>' for src in report["sources"]]
        body.append("</ol>")
    page = (f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body style="{S["body"]}"><div style="{S["card"]}">{"".join(body)}</div></body></html>')
    return page, report["subject"], report["headline"]


def render(markdown: str, base_url: str = "") -> tuple[str, str, str]:
    """Markdown → (完整 email html, subject 短語, 今日頭條)。格式不符時 raise ValueError（parse_report）。"""
    return render_report(parse_report(markdown), base_url)


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
    assert page.count("a=1&amp;b=2") == 2 and "&amp;amp;" not in page  # 頭條內文連結 + 資料來源，各 escape 一次
    assert page.count("https://a.example/1") == 3  # 兩處內文 + 資料來源只列一次
    assert "2026/09/28" in page
    try:
        render("# x\n")
        raise AssertionError("缺頭條應該報錯")
    except ValueError:
        pass

    # 回饋連結：沒設 base_url 就沒有；有設的話每則主要新聞一組，按鈕在巢狀子項目之後、仍在最外層項目裡
    assert "/feedback/" not in page
    page, _, _ = render(md, "https://news.example.com")
    assert page.count("/feedback/") == 2 and page.count("<ul") == page.count("</ul>") == 3
    uid = "0123456789abcdef"
    assert f"https://news.example.com/feedback/{uid}?v=%2B" in page and f"/feedback/{uid}?v=-" in page
    assert "👍 有用" in page and "mark:" not in page
    assert re.search(r"背景：B<div[^>]*><a [^>]*0123456789abcdef.*?</div></li></ul>", page), page
    # 「其餘收錄」（緊接在 ## 後面的整張單行清單）不放連結（與網頁一致），但 mark 註解不能漏進頁面
    assert "0123456789abcdee" not in page and re.search(r"半句</li><li[^>]*><strong>", page), page
    # mark 直接接在巢狀項目後：巢狀清單先收掉，按鈕仍在最外層項目裡
    nested, _, _ = render(md.replace("- 背景：B\n", "").replace("  - 細節\n", "  - 細節\n  - 更深\n"), "https://n.example")
    assert re.search(r"更深</li></ul><div[^>]*><a [^>]*0123456789abcdef.*?</div></li></ul>", nested), nested
    assert nested.count("<ul") == nested.count("</ul>") and nested.count("<li") == nested.count("</li>")

    # 併了多篇文章的新聞：連著好幾行 mark（含重複的 uid、夾著別的註解），只放一組連結，uid 用逗號接；
    # 其餘收錄不放；其餘收錄之後的主要新聞照樣放（不能被前面的狀態帶壞）
    merged_md = md.replace(
        "<!-- mark:    uid=0123456789abcdef -->\n",
        "<!-- mark:    uid=0123456789abcdef -->\n<!-- 別的註解 -->\n<!-- mark:    uid=1111111111111111 -->\n"
        "<!-- mark:    uid=0123456789abcdef -->\n",
    ) + """
## 商業與市場

**[另一則](https://a.example/3)**

- 背景：C
<!-- mark:    uid=2222222222222222 -->
"""
    merged, _, _ = render(merged_md, "https://news.example.com")
    base = "https://news.example.com/feedback/"
    assert merged.count("/feedback/") == 4, merged  # 併過的一組（👍＋👎）＋另一則一組
    assert merged.count(f"{base}0123456789abcdef,1111111111111111?v=%2B") == 1
    assert merged.count(f"{base}0123456789abcdef,1111111111111111?v=-") == 1
    assert merged.count(f"{base}2222222222222222?v=%2B") == 1 and "0123456789abcdee" not in merged
    assert re.search(r"背景：B<div[^>]*><a [^>]*0123456789abcdef,1111111111111111.*?</div></li></ul>", merged), merged
    assert merged.count("<ul") == merged.count("</ul>") == 4 and merged.count("<li") == merged.count("</li>")
    assert "/feedback/" not in render(merged_md)[0] and "mark:" not in merged
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
    base_url = os.environ.get(BASE_URL_ENV, "").strip().rstrip("/")
    if base_url and not re.match(r"https?://[^\s/]", base_url):
        print(f"{BASE_URL_ENV} 要以 http:// 或 https:// 開頭，收到「{base_url}」；這次不加回饋按鈕", file=sys.stderr)
        base_url = ""
    try:
        with metrics.timed("render") as m:
            page, subject, headline = render(src.read_text(encoding="utf-8"), base_url)
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
