#!/usr/bin/env python3
"""寄信層：用 Gmail SMTP 寄出 render_email.py 產的 HTML。

用法：uv run --project packages/notify newsletter-render | uv run --project packages/notify newsletter-send [--dry-run]
      echo '{"subject": "…", "text": "…"}' | uv run --project packages/notify newsletter-send --notice   # 純文字通知（worker 的失敗通知）
      notice 可加 "attachments": [{"path": "…", "filename": "…"}]（worker 的回饋備份）

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
    for a in meta.get("attachments", []):
        msg.add_attachment(Path(a["path"]).read_bytes(), maintype="application", subtype="octet-stream",
                           filename=a["filename"])
    return msg


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
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


if __name__ == "__main__":
    raise SystemExit(main())
