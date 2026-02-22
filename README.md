# AI News Reader

ブラウザ上でAI関連ニュースを取得・要約できる Streamlit Web アプリです。

## 機能

- 複数のRSSフィード（TechCrunch AI / The Verge AI / ITmedia AI+ / NHK 科学・技術 / Gigazine）からニュースを取得
- 英語記事を日本語に自動要約（Claude API または Google Translate）
- フィード選択・表示件数をサイドバーで調整
- 結果をテキストファイルとしてダウンロード可能

## セットアップ

```bash
pip install -r requirements.txt
```

## 起動方法

```bash
python -m streamlit run ai_news_app.py
```

ブラウザで `http://localhost:8501` が開きます。

## 要約エンジン

| エンジン | 条件 |
|----------|------|
| Claude API (Haiku) | 環境変数 `ANTHROPIC_API_KEY` を設定 |
| Google Translate | `deep-translator` インストール済みの場合（APIキー不要） |

### Claude API を使う場合

```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...

# Mac / Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

## 対応フィード

| フィード名 | 言語 |
|------------|------|
| TechCrunch AI | 英語 |
| The Verge AI | 英語 |
| ITmedia AI+ | 日本語 |
| NHK 科学・技術 | 日本語 |
| Gigazine | 日本語 |
