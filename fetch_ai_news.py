#!/usr/bin/env python3
"""
fetch_ai_news.py ― AI ニュース自動取得スクリプト

タスクスケジューラなどから定期実行し、
取得結果を ai-news-reader/news_data.json に追記保存する。
"""

import urllib.request
import xml.etree.ElementTree as ET
import html
import re
import os
import sys
import io
import json
import time
import logging
from datetime import datetime, timedelta

# Windows タスクスケジューラ実行時の文字化け対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 定数・設定
# ---------------------------------------------------------------------------

FEEDS = [
    ("TechCrunch AI",  "https://techcrunch.com/category/artificial-intelligence/feed/",     "en"),
    ("The Verge AI",   "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "en"),
    ("ITmedia AI+",    "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",                      "ja"),
    ("NHK 科学・技術", "https://news.web.nhk.or.jp/n-data/conf/na/rss/cat0.xml",            "ja"),
    ("Gigazine",       "https://gigazine.net/news/rss_2.0/",                                "ja"),
]

OUTPUT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai-news-reader")
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "news_data.json")
LOG_FILE    = os.path.join(OUTPUT_DIR, "fetch_news.log")

MAX_ITEMS_PER_FEED = 10   # フィードあたり最大取得件数
KEEP_DAYS          = 30   # JSON に保持する最大日数

# ---------------------------------------------------------------------------
# 重要度キーワード（スコア付き）
# ---------------------------------------------------------------------------

IMPORTANCE_KEYWORDS: dict[str, int] = {
    # 高重要度 (3点)
    "openai": 3, "gpt-4": 3, "gpt-5": 3, "gpt4": 3, "gpt5": 3,
    "anthropic": 3, "claude": 3, "gemini": 3, "grok": 3,
    "規制": 3, "法律": 3, "法案": 3, "禁止": 3, "ban": 3,
    "regulation": 3, "policy": 3, "政策": 3, "executive order": 3,
    "billion": 3, "兆円": 3, "trillion": 3,
    "買収": 3, "acquisition": 3, "merger": 3,
    "ipo": 3, "上場": 3, "破産": 3, "bankruptcy": 3,
    # 中重要度 (2点)
    "ai": 2, "artificial intelligence": 2, "人工知能": 2,
    "google": 2, "microsoft": 2, "meta": 2, "apple": 2, "amazon": 2,
    "chatgpt": 2, "llm": 2, "大規模言語モデル": 2,
    "launch": 2, "release": 2, "リリース": 2, "発表": 2, "公開": 2,
    "投資": 2, "funding": 2, "million": 2, "億円": 2,
    "breakthrough": 2, "革新": 2, "最新": 2,
    "nvidia": 2, "半導体": 2, "chip": 2,
    # 低重要度 (1点)
    "robot": 1, "ロボット": 1, "automation": 1, "自動化": 1,
    "data": 1, "データ": 1, "privacy": 1, "プライバシー": 1,
    "security": 1, "セキュリティ": 1, "research": 1, "研究": 1,
    "startup": 1, "スタートアップ": 1, "agent": 1, "エージェント": 1,
}

# ---------------------------------------------------------------------------
# カテゴリ定義（キーワードベース）
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, list[str]] = {
    "OpenAI・ChatGPT": [
        "openai", "chatgpt", "gpt-4", "gpt-5", "gpt4", "gpt5",
        "sam altman", "o1", "o3", "sora", "dall-e", "whisper",
    ],
    "Google・Gemini": [
        "google", "gemini", "deepmind", "bard", "vertex ai",
        "google ai", "sundar pichai", "waymo", "google deepmind",
    ],
    "AI規制・政策": [
        "規制", "法律", "法案", "政策", "policy", "regulation",
        "government", "ban", "禁止", "eu ai act", "executive order",
        "congress", "議会", "governance", "法整備", "倫理",
    ],
    "ロボット・ハードウェア": [
        "robot", "ロボット", "hardware", "chip", "nvidia", "semiconductor",
        "半導体", "physical ai", "boston dynamics", "humanoid", "drone",
        "ドローン", "gpu", "tpu", "h100", "blackwell",
    ],
}

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logger = logging.getLogger("fetch_ai_news")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

# ---------------------------------------------------------------------------
# 要約エンジン
# ---------------------------------------------------------------------------

