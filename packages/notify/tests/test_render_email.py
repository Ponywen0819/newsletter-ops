import re

import pytest

from newsletter_notify.render_email import S, render, render_text
from newsletter_shared.report_data import parse_report

MD = """# 每日晨間簡報 2026-09-28

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
BASE = "https://news.example.com"


def test_html():
    page, subject, headline = render(MD)
    assert subject == "測試頭條" and headline == "某事發生，見 來源。"
    assert page.count("<ul") == page.count("</ul>") == 3
    assert page.count("<li") - page.count("</li>") == 0
    assert "&lt;標題&gt;" in page and "mark:" not in page
    assert page.count("a=1&amp;b=2") == 2 and "&amp;amp;" not in page
    assert page.count("https://a.example/1") == 3
    assert "2026/09/28" in page
    assert "<title>測試頭條</title>" in page
    assert page.index("display:none") < page.index(S["card"]) and "某事發生，見 來源。</div>" in page  # 預覽文字在最前面
    assert "/feedback/" not in page and "網頁版" not in page  # 沒設 base_url 就沒有回饋連結


def test_text():
    text = render_text(parse_report(MD))
    assert text.startswith("每日晨間簡報 2026/09/28\n") and "【科技與 AI】" in text
    assert "某事發生，見 來源。" in text and "- 發生什麼：A\n  - 細節\n" in text
    assert "1. 來源\n   https://a.example/x?a=1&b=2" in text and "mark:" not in text and "<" not in text.replace("<標題>", "")
    assert "\n\n\n" not in text and "網頁版" not in text
    assert "網頁版：https://news.example.com/reports/2026-09-28\n" in render_text(parse_report(MD), BASE)


def test_missing_headline():
    with pytest.raises(ValueError):
        render("# x\n")


def test_feedback_links():
    """每則主要新聞一組，按鈕在巢狀子項目之後、仍在最外層項目裡。"""
    page, _, _ = render(MD, BASE)
    assert 'href="https://news.example.com/reports/2026-09-28"' in page
    assert page.count("/feedback/") == 2 and page.count("<ul") == page.count("</ul>") == 3
    uid = "0123456789abcdef"
    assert f"https://news.example.com/feedback/{uid}?v=%2B" in page and f"/feedback/{uid}?v=-" in page
    assert "👍 有用" in page and "mark:" not in page
    assert re.search(r"背景：B<div[^>]*><a [^>]*0123456789abcdef.*?</div></li></ul>", page)
    # 「其餘收錄」（緊接在 ## 後面的整張單行清單）不放連結（與網頁一致），但 mark 註解不能漏進頁面
    assert "0123456789abcdee" not in page and re.search(r"半句</li><li[^>]*><strong>", page)


def test_mark_after_nested():
    """mark 直接接在巢狀項目後：巢狀清單先收掉，按鈕仍在最外層項目裡。"""
    nested, _, _ = render(MD.replace("- 背景：B\n", "").replace("  - 細節\n", "  - 細節\n  - 更深\n"), "https://n.example")
    assert re.search(r"更深</li></ul><div[^>]*><a [^>]*0123456789abcdef.*?</div></li></ul>", nested)
    assert nested.count("<ul") == nested.count("</ul>") and nested.count("<li") == nested.count("</li>")


def test_merged_marks():
    """連著好幾行 mark（含重複的 uid、夾著別的註解）只放一組連結，uid 用逗號接；其餘收錄之後的主要新聞照樣放。"""
    merged_md = MD.replace(
        "<!-- mark:    uid=0123456789abcdef -->\n",
        "<!-- mark:    uid=0123456789abcdef -->\n<!-- 別的註解 -->\n<!-- mark:    uid=1111111111111111 -->\n"
        "<!-- mark:    uid=0123456789abcdef -->\n",
    ) + """
## 商業與市場

**[另一則](https://a.example/3)**

- 背景：C
<!-- mark:    uid=2222222222222222 -->
"""
    merged, _, _ = render(merged_md, BASE)
    base = BASE + "/feedback/"
    assert merged.count("/feedback/") == 4
    assert merged.count(f"{base}0123456789abcdef,1111111111111111?v=%2B") == 1
    assert merged.count(f"{base}0123456789abcdef,1111111111111111?v=-") == 1
    assert merged.count(f"{base}2222222222222222?v=%2B") == 1 and "0123456789abcdee" not in merged
    assert re.search(r"背景：B<div[^>]*><a [^>]*0123456789abcdef,1111111111111111.*?</div></li></ul>", merged)
    assert merged.count("<ul") == merged.count("</ul>") == 4 and merged.count("<li") == merged.count("</li>")
    assert "/feedback/" not in render(merged_md)[0] and "mark:" not in merged
