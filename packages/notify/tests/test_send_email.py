import io
import json
from datetime import datetime
from email import message_from_bytes, policy

from newsletter_notify import send_email
from newsletter_notify.send_email import build, build_notice, write_outbox


def test_outbox(tmp_path, monkeypatch, capsys):
    """設了 NEWSLETTER_MAIL_OUTBOX 就不寄：整封信寫成 .eml；沒有 GMAIL_USER 也行（測試區）。"""
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NEWSLETTER_MAIL_TO", "")  # 測試區沒設時 compose 給的是空字串
    monkeypatch.setenv(send_email.OUTBOX_ENV, "logs/outbox")
    monkeypatch.setattr(send_email, "ROOT", tmp_path)
    monkeypatch.setattr(send_email.smtplib, "SMTP_SSL", None)  # 真的去連就會炸
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"subject": "失敗通知", "text": "內容\n"})))
    assert send_email.main(["--notice"]) == 0
    (path,) = (tmp_path / "logs" / "outbox").iterdir()
    assert path.name.endswith("-notice.eml") and "不寄" in capsys.readouterr().err
    msg = message_from_bytes(path.read_bytes(), policy=policy.default)
    assert msg["Subject"] == "失敗通知" and msg["From"] == msg["To"] == send_email.OUTBOX_SENDER
    assert msg.get_content() == "內容\n"


def test_outbox_same_second(tmp_path):
    msg = build_notice({"subject": "s", "text": "t\n"}, "a@example.com", ["b@example.com"])
    now = datetime(2026, 11, 14, 8, 0, 0)
    names = [write_outbox(msg, tmp_path, "report", now).name for _ in range(3)]
    assert names == ["20261114-080000-report.eml", "20261114-080000-report-2.eml", "20261114-080000-report-3.eml"]


def test_no_sender_without_outbox(monkeypatch, capsys):
    for k in ("GMAIL_USER", "NEWSLETTER_MAIL_TO", send_email.OUTBOX_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"subject": "s", "text": "t"})))
    assert send_email.main(["--notice"]) == 2 and "缺 GMAIL_USER" in capsys.readouterr().err


def test_build(tmp_path):
    html = tmp_path / "r.html"
    html.write_text("<p>頭條</p>", encoding="utf-8")
    msg = build({"subject": "每日晨間簡報 2026/09/28 — 測試", "headline": "頭條", "html_path": str(html)},
                "a@example.com", ["b@example.com", "c@example.com"])
    assert msg["To"] == "b@example.com, c@example.com"
    assert msg["Subject"] == "每日晨間簡報 2026/09/28 — 測試"
    html_part = msg.get_body(("html",))
    assert html_part is not None and "頭條" in html_part.get_content()
    assert "頭條" in msg.get_body(("plain",)).get_content()


def test_build_text_part(tmp_path):
    tx, hx = tmp_path / "r.txt", tmp_path / "r.html"
    tx.write_text("純文字版\n", encoding="utf-8")
    hx.write_text("<p>x</p>", encoding="utf-8")
    msg = build({"subject": "s", "headline": "h", "html_path": str(hx), "text_path": str(tx)}, "a@example.com", ["b@example.com"])
    assert msg.get_body(("plain",)).get_content() == "純文字版\n"


def test_notice():
    msg = build_notice({"subject": "失敗", "text": "內容\n"}, "a@example.com", ["b@example.com"])
    assert msg["Subject"] == "失敗" and msg.get_content() == "內容\n" and not msg.is_multipart()


def test_notice_attachment(tmp_path):
    ax = tmp_path / "a.jsonl"
    ax.write_bytes(b'{"uid": "x"}\n')
    msg = build_notice({"subject": "備份", "text": "t\n", "attachments": [{"path": str(ax), "filename": "f.jsonl"}]},
                       "a@example.com", ["b@example.com"])
    att = next(msg.iter_attachments())
    assert msg.get_body(("plain",)).get_content() == "t\n" and att.get_filename() == "f.jsonl"
    assert att.get_content() == b'{"uid": "x"}\n'
