# デプロイ手順（フロント / バック 別々）

このアプリは2プロセス構成です。フロントとバックを別サービスにデプロイします。

- バックエンド: FastAPI（`backend/main.py`）→ Render
- フロントエンド: Streamlit（`app.py`）→ Streamlit Community Cloud

以下は1リポジトリ（モノレポ）のまま、それぞれのサービスに同じリポジトリを連携する前提の手順です。

---

## 0. 前提

- GitHubにこのリポジトリをpush済みであること
- Render（https://render.com）とStreamlit Community Cloud（https://share.streamlit.io）のアカウント（どちらもGitHubログイン可）

---

## 1. バックエンド（FastAPI）を Render にデプロイ

1. Render ダッシュボード → **New +** → **Web Service**
2. このGitHubリポジトリを選択
3. 設定（`render.yaml` があれば自動認識されるが、手動なら以下）:
   - Runtime: **Python 3**
   - Build Command: `pip install -r backend/requirements.txt`
   - Start Command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
   - Plan: Free
4. 環境変数（Environment）:
   - `PYTHON_VERSION` = `3.11.6`
   - `ALLOWED_ORIGINS` = 最初は `*`。フロントのURL確定後にそのオリジンへ変更（例: `https://xxxx.streamlit.app`）
5. Deploy を実行 → 払い出されるURL（例: `https://hojokin-backend.onrender.com`）を控える
6. `https://<バックエンドURL>/` にアクセスし、`{"status":"ok", ...}` が返ることを確認

---

## 2. フロントエンド（Streamlit）を Streamlit Community Cloud にデプロイ

Streamlit Cloud はリポジトリ直下の `requirements.txt` を参照します。
このリポジトリの `requirements.txt` にはバック用の依存も含まれますが、フロントの起動には支障ありません。
フロントを軽量にしたい場合は `requirements-frontend.txt` の内容を別リポジトリの `requirements.txt` として使ってください。

1. https://share.streamlit.io → **New app**
2. リポジトリ / ブランチ / **Main file path: `app.py`** を指定
3. **Advanced settings → Secrets** に以下を貼り付け（`.streamlit/secrets.toml.example` 参照）:
   ```toml
   BACKEND_URL = "https://<手順1で控えたバックエンドURL>"
   ```
4. Deploy を実行 → 払い出されるURL（例: `https://xxxx.streamlit.app`）を控える

---

## 3. CORS を締める（推奨）

フロントのURLが確定したら、Render の環境変数 `ALLOWED_ORIGINS` を
フロントのオリジンに変更して再デプロイします（`*` のままでも動作はします）。

```
ALLOWED_ORIGINS = https://xxxx.streamlit.app
```

---

## 4. 動作確認

1. フロントのURLをブラウザで開く
2. 企業名またはURLを入力して解析 → 補助金が表示されればOK
3. うまくつながらない場合:
   - フロントの Secrets の `BACKEND_URL` が正しいか
   - バックの `/` がブラウザから 200 を返すか
   - CORS（`ALLOWED_ORIGINS`）がフロントのオリジンを許可しているか

---

## 補足: 別リポジトリ2つに分ける場合

- バック用リポジトリ: `backend/`, `modules/`, `data/`, `backend/requirements.txt`, `render.yaml`, `Procfile`
- フロント用リポジトリ: `app.py`, `.streamlit/`, `requirements.txt`（＝`requirements-frontend.txt`の内容）

`modules/` はバック側でのみ使うため、フロント側には不要です。
