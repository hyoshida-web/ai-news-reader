import urllib.request
import xml.etree.ElementTree as ET
import html
import re
import os
import time
from datetime import datetime, timedelta
import json
from collections import Counter

import streamlit as st

# ---------------------------------------------------------------------------
# フィード定義
# ---------------------------------------------------------------------------

FEEDS = [
    # (フィード名, URL, 言語)  lang="en" → 日本語要約 / lang="ja" → そのまま表示
    ("TechCrunch AI",  "https://techcrunch.com/category/artificial-intelligence/feed/",     "en"),
    ("The Verge AI",   "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "en"),
    ("ITmedia AI+",    "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",                      "ja"),
    ("NHK 科学・技術", "https://news.web.nhk.or.jp/n-data/conf/na/rss/cat0.xml",            "ja"),
    ("Gigazine",       "https://gigazine.net/news/rss_2.0/",                                "ja"),
]

# ---------------------------------------------------------------------------
# 重要度キーワード（スコア付き）
# ---------------------------------------------------------------------------

IMPORTANCE_KEYWORDS: dict[str, int] = {
    # 高重要度 (3点): 主要AIプレイヤー・規制・大型資金
    "openai": 3, "gpt-4": 3, "gpt-5": 3, "gpt4": 3, "gpt5": 3,
    "anthropic": 3, "claude": 3, "gemini": 3, "grok": 3,
    "規制": 3, "法律": 3, "法案": 3, "禁止": 3, "ban": 3,
    "regulation": 3, "policy": 3, "政策": 3, "executive order": 3,
    "billion": 3, "兆円": 3, "trillion": 3,
    "買収": 3, "acquisition": 3, "merger": 3,
    "ipo": 3, "上場": 3, "破産": 3, "bankruptcy": 3,
    # 中重要度 (2点): AI全般・大手企業・リリース・投資
    "ai": 2, "artificial intelligence": 2, "人工知能": 2,
    "google": 2, "microsoft": 2, "meta": 2, "apple": 2, "amazon": 2,
    "chatgpt": 2, "llm": 2, "大規模言語モデル": 2,
    "launch": 2, "release": 2, "リリース": 2, "発表": 2, "公開": 2,
    "投資": 2, "funding": 2, "million": 2, "億円": 2,
    "breakthrough": 2, "革新": 2, "最新": 2,
    "nvidia": 2, "半導体": 2, "chip": 2,
    # 低重要度 (1点): 関連技術・一般トピック
    "robot": 1, "ロボット": 1, "automation": 1, "自動化": 1,
    "data": 1, "データ": 1, "privacy": 1, "プライバシー": 1,
    "security": 1, "セキュリティ": 1, "research": 1, "研究": 1,
    "startup": 1, "スタートアップ": 1, "agent": 1, "エージェント": 1,
}

# ---------------------------------------------------------------------------
# カテゴリ定義
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, dict] = {
    "OpenAI・ChatGPT": {
        "keywords": ["openai", "chatgpt", "gpt-4", "gpt-5", "gpt4", "gpt5",
                     "sam altman", "o1", "o3", "sora", "dall-e", "whisper"],
        "icon": "🤖",
        "color": "#10a37f",
    },
    "Google・Gemini": {
        "keywords": ["google", "gemini", "deepmind", "bard", "vertex ai",
                     "google ai", "sundar pichai", "waymo", "google deepmind"],
        "icon": "🔵",
        "color": "#4285f4",
    },
    "AI規制・政策": {
        "keywords": ["規制", "法律", "法案", "政策", "policy", "regulation",
                     "government", "ban", "禁止", "eu ai act", "executive order",
                     "congress", "議会", "governance", "法整備", "倫理"],
        "icon": "⚖️",
        "color": "#ea4335",
    },
    "ロボット・ハードウェア": {
        "keywords": ["robot", "ロボット", "hardware", "chip", "nvidia", "semiconductor",
                     "半導体", "physical ai", "boston dynamics", "humanoid", "drone",
                     "ドローン", "gpu", "tpu", "h100", "blackwell"],
        "icon": "🦾",
        "color": "#ff6d00",
    },
    "その他": {
        "keywords": [],
        "icon": "📌",
        "color": "#666666",
    },
}

