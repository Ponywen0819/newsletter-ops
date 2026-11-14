"""deploy/sample-data（devtools seed 的範例資料）要過得了真正的解析器：報告格式、每則都有 curated、回饋是有效的列。"""
import json
from pathlib import Path

import pytest

from newsletter_shared.feedback import read_feedback
from newsletter_shared.report_data import parse_report, uids_of

SAMPLE = Path(__file__).resolve().parents[3] / "deploy" / "sample-data"
DATES = sorted(p.stem for p in (SAMPLE / "reports").glob("*.md"))


def test_has_days():
    assert len(DATES) >= 3


@pytest.mark.parametrize("stamp", DATES)
def test_report_matches_curated(stamp):
    report = parse_report((SAMPLE / "reports" / f"{stamp}.md").read_text(encoding="utf-8"))
    assert report["blocks"][0]["date"] == stamp and report["subject"] and report["headline"]
    curated = json.loads((SAMPLE / "data" / "curated" / f"{stamp}.json").read_text(encoding="utf-8"))
    assert sorted(uids_of(report)) == sorted(i["uid"] for i in curated["items"])  # 每則都寫進報告、也只寫這些


def test_feedback_points_at_sample_items():
    rows = read_feedback(SAMPLE / "state" / "feedback.jsonl")
    items = {i["uid"] for p in (SAMPLE / "data" / "curated").glob("*.json")
             for i in json.loads(p.read_text(encoding="utf-8"))["items"]}
    assert rows and set(rows) <= items and all(r["report"][:-3] in DATES for r in rows.values())