def build_summarizer(logger: logging.Logger):
    """(engine_name, summarize_fn | None) を返す。英語タイトル専用。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)

            def _claude(title: str) -> str:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    messages=[{"role": "user", "content": (
                        "以下の英語ニュースタイトルを日本語で1〜2行に要約してください。"
                        "記事の内容と意義を簡潔に説明してください。要約文のみ出力し、前置きは不要です。\n\n"
                        f"タイトル: {title}"
                    )}],
                )
                return msg.content[0].text.strip()

            logger.info("要約エンジン: Claude API (Haiku)")
            return "Claude API", _claude
        except Exception as e:
            logger.warning(f"Claude API 初期化失敗: {e}")

    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="en", target="ja")
        logger.info("要約エンジン: Google Translate (フォールバック)")
        return "Google Translate", lambda t: tr.translate(t)
    except Exception as e:
        logger.warning(f"Google Translate 利用不可: {e}")
        return None, None

# ---------------------------------------------------------------------------
# RSSフィード取得（タイトル + URL + 抜粋）
# ---------------------------------------------------------------------------

def fetch_items(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    root = ET.fromstring(data)

    ns = {}
    items = root.findall(".//item")
    is_atom = False
    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//atom:entry", ns)
        is_atom = True

    result = []
    for item in items[:MAX_ITEMS_PER_FEED]:
        if is_atom:
            raw_title = item.findtext("atom:title", namespaces=ns) or "(タイトルなし)"
            link_el = item.find("atom:link", ns)
            raw_link = link_el.get("href", "") if link_el is not None else ""
            raw_desc = (
                item.findtext("atom:summary", namespaces=ns)
                or item.findtext("atom:content", namespaces=ns)
                or ""
            )
        else:
            raw_title = item.findtext("title") or "(タイトルなし)"
            raw_link = item.findtext("link") or ""
            raw_desc = item.findtext("description") or ""

        title = html.unescape(raw_title.strip())
        link = raw_link.strip()
        desc = re.sub(r"<[^>]+>", "", html.unescape(raw_desc)).strip()
        if len(desc) > 200:
            desc = desc[:200] + "…"

        result.append({"title": title, "url": link, "excerpt": desc})
    return result

# ---------------------------------------------------------------------------
# カテゴリ分類・スコアリング
# ---------------------------------------------------------------------------

def classify_by_keyword(title: str, excerpt: str) -> str:
    text = (title + " " + excerpt).lower()
    for cat_name, keywords in CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return cat_name
    return "その他"


def score_article(title: str, excerpt: str) -> int:
    text = (title + " " + excerpt).lower()
    return sum(pts for kw, pts in IMPORTANCE_KEYWORDS.items() if kw in text)

# ---------------------------------------------------------------------------
# JSON 追記保存
# ---------------------------------------------------------------------------

def load_json() -> dict:
    if not os.path.exists(OUTPUT_JSON):
        return {"articles": []}
    try:
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"articles": []}


def append_articles(new_articles: list[dict], logger: logging.Logger) -> int:
    """news_data.json に新規記事を追記。重複除去・古いデータ削除を行い、追記件数を返す。"""
    data = load_json()

    # KEEP_DAYS 超の古いデータを削除
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    before = len(data["articles"])
    data["articles"] = [
        a for a in data["articles"] if a.get("fetched_date", "") >= cutoff
    ]
    removed = before - len(data["articles"])
    if removed:
        logger.info(f"古いデータを削除: {removed} 件（{KEEP_DAYS}日超）")

    # 本日分の重複チェック用セット
    today = datetime.now().strftime("%Y-%m-%d")
    existing_urls = {
        a["url"] for a in data["articles"]
        if a.get("fetched_date") == today and a.get("url")
    }
    existing_titles = {
        a["title"] for a in data["articles"]
        if a.get("fetched_date") == today and not a.get("url")
    }

    added = 0
    for art in new_articles:
        url = art.get("url", "")
        title = art.get("title", "")
        if url and url in existing_urls:
            continue
        if not url and title in existing_titles:
            continue
        data["articles"].append(art)
        if url:
            existing_urls.add(url)
        else:
            existing_titles.add(title)
        added += 1

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return added

# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def main() -> int:
    logger = setup_logging()
    logger.info("=" * 50)
    logger.info("AI ニュース自動取得 開始")

    engine_name, summarize_en = build_summarizer(logger)
    today = datetime.now().strftime("%Y-%m-%d")

    new_articles: list[dict] = []
    fetch_errors = 0
    total_fetched = 0

    for feed_name, url, lang in FEEDS:
        label = "英語" if lang == "en" else "日本語"
        logger.info(f"取得中: {feed_name} ({label})")
        try:
            items = fetch_items(url)
        except Exception as e:
            logger.error(f"フィード取得エラー [{feed_name}]: {e}")
            fetch_errors += 1
            continue

        for item in items:
            title   = item["title"]
            url_link = item["url"]
            excerpt  = item["excerpt"]

            summary = ""
            if lang == "en" and summarize_en:
                try:
                    summary = summarize_en(title)
                    time.sleep(0.3)   # レート制限対策
                except Exception as e:
                    logger.warning(f"要約エラー ({title[:30]}…): {e}")

            new_articles.append({
                "fetched_date": today,
                "feed":         feed_name,
                "lang":         lang,
                "title":        title,
                "summary":      summary,
                "url":          url_link,
                "excerpt":      excerpt,
                "category":     classify_by_keyword(title, excerpt),
                "score":        score_article(title, excerpt),
            })
            total_fetched += 1

        logger.info(f"  → {len(items)} 件取得完了")

    # JSON に追記保存
    added = append_articles(new_articles, logger)

    logger.info("-" * 50)
    logger.info(
        f"取得: {total_fetched} 件 ｜ 新規保存: {added} 件 ｜ "
        f"エラー: {fetch_errors}/{len(FEEDS)} フィード"
    )
    logger.info(f"保存先: {OUTPUT_JSON}")
    logger.info("AI ニュース自動取得 完了")
    logger.info("=" * 50)

    # 全フィード失敗時のみ終了コード 1（タスクスケジューラでエラー検知可能）
    return 1 if fetch_errors == len(FEEDS) else 0


if __name__ == "__main__":
    sys.exit(main())
