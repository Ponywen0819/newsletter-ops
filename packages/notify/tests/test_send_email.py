from newsletter_notify.send_email import build, build_notice


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
