"""來源載入層：掃 config/sources.d/*.json，合併成 fetch 用的來源清單。

每個檔案一個主題，格式：
{
  "topic": "ai-research",
  "label": "AI / 研究論文動態",
  "enabled": true,                  // 選填，預設 true；整個主題可一鍵關閉
  "defaults": {"weight": 1.0},      // 選填，套用到本檔所有 feed
  "feeds": [
    {"name": "arXiv cs.CV", "type": "arxiv", "query": "cat:cs.CV"},
    {"name": "Hugging Face Blog", "type": "rss", "url": "https://...", "weight": 1.2},
    {"name": "暫時停用的來源", "type": "rss", "url": "https://...", "enabled": false}
  ]
}

檔名不影響行為，topic 以檔內的 "topic" 為準（缺少時用檔名 stem）。
"""
from __future__ import annotations

import json
from pathlib import Path

VALID_TYPES = {"rss", "arxiv"}


class SourceConfigError(ValueError):
    pass


def _validate(feed: dict, origin: str) -> None:
    name = feed.get("name") or "<未命名>"
    if not feed.get("name"):
        raise SourceConfigError(f"{origin}: 有 feed 缺少 name")
    ftype = feed.get("type")
    if ftype not in VALID_TYPES:
        raise SourceConfigError(
            f"{origin}: {name} 的 type={ftype!r} 無效，只接受 {sorted(VALID_TYPES)}")
    if ftype == "rss" and not feed.get("url"):
        raise SourceConfigError(f"{origin}: {name} 是 rss 但沒有 url")
    if ftype == "arxiv" and not feed.get("query"):
        raise SourceConfigError(f"{origin}: {name} 是 arxiv 但沒有 query")
    try:
        float(feed.get("weight", 1.0))
    except (TypeError, ValueError):
        raise SourceConfigError(f"{origin}: {name} 的 weight 不是數字") from None


def load_sources(config_dir: Path) -> tuple[list[dict], dict[str, str], list[str]]:
    """回傳 (sources, topics, notes)。notes 記錄被停用/跳過的項目。"""
    directory = Path(config_dir) / "sources.d"
    if not directory.is_dir():
        raise SourceConfigError(f"找不到來源目錄：{directory}")

    sources: list[dict] = []
    topics: dict[str, str] = {}
    notes: list[str] = []
    seen_keys: dict[str, str] = {}

    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            notes.append(f"略過 {path.name}（底線開頭視為草稿）")
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceConfigError(f"{path.name} 不是合法 JSON：{exc}") from None

        topic = doc.get("topic") or path.stem
        topics[topic] = doc.get("label", topic)
        if doc.get("enabled", True) is False:
            notes.append(f"主題 {topic} 整組停用（{path.name}）")
            continue

        defaults = doc.get("defaults", {})
        for feed in doc.get("feeds", []):
            _validate({**defaults, **feed}, path.name)
            if feed.get("enabled", True) is False:
                notes.append(f"停用：{feed['name']}（{path.name}）")
                continue
            merged = {**defaults, **feed, "topic": topic}
            merged.setdefault("weight", 1.0)
            merged["weight"] = float(merged["weight"])
            merged.pop("enabled", None)

            key = merged.get("url") or f"arxiv:{merged.get('query')}"
            if key in seen_keys:
                notes.append(
                    f"重複來源跳過：{merged['name']}（{path.name}）"
                    f"與 {seen_keys[key]} 指向同一個 {key}")
                continue
            seen_keys[key] = merged["name"]
            sources.append(merged)

    if not sources:
        raise SourceConfigError(f"{directory} 裡沒有任何啟用的來源")
    return sources, topics, notes
