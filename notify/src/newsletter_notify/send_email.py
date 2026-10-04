#!/usr/bin/env python3
"""寄信層：把 render_email.py 產出的 HTML 用 Gmail SMTP 寄出，不依賴 Claude 的 Gmail connector。

用法：uv run newsletter-render | uv run newsletter-send [--dry-run]
      uv run newsletter-send --selftest
stdin 是 render_email.py 印的那行 JSON（subject / headline / html_path）。

環境變數：
  GMAIL_USER           寄件帳號
  GMAIL_APP_PASSWORD   Google 帳號的「應用程式密碼」（不是登入密碼；需先開兩步驟驗證）
  NEWSLETTER_MAIL_TO   收件者，逗號分隔；沒設就寄給自己
--dry-run 只印出信件標頭，不連線。
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path


def build(meta: dict, sender: str, to: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = meta["subject"]
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(f"{meta['headline']}\n\n這封信是 HTML 格式，請用支援 HTML 的信箱開啟。")
    msg.add_alternative(Path(meta["html_path"]).read_text(encoding="utf-8"), subtype="html")
    return msg


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        selftest()
        return 0
    meta = json.loads(sys.stdin.read())
    sender = os.environ.get("GMAIL_USER", "")
    to = [a.strip() for a in os.environ.get("NEWSLETTER_MAIL_TO", sender).split(",") if a.strip()]
    if not sender or not to:
        print("缺 GMAIL_USER（或 NEWSLETTER_MAIL_TO），不寄", file=sys.stderr)
        return 2
    msg = build(meta, sender, to)
    if "--dry-run" in argv:
        print(f"From: {msg['From']}\nTo: {msg['To']}\nSubject: {msg['Subject']}")
        return 0
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        print("缺 GMAIL_APP_PASSWORD，不寄", file=sys.stderr)
        return 2
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    print(f"[send] 已寄出 → {msg['To']}", file=sys.stderr)
    return 0


def selftest() -> None:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
        fh.write("<p>頭條</p>")
    msg = build({"subject": "每日晨間簡報 2026/09/28 — 測試", "headline": "頭條", "html_path": fh.name},
                "a@example.com", ["b@example.com", "c@example.com"])
    os.unlink(fh.name)
    assert msg["To"] == "b@example.com, c@example.com"
    assert msg["Subject"] == "每日晨間簡報 2026/09/28 — 測試"
    html_part = msg.get_body(("html",))
    assert html_part is not None and "頭條" in html_part.get_content()
    assert "頭條" in msg.get_body(("plain",)).get_content()
    print("ok")


if __name__ == "__main__":
    raise SystemExit(main())
