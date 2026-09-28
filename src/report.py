"""輸出層：產生模板版 Markdown 報告（無 LLM 的保底版本）。

有洞察的敘事版報告由 Claude 依 news-digest skill 讀 data/curated/<date>.json
後改寫，覆蓋同一個 reports/<date>.md。兩種版本都要保留每則的 mark 註解，
否則 feedback.py 收不到標記。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from feedback import mark_comment  # noqa: E402


def render(items: list[dict], stats: dict, config: dict, errors: list[str]) -> str:
    tz = ZoneInfo(config.get("timezone", "Asia/Taipei"))
    now = datetime.now(tz)
    topics = config.get("topics", {})

    lines = [
        f"# 新聞摘要 {now:%Y-%m-%d}",
        "",
        f"> 產出時間：{now:%Y-%m-%d %H:%M} ({config.get('timezone')})　"
        f"抓取 {stats['fetched']} 則 → 時間窗內新項目 {stats['in_window_new']} 則 → "
        f"通過相關性 {stats['passed_relevance']} 則 → 收錄 {stats['after_dedupe_and_quota']} 則",
        "",
        "<!-- 看完把每則的 mark: 填上 + 或 -（++ / -- 表示強烈），再跑 python3 src/feedback.py -->",
        "",
        "## 今日重點",
        "",
        "<!-- claude:summary — 由 news-digest skill 填寫；模板版留白 -->",
        "",
    ]

    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item["topic"], []).append(item)

    for topic, bucket in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        lines += [f"## {topics.get(topic, topic)}（{len(bucket)}）", ""]
        for item in bucket:
            meta = [item["source"]]
            if item.get("published"):
                meta.append(item["published"][:16].replace("T", " ") + "Z")
            if item.get("also_in"):
                meta.append("另見：" + "、".join(item["also_in"]))
            lines.append(f"### [{item['title']}]({item['url']})")
            lines.append(
                f"*{' ｜ '.join(meta)} ｜ relevance {item['relevance']} ｜ rank {item['rank']}*")
            if item.get("summary"):
                lines.append("")
                lines.append(item["summary"][:400])
            lines.append("")
            lines.append(mark_comment(item["uid"]))
            lines.append("")

    if errors:
        lines += ["## 抓取失敗來源", ""] + [f"- {e}" for e in errors] + [""]

    return "\n".join(lines)


def write(markdown: str, config: dict) -> Path:
    tz = ZoneInfo(config.get("timezone", "Asia/Taipei"))
    path = ROOT / "reports" / f"{datetime.now(tz):%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path
