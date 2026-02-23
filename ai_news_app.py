import urllib.request
import xml.etree.ElementTree as ET
import html
import re
import os
import time
from datetime import datetime

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

        status_area.empty()
        st.session_state["results"] = results
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

        st.markdown(
            f"**合計 {len(results)} 件** "
            f"（英語 {en_count} 件 / 日本語 {ja_count} 件）"
            + (f" ｜ 要約: {used_engine}" if en_count > 0 else "")
        )

        # フィードごとにタブ表示
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
