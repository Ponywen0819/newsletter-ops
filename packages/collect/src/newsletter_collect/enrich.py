"""補原文：收錄的新聞由程式抓原文，存進 curated 的 fulltext，writer 讀檔判斷，不必自己上網。

抽文字：新聞優先只接 <p> 段落，段落太少才取整塊文字；論文取整塊（作者機構不在段落裡）。
挑選：所有新聞（含搜尋補來的與 near_misses）、還沒有主會議資訊的 arXiv 前 PAPERS 篇
（讀 arxiv.org/html/<id>，開頭有作者機構；沒有 HTML 版就讀 abs 頁）。抓不到就記 fulltext_error。

擋板：反廣告攔截／要開 JavaScript／機器人驗證的頁面不是內文，付費牆只留牆前面的開頭（太短就當抓不到）。
內容不足：原文抓不到、摘要又短（或只是重複標題）的新聞不收，報告裡不會出現只有標題的項目；
空出來的名額由同主題沒進配額的候補依 rank 補上（settle）。arXiv 論文的摘要就是內容，不受影響。
"""
from __future__ import annotations

import html
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import urlparse

from newsletter_collect import fetch
from newsletter_shared import metrics

PAPERS = 3
CHARS = 3000
MAIN = re.compile(r"<(article|main)\b[^>]*>(.*?)</\1\s*>", re.S | re.I)
DROP = re.compile(r"<(nav|header|footer|aside|form)\b.*?</\1\s*>", re.S | re.I)
PARA = re.compile(r"<p\b[^>]*>(.*?)</p\s*>", re.S | re.I)
MIN_PARA = 25  # 字元；更短的多半是圖說、按鈕、「本文共 N 字」
MIN_PARAS_TEXT = 200  # 段落加起來不到這麼多就退回整塊文字（有些網站內文不用 <p>）
JUNK = re.compile(r"(?:\s*'\s*,\s*'){2,}'?|(?:\s*-->){2,}")  # 經濟日報頁面裡外漏的 JS 陣列「', ' ', '」與註解尾巴

# 整頁都是擋板：Nature 的「JavaScript is disabled… disable any ad blockers」、Cloudflare 的驗證頁。
# 擋板頁很短，長文裡順帶提到這些字（報導廣告攔截的新聞）不算。
BLOCKED = re.compile(
    # 不比對單獨的「enable JavaScript」：BBC 影片頁有「To play this video you need to enable JavaScript」，內文還在
    r"JavaScript is disabled|enable JavaScript (?:and cookies )?to (?:proceed|continue)"
    r"|disable (?:any |your )?ad.?blockers?|ad.?blocker (?:detected|is enabled)"
    r"|verify you are (?:a )?human|are you a robot|checking your browser|Just a moment\.\.\.|Access Denied"
    r"|請(?:關閉|停用)廣告(?:攔截|阻擋)|偵測到.{0,8}廣告(?:攔截|阻擋)|請(?:啟用|開啟) ?JavaScript", re.I)
BLOCKED_MAX = 1500
# 付費牆：只給開頭，接著是訂閱、登入的按鈕（經濟日報「展開全文…立即訂閱…免費註冊，立即解鎖全文」），
# 後面常是一長串推薦文章。牆前面的開頭留下，牆之後全部不要。
PAYWALL = re.compile(
    r"展開全文|解鎖全文|訂閱(?:會員)?(?:後|即可|才能)(?:閱讀|觀看|看)全文|付費會員(?:專屬|才能)"
    r"|subscribe (?:now )?to (?:continue|keep) reading|to continue reading,? (?:please )?(?:subscribe|log ?in|sign ?in)"
    r"|already a subscriber\??|this (?:article|content|story) is (?:only )?(?:for|available to) (?:paid )?subscribers",
    re.I)
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿가-힯]")
DETAIL = {"min_fulltext": 200, "min_summary": 300}  # 字數加權：中日韓一字算 2（資訊量約是英文字母的兩倍）