CATEGORY_NAMES = list(CATEGORIES.keys())

# 履歴JSONの保存先（スクリプトと同じディレクトリ）
HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_history.json")


def classify_articles_with_claude(articles: list[dict], client) -> list[str]:
    """Claude API でニュース記事を一括カテゴリ分類する。"""
    category_list = "\n".join([f"- {name}" for name in CATEGORY_NAMES])
    items_text = "\n".join(
        [f"{i + 1}. {a['title']}" for i, a in enumerate(articles)]
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": (
                "以下のニュース記事を、指定されたカテゴリのいずれかに分類してください。\n\n"
                f"カテゴリ一覧:\n{category_list}\n\n"
                f"記事一覧:\n{items_text}\n\n"
                "各記事を「番号: カテゴリ名」の形式で1行ずつ出力してください。"
                "カテゴリ名はリストにある文字列と完全一致させてください。前置きは不要です。"
            ),
        }],
    )
    response_text = msg.content[0].text.strip()
    categories: dict[int, str] = {}
    for line in response_text.split("\n"):
        if ":" in line:
            parts = line.split(":", 1)
            try:
                idx = int(parts[0].strip()) - 1
                cat = parts[1].strip()
                categories[idx] = cat if cat in CATEGORIES else "その他"
            except ValueError:
                pass
    return [categories.get(i, "その他") for i in range(len(articles))]


def classify_article_by_keyword(title: str, excerpt: str) -> str:
    """キーワードマッチングによるカテゴリ分類（フォールバック）。"""
    text = (title + " " + excerpt).lower()
    for cat_name, cat_info in CATEGORIES.items():
        if cat_name == "その他":
            continue
        if any(kw in text for kw in cat_info["keywords"]):
            return cat_name
    return "その他"


@st.cache_resource
def build_categorizer():
    """カテゴリ分類関数を返す。署名: fn(articles: list[dict]) -> list[str]"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            return lambda articles: classify_articles_with_claude(articles, client)
        except Exception:
            pass
    return lambda articles: [
        classify_article_by_keyword(a["title"], a["excerpt"]) for a in articles
    ]


# ---------------------------------------------------------------------------
# 要約エンジン
# ---------------------------------------------------------------------------

def summarize_with_claude(title: str, client) -> str:
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                "以下の英語ニュースタイトルを日本語で1〜2行に要約してください。"
                "記事の内容と意義を簡潔に説明してください。要約文のみ出力し、前置きは不要です。\n\n"
                f"タイトル: {title}"
            ),
        }],
    )
    return msg.content[0].text.strip()


def summarize_with_translate(title: str, translator) -> str:
    try:
        return translator.translate(title)
    except Exception as e:
        return f"（翻訳エラー: {e}）"


@st.cache_resource
def build_summarizer():
    """利用可能な要約エンジンを (engine_name, fn) で返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            return "Claude API (Haiku)", lambda t: summarize_with_claude(t, client)
        except Exception as e:
            st.warning(f"Claude API 初期化失敗: {e}")

    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="en", target="ja")
        return "Google Translate", lambda t: summarize_with_translate(t, tr)
    except Exception as e:
        return None, None


def summarize_top3_with_claude(title: str, excerpt: str, client) -> str:
    """Top3記事を3行で日本語要約する（Claude API使用）。"""
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                "以下のニュース記事を日本語で必ず3行に要約してください。\n"
                "① 何が起きたか（事実を簡潔に）\n"
                "② なぜ重要か（業界・社会への意義）\n"
                "③ 今後どうなるか（影響・展望）\n"
                "各行を「①」「②」「③」で始め、前置きなしで出力してください。\n\n"
                f"タイトル: {title}\n"
                f"抜粋: {excerpt or 'なし'}"
            ),
        }],
    )
    return msg.content[0].text.strip()


def summarize_top3_fallback(title: str, excerpt: str, summary: str | None) -> str:
    """Claude APIなしのTop3要約フォールバック。"""
    lines = []
    base = summary or title
    lines.append(f"① {base}")
    if excerpt:
        sentences = [s.strip() for s in re.split(r"[。．.!！?\?]", excerpt) if s.strip()]
        if len(sentences) >= 1:
            lines.append(f"② {sentences[0]}")
        if len(sentences) >= 2:
            lines.append(f"③ {sentences[1]}")
    while len(lines) < 3:
        lines.append("")
    return "\n\n".join(lines[:3])


