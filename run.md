# 起動方法

依存パッケージのインストール:

```powershell
pip install -r requirements.txt
```

## 1. バックエンド（FastAPI）を起動

ターミナル1で実行:

```powershell
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

APIドキュメント: http://127.0.0.1:8000/docs

## 2. フロントエンド（Streamlit）を起動

ターミナル2で実行:

```powershell
streamlit run app.py
```

## 使い方

入力欄には次のどちらでも入力できます。

- **企業HPのURL**（例: `https://example.co.jp`）→ 直接クロール
- **企業名・自然言語**（例: `株式会社サンプル`）→ Web検索で公式HPを特定してから解析

解析すると、事業概要・推定業種・キーワードを分析し、関連度と推薦理由付きで補助金候補を提示します。

## 構成

```
hojokin/
├── app.py              # Streamlit フロントエンド（FastAPIをHTTPで呼び出す）
├── backend/
│   └── main.py         # FastAPI バックエンド（/api/analyze, /api/search）
└── modules/
    ├── search.py       # 企業名→公式HP特定（DuckDuckGo）
    ├── crawler.py      # HPクロール＆キーワード抽出＆概要/業種分析
    ├── jgrants.py      # 補助金データ
    └── matcher.py      # マッチングロジック（概要・業種を加味）
```

フロントとバックの連携先は環境変数 `BACKEND_URL` で変更可能（既定: http://127.0.0.1:8000）。