def is_paper(item: dict) -> bool:
    return item.get("source", "").startswith("arXiv")


def targets(items: list[dict]) -> list[dict]:
    """新聞全部；arXiv 只讀還沒有主會議資訊的前 PAPERS 篇（摘要已經是內容，讀全文只為了作者機構）。"""
    picked: list[dict] = []
    papers = 0
    for item in sorted(items, key=lambda i: i.get("rank", 0), reverse=True):
        if not is_paper(item):
            picked.append(item)
        elif (item.get("venue") or {}).get("status") != "main" and papers < PAPERS:
            picked.append(item)
            papers += 1
    return picked


def urls_for(item: dict) -> list[str]:
    url = item["url"]
    if is_paper(item) and "/abs/" in url:
        return [url.replace("/abs/", "/html/"), url]
    return [url]


def text_of(page: str, paragraphs: bool = True) -> str:
    """有 <article> 或 <main> 就只取它，否則取 <body>；去掉選單、頁首頁尾這類區塊。
    paragraphs：優先只接 <p> 段落（分享按鈕、影片提示、相關連結通常不在 <p> 裡）。
    論文要關掉：arXiv HTML 開頭的作者機構不在 <p> 裡。"""
    m = MAIN.search(page)
    body = DROP.sub(" ", m.group(2) if m else re.split(r"<body\b[^>]*>", page, maxsplit=1, flags=re.I)[-1])
    paras = [t for p in PARA.findall(body) if len(t := html.unescape(fetch._clean(p, CHARS))) >= MIN_PARA]
    if paragraphs and len(text := " ".join(paras)) >= MIN_PARAS_TEXT:
        return text[:CHARS]
    return JUNK.sub(" ", html.unescape(fetch._clean(JUNK.sub(" ", body), CHARS * 2)))[:CHARS]


def unwall(text: str) -> tuple[str, str | None]:
    """回傳 (可用的內文, 擋下的原因)。擋板頁整頁不要；付費牆只留牆前面。"""
    if len(text) <= BLOCKED_MAX and BLOCKED.search(text):
        return "", "被擋：頁面要求開 JavaScript、關掉廣告攔截或驗證身分"
    if m := PAYWALL.search(text):
        return text[:m.start()].rstrip(), "付費牆"
    return text, None


def weight(text: str) -> int:
    """長度，中日韓字算兩倍。"""
    return len(text) + len(CJK.findall(text))


def _body(text: str, title: str) -> str:
    """去掉開頭重複的標題（RSS 摘要常常就是標題、原文開頭常先印一次標題）。"""
    text = (text or "").strip()
    title = (title or "").strip()
    for _ in range(2):  # 經濟日報的頁面開頭是「原本--> 標題」
        i = text.find(title) if title else -1
        if 0 <= i <= 20:
            text = text[i + len(title):].strip()
    return text


def lacks_detail(item: dict, cfg: dict | None = None) -> str | None:
    """沒有足夠內容就回傳原因，有就回 None。原文（擋板之外）夠長，或摘要夠長，都算有內容。"""
    if is_paper(item):
        return None
    cfg = {**DETAIL, **(cfg or {})}
    title = item.get("title", "")
    if weight(_body(item.get("fulltext") or "", title)) >= cfg["min_fulltext"]:
        return None
    if weight(_body(item.get("summary") or "", title)) >= cfg["min_summary"]:
        return None
    why = item.get("fulltext_error")
    return f"摘要太短，原文抓不到（{why}）" if why else "摘要太短，原文也沒有內容"


def _getter(config: dict) -> Callable[[str], str]:
    req = {**config.get("request", {}), "retries": 0, "timeout": 15}
    return lambda url: fetch._http_get(url, req).decode("utf-8", "replace")


