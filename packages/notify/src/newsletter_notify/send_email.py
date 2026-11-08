#!/usr/bin/env python3
"""寄信層：用 Gmail SMTP 寄出 render_email.py 產的 HTML。

用法：uv run --project packages/notify newsletter-render | uv run --project packages/notify newsletter-send [--dry-run]
      echo '{"subject": "…", "text": "…"}' | uv run --project packages/notify newsletter-send --notice   # 純文字通知（worker 的失敗通知）
      uv run --project packages/notify newsletter-send --selftest

環境變數：GMAIL_USER、GMAIL_APP_PASSWORD（應用程式密碼）、NEWSLETTER_MAIL_TO（逗號分隔，預設寄給自己）
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
    text = Path(meta["text_path"]).read_text(encoding="utf-8") if meta.get("text_path") else \
        f"{meta['headline']}\n\n這封信是 HTML 格式，請用支援 HTML 的信箱開啟。"
    msg.set_content(text)
    msg.add_alternative(Path(meta["html_path"]).read_text(encoding="utf-8"), subtype="html")
    return msg


def build_notice(meta: dict, sender: str, to: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = meta["subject"]
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(meta["text"])
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
    msg = build_notice(meta, sender, to) if "--notice" in argv else build(meta, sender, to)
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
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tx, \
            tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as hx:
        tx.write("純文字版\n")
        hx.write("<p>x</p>")
    msg = build({"subject": "s", "headline": "h", "html_path": hx.name, "text_path": tx.name}, "a@example.com", ["b@example.com"])
    os.unlink(tx.name)
    os.unlink(hx.name)
    assert msg.get_body(("plain",)).get_content() == "純文字版\n"
    msg = build_notice({"subject": "失敗", "text": "內容\n"}, "a@example.com", ["b@example.com"])
    assert msg["Subject"] == "失敗" and msg.get_content() == "內容\n" and not msg.is_multipart()
    print("ok")


if __name__ == "__main__":
    raise SystemExit(main())