@st.cache_resource
def build_top3_summarizer():
    """Top3用3行要約関数を返す。署名: fn(title, excerpt, summary) -> str"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            return lambda title, excerpt, _: summarize_top3_with_claude(title, excerpt, client)
        except Exception:
            pass
    return summarize_top3_fallback

# ---------------------------------------------------------------------------
# RSSフィード取得
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
    for item in items:
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
# 重要度スコアリング & Top3選定
# ---------------------------------------------------------------------------

def score_article(title: str, excerpt: str) -> int:
    """タイトル＋抜粋に含まれるキーワードをもとに重要度スコアを計算する。"""
    text = (title + " " + excerpt).lower()
    return sum(
        points for kw, points in IMPORTANCE_KEYWORDS.items() if kw in text
    )


def get_top3(results: list) -> list:
    """resultsから重要度上位3件を返す。同スコアは先着順。"""
    scored = [
        (score_article(title, excerpt), row)
        for row in results
        for _, lang, title, summary, url_link, excerpt in [row]
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(score, row) for score, row in scored[:3]]


# ---------------------------------------------------------------------------
# 履歴保存・週次レポート
# ---------------------------------------------------------------------------

def save_to_history(results: list, categories: list[str]) -> int:
    """取得結果をローカルJSONに追記保存する。URLベースで重複除去し、新規保存件数を返す。"""
    today = datetime.now().strftime("%Y-%m-%d")

    existing: dict = {"articles": []}
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {"articles": []}

    # 30日超の古いデータを削除
    cutoff_old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    existing["articles"] = [
        a for a in existing["articles"] if a.get("fetched_date", "") >= cutoff_old
    ]

    # 今日分の重複チェック用セット
    today_urls = {
        a["url"] for a in existing["articles"]
        if a.get("fetched_date") == today and a.get("url")
    }
    today_titles = {
        a["title"] for a in existing["articles"]
        if a.get("fetched_date") == today and not a.get("url")
    }

    new_articles = []
    for i, (feed_name, lang, title, summary, url_link, excerpt) in enumerate(results):
        if title.startswith("フィード取得エラー"):
            continue
        if url_link and url_link in today_urls:
            continue
        if not url_link and title in today_titles:
            continue
        cat = categories[i] if i < len(categories) else "その他"
        new_articles.append({
            "fetched_date": today,
            "feed": feed_name,
            "lang": lang,
            "title": title,
            "summary": summary or "",
            "url": url_link,
            "excerpt": excerpt,
            "category": cat,
            "score": score_article(title, excerpt),
        })
        if url_link:
            today_urls.add(url_link)
        else:
            today_titles.add(title)

    existing["articles"].extend(new_articles)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return len(new_articles)


def load_history(days: int = 7) -> list[dict]:
    """過去N日分の記事リストを返す。"""
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [a for a in data.get("articles", []) if a.get("fetched_date", "") >= cutoff]


def generate_weekly_report(days: int = 7) -> dict | None:
    """過去N日の履歴からレポートデータを生成する。"""
    articles = load_history(days)
    if not articles:
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # カテゴリ別集計
    cat_counter: Counter = Counter(a.get("category", "その他") for a in articles)
    by_category = [(cat, cat_counter.get(cat, 0)) for cat in CATEGORY_NAMES]
    by_category_sorted = sorted(
        [(c, n) for c, n in by_category if n > 0], key=lambda x: x[1], reverse=True
    )

    # TOP5（スコア降順）
    top5 = sorted(articles, key=lambda a: a.get("score", 0), reverse=True)[:5]

    # 頻出キーワード（IMPORTANCE_KEYWORDS の語彙で出現記事数カウント）
    kw_counter: Counter = Counter()
    for a in articles:
        text = (a.get("title", "") + " " + a.get("excerpt", "")).lower()
        for kw in IMPORTANCE_KEYWORDS:
            if kw in text:
                kw_counter[kw] += 1
    top_keywords = kw_counter.most_common(10)

    dates = sorted(set(a.get("fetched_date", "") for a in articles if a.get("fetched_date")))

    return {
        "total": len(articles),
        "period_from": cutoff,
        "period_to": today,
        "dates": dates,
        "by_category": by_category_sorted,
        "top5": top5,
        "keywords": top_keywords,
    }


def build_weekly_report_text(report: dict) -> str:
    """週次レポートのテキスト版を生成する。"""
    lines = [
        "=" * 60,
        "  AI ニュース 週次レポート",
        f"  集計期間: {report['period_from']} ～ {report['period_to']}",
        f"  生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
        f"■ 今週の総記事数: {report['total']} 件",
        f"  取得日: {', '.join(report['dates'])}",
        "",
        "■ カテゴリ別記事数ランキング",
        "-" * 40,
    ]
    for rank, (cat, count) in enumerate(report["by_category"], 1):
        icon = CATEGORIES[cat]["icon"]
        lines.append(f"  {rank}位 {icon} {cat}: {count} 件")
    lines += ["", "■ 今週の重要ニュース TOP5", "-" * 40]
    for rank, art in enumerate(report["top5"], 1):
        lines.append(f"  {rank}. [{art.get('fetched_date', '')}] {art.get('title', '')}")
        if art.get("summary"):
            lines.append(f"     要約: {art['summary']}")
        if art.get("url"):
            lines.append(f"     URL: {art['url']}")
        lines.append(f"     重要度スコア: {art.get('score', 0)}")
        lines.append("")
    lines += ["■ 頻出キーワード ランキング", "-" * 40]
    for rank, (kw, count) in enumerate(report["keywords"], 1):
        lines.append(f"  {rank:2d}位  {kw}: {count} 件")
    lines += ["", "=" * 60]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ニュース取得処理（リアルタイム表示）
# ---------------------------------------------------------------------------

def fetch_all_news(engine_name, summarize_en, status_placeholder):
    """全フィードを取得してリストを返す。進捗はstatus_placeholderに表示。"""
    results = []  # (feed_name, lang, title, summary|None, url, excerpt)

    for feed_idx, (feed_name, url, lang) in enumerate(FEEDS):
        label = "英語" if lang == "en" else "日本語"
        status_placeholder.info(f"取得中... [{feed_idx + 1}/{len(FEEDS)}] {feed_name} ({label})")

        try:
            items = fetch_items(url)
        except Exception as e:
            results.append((feed_name, lang, f"フィード取得エラー: {e}", None, "", ""))
            continue

        for item in items:
            title, url_link, excerpt = item["title"], item["url"], item["excerpt"]
            if lang == "en" and summarize_en:
                summary = summarize_en(title)
                time.sleep(0.3)
            else:
                summary = None
            results.append((feed_name, lang, title, summary, url_link, excerpt))

    return results


# ---------------------------------------------------------------------------
# サマリーテキスト生成
# ---------------------------------------------------------------------------

def build_summary_text(results, engine_name):
    en_count = sum(1 for r in results if r[1] == "en")
    ja_count = sum(1 for r in results if r[1] == "ja")

    lines = [
        "AI ニュース日本語要約",
        f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"英語記事: {en_count} 件（要約エンジン: {engine_name or '利用不可'}）",
        f"日本語記事: {ja_count} 件（タイトルそのまま）",
        "=" * 60,
    ]

    current_feed = None
    for feed_name, lang, title, summary, url_link, excerpt in results:
        if feed_name != current_feed:
            lines.append(f"\n■ {feed_name}")
            lines.append("-" * 60)
            current_feed = feed_name
        lines.append(f"【タイトル】{title}")
        if excerpt:
            lines.append(f"【抜粋】    {excerpt}")
        if summary is not None:
            lines.append(f"【要約】    {summary}")
        if url_link:
            lines.append(f"【URL】     {url_link}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="AI ニュースリーダー",
    page_icon="📰",
    layout="wide",
)

st.title("📰 AI ニュースリーダー")
st.caption("各種RSSフィードからAIニュースを取得し、英語記事を日本語に要約します。")

# --- サイドバー: フィード選択 ---
st.sidebar.header("設定")

feed_options = {f"{name} ({'英語' if lang == 'en' else '日本語'})": (name, url, lang)
                for name, url, lang in FEEDS}
selected_labels = st.sidebar.multiselect(
    "取得するフィード",
    options=list(feed_options.keys()),
    default=list(feed_options.keys()),
)
selected_feeds = [feed_options[label] for label in selected_labels]

max_items = st.sidebar.slider("フィードあたり最大件数", min_value=3, max_value=30, value=10)

# --- エンジン情報 ---
engine_name, summarize_en = build_summarizer()
summarize_top3 = build_top3_summarizer()
categorize = build_categorizer()

if engine_name:
    st.sidebar.success(f"要約エンジン: {engine_name}")
else:
    st.sidebar.error("要約エンジンが見つかりません。\nANTHROPIC_API_KEY を設定するか deep_translator をインストールしてください。")

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**ANTHROPIC_API_KEY** を環境変数に設定すると\nClaude API で高品質な要約を利用できます。"
)

# --- メイン: 取得ボタン ---
col1, col2 = st.columns([1, 4])
with col1:
    run_button = st.button("ニュースを取得", type="primary", use_container_width=True)
with col2:
    if "last_fetched" in st.session_state:
        st.caption(f"最終取得: {st.session_state['last_fetched']}")

status_area = st.empty()

# --- 取得実行 ---
if run_button:
    if not selected_feeds:
        st.warning("フィードを1つ以上選択してください。")
    else:
        with st.spinner("ニュースを取得・要約中です。しばらくお待ちください..."):
            # 選択フィードだけ処理
            results = []
            for feed_idx, (feed_name, url, lang) in enumerate(selected_feeds):
                label = "英語" if lang == "en" else "日本語"
                status_area.info(
                    f"取得中... [{feed_idx + 1}/{len(selected_feeds)}] {feed_name} ({label})"
                )
                try:
                    items = fetch_items(url)[:max_items]
                except Exception as e:
                    results.append((feed_name, lang, f"フィード取得エラー: {e}", None, "", ""))
                    continue

                for item in items:
                    title = item["title"]
                    url_link = item["url"]
                    excerpt = item["excerpt"]
                    if lang == "en" and summarize_en:
                        summary = summarize_en(title)
                        time.sleep(0.3)
                    else:
                        summary = None
                    results.append((feed_name, lang, title, summary, url_link, excerpt))

        # Top3選定と3行要約生成
        top3_data = []
        for score, row in get_top3(results):
            fn, lang, title, summary, url_link, excerpt = row
            summary3 = summarize_top3(title, excerpt, summary)
            top3_data.append({
                "score": score, "feed": fn, "lang": lang,
                "title": title, "summary": summary, "summary3": summary3,
                "url": url_link, "excerpt": excerpt,
            })

        # カテゴリ分類
        status_area.info("カテゴリ分類中...")
        article_inputs = [
            {"title": title, "excerpt": excerpt}
            for _, _, title, _, _, excerpt in results
        ]
        article_categories = categorize(article_inputs)

        # 履歴に保存
        status_area.info("履歴を保存中...")
        saved_count = save_to_history(results, article_categories)

        status_area.empty()
        st.session_state["results"] = results
        st.session_state["top3"] = top3_data
        st.session_state["categories"] = article_categories
        st.session_state["engine_name"] = engine_name
        st.session_state["last_fetched"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()

# --- 結果表示 ---
if "results" in st.session_state:
    results = st.session_state["results"]
    used_engine = st.session_state.get("engine_name", "不明")

    if not results:
        st.info("ニュースが見つかりませんでした。")
    else:
        en_count = sum(1 for r in results if r[1] == "en")
        ja_count = sum(1 for r in results if r[1] == "ja")

        # ── 今日の重要3本 ──────────────────────────────────────────────
        top3 = st.session_state.get("top3", [])
        if top3:
            st.markdown("---")
            st.markdown(
                "<h2 style='text-align:center; color:#d4a017;'>🏆 今日の重要ニュース TOP 3</h2>",
                unsafe_allow_html=True,
            )
            medals = ["🥇", "🥈", "🥉"]
            cols = st.columns(3)
            for col, medal, item in zip(cols, medals, top3):
                with col:
                    with st.container(border=True):
                        st.markdown(
                            f"<div style='font-size:1.6rem; text-align:center;'>{medal}</div>",
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f"**{item['title']}**",
                        )
                        st.caption(f"📰 {item['feed']}　｜　重要度スコア: {item['score']}")
                        st.markdown("---")
                        for line in item["summary3"].split("\n\n"):
                            if line.strip():
                                st.markdown(line.strip())
                        if item["url"]:
                            st.markdown(f"[元記事を読む →]({item['url']})")
            st.markdown("---")
        # ──────────────────────────────────────────────────────────────

        st.markdown(
            f"**合計 {len(results)} 件** "
            f"（英語 {en_count} 件 / 日本語 {ja_count} 件）"
            + (f" ｜ 要約: {used_engine}" if en_count > 0 else "")
        )

        # 表示モード切り替え
        view_mode = st.radio(
            "表示モード",
            options=["📰 メディア別", "🗂️ カテゴリ別"],
            horizontal=True,
            key="view_mode",
            label_visibility="collapsed",
        )

        article_categories = st.session_state.get("categories", [])

        # ── カテゴリ別表示 ──────────────────────────────────────────────
        if view_mode == "🗂️ カテゴリ別":
            # カテゴリ → 記事リスト のマッピングを構築
            cat_map: dict[str, list] = {name: [] for name in CATEGORY_NAMES}
            for i, (fn, lang, title, summary, url_link, excerpt) in enumerate(results):
                cat = article_categories[i] if i < len(article_categories) else "その他"
                cat_map[cat].append((lang, title, summary, url_link, excerpt, fn))

            for cat_name in CATEGORY_NAMES:
                articles_in_cat = cat_map[cat_name]
                if not articles_in_cat:
                    continue
                cat_info = CATEGORIES[cat_name]
                st.markdown(
                    f"<h3 style='color:{cat_info['color']};'>"
                    f"{cat_info['icon']} {cat_name} "
                    f"<span style='font-size:0.8rem; font-weight:normal;'>"
                    f"({len(articles_in_cat)} 件)</span></h3>",
                    unsafe_allow_html=True,
                )
                for i, (lang, title, summary, url_link, excerpt, feed_name) in enumerate(
                    articles_in_cat, 1
                ):
                    flag = "🌐" if lang == "en" else "🇯🇵"
                    with st.expander(f"{flag} {i}. {title}"):
                        st.caption(f"📰 {feed_name}")
                        if excerpt:
                            st.markdown("**記事抜粋**")
                            st.markdown(excerpt)
                        if summary:
                            st.markdown("**日本語要約**")
                            st.markdown(
                                f'<div style="color:#1a73e8; padding:4px 0;">→ {summary}</div>',
                                unsafe_allow_html=True,
                            )
                        if url_link:
                            st.markdown(f"[元記事を読む →]({url_link})")
                st.markdown("")

        # ── メディア別表示（既存） ──────────────────────────────────────
        else:
            feed_names = list(dict.fromkeys(r[0] for r in results))
            tabs = st.tabs(feed_names)

            for tab, feed_name in zip(tabs, feed_names):
                feed_results = [
                    (lang, title, summary, url_link, excerpt)
                    for fn, lang, title, summary, url_link, excerpt in results
                    if fn == feed_name
                ]
                with tab:
                    for i, (lang, title, summary, url_link, excerpt) in enumerate(feed_results, 1):
                        flag = "🌐" if lang == "en" else "🇯🇵"
                        with st.expander(f"{flag} {i}. {title}"):
                            if excerpt:
                                st.markdown("**記事抜粋**")
                                st.markdown(excerpt)
                            if summary:
                                st.markdown("**日本語要約**")
                                st.markdown(
                                    f'<div style="color:#1a73e8; padding:4px 0;">→ {summary}</div>',
                                    unsafe_allow_html=True,
                                )
                            if url_link:
                                st.markdown(f"[元記事を読む →]({url_link})")

        # ダウンロードボタン
        summary_text = build_summary_text(results, used_engine)
        filename = f"ai_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        st.download_button(
            label="テキストをダウンロード",
            data=summary_text.encode("utf-8"),
            file_name=filename,
            mime="text/plain",
        )

# ---------------------------------------------------------------------------
# 週次レポートセクション（常時表示）
# ---------------------------------------------------------------------------
st.markdown("---")
st.markdown("### 📊 週次レポート")

# 蓄積状況サマリー
if os.path.exists(HISTORY_PATH):
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as _f:
            _hist = json.load(_f)
        _all = _hist.get("articles", [])
        _dates = sorted(set(a.get("fetched_date", "") for a in _all if a.get("fetched_date")))
        st.caption(
            f"蓄積済み: {len(_all)} 件 ｜ 取得日数: {len(_dates)} 日分"
            + (f" ｜ 最終取得: {_dates[-1]}" if _dates else "")
        )
    except Exception:
        pass
else:
    st.caption("まだ履歴がありません。ニュースを取得すると自動で蓄積されます。")

col_rb1, col_rb2 = st.columns([1, 4])
with col_rb1:
    report_button = st.button("週次レポート生成", use_container_width=True)

if report_button:
    with st.spinner("過去7日分のデータを集計中..."):
        _report = generate_weekly_report()
    if _report is None:
        st.warning("過去7日分のデータがありません。先にニュースを取得してください。")
    else:
        st.session_state["weekly_report"] = _report

if "weekly_report" in st.session_state:
    report = st.session_state["weekly_report"]
    st.markdown(
        "<h3 style='color:#6b48ff;'>集計結果</h3>",
        unsafe_allow_html=True,
    )

    # ── メトリクス ──
    m1, m2, m3 = st.columns(3)
    m1.metric("今週の総記事数", f"{report['total']} 件")
    m2.metric("集計期間", f"{report['period_from']} ～ {report['period_to']}")
    m3.metric("取得日数", f"{len(report['dates'])} 日")
    st.markdown("")

    col_left, col_right = st.columns(2)

    # ── カテゴリ別ランキング ──
    with col_left:
        st.markdown("#### カテゴリ別記事数ランキング")
        rank_medals = ["🥇", "🥈", "🥉"]
        for rank, (cat, count) in enumerate(report["by_category"]):
            cat_info = CATEGORIES[cat]
            medal = rank_medals[rank] if rank < 3 else f"{rank + 1}位"
            bar_pct = int(count / report["total"] * 100) if report["total"] else 0
            st.markdown(
                f"{medal} **{cat_info['icon']} {cat}**　{count} 件　({bar_pct}%)"
            )
            st.progress(bar_pct / 100)

    # ── 頻出キーワード ──
    with col_right:
        st.markdown("#### 頻出キーワードランキング")
        if report["keywords"]:
            max_kw_count = report["keywords"][0][1]
            for rank, (kw, count) in enumerate(report["keywords"], 1):
                bar_pct = int(count / max_kw_count * 100) if max_kw_count else 0
                st.markdown(f"**{rank}位** `{kw}` — {count} 件")
                st.progress(bar_pct / 100)

    # ── 今週の重要ニュース TOP5 ──
    st.markdown("#### 今週の重要ニュース TOP5")
    top5_medals = ["🥇", "🥈", "🥉", "4位", "5位"]
    for rank, art in enumerate(report["top5"]):
        medal = top5_medals[rank]
        with st.expander(f"{medal} {art.get('title', '')}"):
            st.caption(
                f"📅 {art.get('fetched_date', '')}　｜　"
                f"📰 {art.get('feed', '')}　｜　"
                f"重要度スコア: {art.get('score', 0)}"
            )
            if art.get("summary"):
                st.markdown(
                    f'<div style="color:#1a73e8; padding:4px 0;">→ {art["summary"]}</div>',
                    unsafe_allow_html=True,
                )
            if art.get("url"):
                st.markdown(f"[元記事を読む →]({art['url']})")

    # ── テキストダウンロード ──
    report_text = build_weekly_report_text(report)
    report_filename = f"ai_news_weekly_{datetime.now().strftime('%Y%m%d')}.txt"
    st.download_button(
        label="週次レポートをダウンロード",
        data=report_text.encode("utf-8"),
        file_name=report_filename,
        mime="text/plain",
    )