def fetch_one(item: dict, get: Callable[[str], str], partial_min: int) -> None:
    """就地加上 fulltext（付費牆只有開頭時另加 fulltext_note）或 fulltext_error。"""
    error = None
    for url in urls_for(item):
        try:
            text, why = unwall(text_of(get(url), paragraphs=not is_paper(item)))
        except Exception as exc:
            error = str(exc)
            continue
        if why and weight(_body(text, item.get("title", ""))) < partial_min:
            error = why
            continue
        if text:
            item["fulltext"] = text
            if why:
                item["fulltext_note"] = f"{why}：只有開頭"
            return
        error = "頁面沒有文字"
    item["fulltext_error"] = error


def enrich(items: list[dict], config: dict, get: Callable[[str], str] | None = None) -> None:
    """就地加上 fulltext 或 fulltext_error。已經抓過的（有其中一個欄位）不重抓。"""
    get = get or _getter(config)
    partial_min = {**DETAIL, **config.get("detail", {})}["min_summary"]  # 付費牆前的開頭要和摘要一樣長才算數
    req = config.get("request", {})

    def host(items: list[dict]) -> None:
        """同一網域依序、守 request 的間隔（現在每則新聞都抓，一個網域一次十幾則，不要同時打）。"""
        last_hit: dict[str, float] = {}
        for item in items:
            name = urlparse(item["url"]).hostname or ""
            time.sleep(fetch.wait_seconds(name, last_hit, req, time.monotonic()))
            fetch_one(item, get, partial_min)
            last_hit[name] = time.monotonic()

    picked = [i for i in targets(items) if "fulltext" not in i and "fulltext_error" not in i]
    if not picked:
        return
    groups: dict[str, list[dict]] = {}
    for item in picked:
        groups.setdefault(urlparse(item["url"]).hostname or "", []).append(item)
    with metrics.timed("enrich", items=len(picked)) as m, \
            ThreadPoolExecutor(max_workers=max(1, min(fetch.PARALLEL_HOSTS, len(groups)))) as pool:
        list(pool.map(host, groups.values()))
        failed = sum("fulltext_error" in i for i in picked)
        m.update(failed=failed)
    print(f"[enrich] 補原文 {len(picked) - failed}/{len(picked)} 則", file=sys.stderr)


def settle(items: list[dict], reserve: list[dict], config: dict,
           get: Callable[[str], str] | None = None) -> tuple[list[dict], list[dict]]:
    """補原文，拿掉內容不足的項目；空出來的名額由同主題的候補（reserve，沒進配額的）依 rank 補上。
    搜尋補來的不補位（它們本來就是額外的）。回傳 (留下的, 拿掉的)，拿掉的加上 dropped_reason。"""
    get = get or _getter(config)
    cfg = config.get("detail", {})
    enrich(items, config, get)
    kept, dropped = [], []
    for item in items:
        if why := lacks_detail(item, cfg):
            dropped.append({**item, "dropped_reason": why})
        else:
            kept.append(item)
    need = Counter(i["topic"] for i in dropped if i.get("origin") != "search")
    pool = sorted(reserve, key=lambda i: i.get("rank", 0), reverse=True)
    while need and pool:
        batch, rest = [], []
        for item in pool:
            (batch if need[item["topic"]] > sum(b["topic"] == item["topic"] for b in batch) else rest).append(item)
        if not batch:
            break
        pool = rest
        enrich(batch, config, get)
        for item in batch:
            if not lacks_detail(item, cfg):
                kept.append(item)
                need[item["topic"]] -= 1
        need = +need
    kept.sort(key=lambda i: i.get("rank", 0), reverse=True)
    return kept, dropped


def dropped_note(dropped: list[dict]) -> str | None:
    """給 curated errors 的一句話，writer 寫進本期備註。"""
    if not dropped:
        return None
    by = Counter(i["source"] for i in dropped)
    return (f"{len(dropped)} 則原文被擋或抓不到、摘要又太短，沒有收錄：" +
            "、".join(f"{s} {n}" for s, n in by.most_common()))
