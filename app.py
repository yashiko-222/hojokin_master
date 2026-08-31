"""
補助金マッチングアプリ
企業HPのURLまたは企業名から事業内容を解析し、最適な補助金を提案する。
"""

import os

import requests
import streamlit as st


# FastAPIバックエンドのURL。
# 優先順位: 環境変数 BACKEND_URL > Streamlit secrets(BACKEND_URL) > ローカル既定。
# Streamlit Community Cloud では Secrets に BACKEND_URL を設定する。
def _resolve_backend_url() -> str:
    env_url = os.environ.get("BACKEND_URL")
    if env_url:
        return env_url
    try:
        # st.secrets は secrets 未設定時に例外を投げるため保護する
        secret_url = st.secrets.get("BACKEND_URL")  # type: ignore[attr-defined]
        if secret_url:
            return str(secret_url)
    except Exception:
        pass
    return "http://127.0.0.1:8000"


API_BASE_URL = _resolve_backend_url()

# 管理者パスワード（環境変数 ADMIN_PASSWORD で変更可。未設定時は "admin"）
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")


def call_search_api(query: str) -> dict:
    """企業名から公式HP候補を検索する。"""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/search",
            json={"query": query},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"error": _backend_error_msg()}
    except requests.exceptions.RequestException as e:
        return {"error": f"API呼び出しエラー: {str(e)}"}


def call_analyze_api(query: str, crawl_subpages: bool, max_results: int) -> dict:
    """FastAPIの解析エンドポイントを呼び出す（URLまたは企業名）。"""
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/analyze",
            json={
                "query": query,
                "crawl_subpages": crawl_subpages,
                "max_results": max_results,
            },
            timeout=180,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"error": _backend_error_msg()}
    except requests.exceptions.RequestException as e:
        return {"error": f"API呼び出しエラー: {str(e)}"}


def _backend_error_msg() -> str:
    return (
        "バックエンド（FastAPI）に接続できません。"
        "別ターミナルで `uvicorn backend.main:app` を起動してください。"
    )


def call_custom_list() -> dict:
    """登録済みカスタム補助金の一覧を取得する。"""
    try:
        r = requests.get(f"{API_BASE_URL}/api/custom/list", timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException:
        return {"subsidies": []}


def call_custom_add(payload: dict) -> dict:
    """カスタム補助金を1件登録する。"""
    try:
        r = requests.post(f"{API_BASE_URL}/api/custom", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def call_custom_add_by_url(url: str) -> dict:
    """補助金ページのURLから自動抽出して登録する。"""
    try:
        r = requests.post(f"{API_BASE_URL}/api/custom/add_by_url",
                          json={"url": url}, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def call_import_mirasapo() -> dict:
    """ミラサポplusから補助金を一括取り込みする。"""
    try:
        r = requests.post(f"{API_BASE_URL}/api/custom/import_mirasapo", timeout=180)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def call_custom_import(records: list) -> dict:
    """カスタム補助金を一括インポートする。"""
    try:
        r = requests.post(f"{API_BASE_URL}/api/custom/import",
                          json=records, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def call_custom_delete(subsidy_id: str) -> dict:
    """カスタム補助金を削除する。"""
    try:
        r = requests.delete(f"{API_BASE_URL}/api/custom/{subsidy_id}", timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def call_custom_update(subsidy_id: str, payload: dict) -> dict:
    """カスタム補助金の金額・対象業種等を手動更新する。"""
    try:
        r = requests.post(f"{API_BASE_URL}/api/custom/{subsidy_id}/update",
                          json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def render_admin_panel():
    """管理者専用：補助金の手動登録（URLから追加）・名前編集・削除UI。"""
    with st.expander("➕ 独自の補助金を追加する", expanded=True):
        st.caption(
            "補助金の公募ページのURLを貼るだけで追加できます。"
            "内容は自動で読み取られ、企業の事業内容に合わせて候補に表示されます。"
        )
        add_url = st.text_input(
            "補助金ページのURL",
            placeholder="https://... （自治体・省庁の公募ページなど）",
            key="custom_add_url",
        )
        if st.button("このURLから追加", type="primary"):
            if not add_url.strip():
                st.warning("URLを入力してください。")
            else:
                with st.spinner("ページを読み取っています..."):
                    res = call_custom_add_by_url(add_url.strip())
                if res.get("duplicate"):
                    st.warning(res.get("error", "このURLは既に登録済みです。"))
                elif res.get("error"):
                    st.error(f"追加できませんでした: {res['error']}")
                else:
                    title = res.get("saved", {}).get("title", "")
                    st.success(f"追加しました: {title}")

        # ミラサポplusから一括取り込み（公的サイト）
        st.divider()
        st.caption(
            "経産省・中小企業庁の「ミラサポplus」から主要な補助金を"
            "まとめて取り込めます（登録済みは自動でスキップ）。"
        )
        if st.button("📥 ミラサポplusを取込", use_container_width=True):
            with st.spinner("ミラサポplusから取得しています...（少し時間がかかります）"):
                res = call_import_mirasapo()
            if res.get("error"):
                st.error(f"取り込みに失敗しました: {res['error']}")
            else:
                st.success(
                    f"{res.get('added', 0)}件を追加しました"
                    f"（重複スキップ: {res.get('skipped', 0)}件）。"
                )
                st.rerun()

        # 追加済み一覧（URL表示・名前編集・削除）
        custom_list = call_custom_list().get("subsidies", [])
        st.markdown(f"**追加済みの補助金（{len(custom_list)}件）**")
        st.session_state.setdefault("editing_custom_id", None)
        if custom_list:
            for cs in custom_list:
                col_t, col_e, col_d = st.columns([5, 1, 1])
                with col_t:
                    url = cs.get("official_url", "")
                    src = "🏛️" if cs.get("source") == "mirasapo" else "✍️"
                    if url:
                        st.markdown(f"{src} [{cs['title']}]({url})")
                    else:
                        st.markdown(f"{src} {cs['title']}")
                with col_e:
                    if st.button("✏️", key=f"edit_{cs['id']}", help="名前を編集"):
                        st.session_state.editing_custom_id = (
                            None if st.session_state.editing_custom_id == cs["id"]
                            else cs["id"]
                        )
                        st.rerun()
                with col_d:
                    if st.button("削除", key=f"del_{cs['id']}"):
                        if st.session_state.editing_custom_id == cs["id"]:
                            st.session_state.editing_custom_id = None
                        call_custom_delete(cs["id"])
                        st.rerun()

                # 名前の編集フォーム（✏️を押した行のみ表示）
                if st.session_state.editing_custom_id == cs["id"]:
                    new_title = st.text_input(
                        "補助金名を編集", value=cs["title"], key=f"title_{cs['id']}"
                    )
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        if st.button("保存", key=f"save_{cs['id']}", type="primary"):
                            if new_title.strip():
                                res = call_custom_update(
                                    cs["id"], {"title": new_title.strip()}
                                )
                                if res.get("error"):
                                    st.error(res["error"])
                                else:
                                    st.session_state.editing_custom_id = None
                                    st.rerun()
                            else:
                                st.warning("補助金名を入力してください。")
                    with ec2:
                        if st.button("キャンセル", key=f"cancel_{cs['id']}"):
                            st.session_state.editing_custom_id = None
                            st.rerun()
        else:
            st.caption("まだ追加された補助金はありません。")


def is_url_input(text: str) -> bool:
    """入力がURLらしいか（フロント側の簡易判定）。"""
    text = text.strip()
    return text.startswith(("http://", "https://")) or (
        "." in text and " " not in text
    )


def render_analysis(result: dict, max_results: int):
    """解析結果を描画する。"""
    # 事業概要（クリックで展開。初期状態は折りたたみ）
    with st.expander("🏢 企業概要の分析（クリックで詳細を表示）", expanded=False):
        if result.get("resolved_url"):
            st.info(f"🌐 解析したHP: {result['resolved_url']}")
        if result.get("summary"):
            st.markdown(
                f'<div class="summary-box">{result["summary"]}</div>',
                unsafe_allow_html=True,
            )

        # 推定業種
        if result.get("industries"):
            industry_html = "　".join([
                f'<span class="industry-tag">{ind["industry"]}</span>'
                for ind in result["industries"]
            ])
            st.markdown("**推定業種:** " + industry_html, unsafe_allow_html=True)

        # 推定企業規模
        cs = result.get("company_size") or {}
        size_label = {
            "large": "上場・大企業の可能性",
            "sme": "中小企業の可能性",
            "unknown": "規模不明",
        }.get(cs.get("size", "unknown"), "規模不明")
        size_line = f"**推定企業規模:** {size_label}"
        if cs.get("signals"):
            size_line += f"（根拠: {'、'.join(cs['signals'][:3])}）"
        st.markdown(size_line)

        # キーワード
        keywords = result.get("keywords", [])
        if keywords:
            st.markdown("**抽出キーワード:**")
            keyword_html = " ".join([
                f'<span class="keyword-tag">{kw["keyword"]}</span>'
                for kw in keywords[:15]
            ])
            st.markdown(keyword_html, unsafe_allow_html=True)

    st.divider()

    # 補助金候補
    st.subheader("📋 おすすめ補助金候補")

    # 企業規模フィルタ等の注記
    for note in result.get("notices", []):
        st.warning(note)

    results = result.get("results", [])
    if results:
        st.success(
            f"🎯 {result.get('total_found', len(results))}件の補助金から、"
            f"関連度の高い{len(results)}件を表示しています。"
        )
        for i, subsidy in enumerate(results, 1):
            score_pct = int(subsidy["relevance_score"] * 100)
            if score_pct >= 40:
                score_color = "🟢"
            elif score_pct >= 20:
                score_color = "🟡"
            else:
                score_color = "🟠"

            badge = "🏷️追加した補助金 " if subsidy.get("is_custom") else ""
            with st.expander(
                f"{score_color} {badge}**{i}. {subsidy['title']}** （関連度: {score_pct}%）",
                expanded=(i <= 3),
            ):
                if subsidy.get("is_custom"):
                    st.caption("📌 あなたが追加した補助金（事業内容に合わせて表示しています）")
                if subsidy.get("recommendation_reason"):
                    st.markdown(f"💡 **おすすめ理由:** {subsidy['recommendation_reason']}")
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"**💴 補助金上限額:** {subsidy['subsidy_max_limit']}")
                    st.markdown(f"**📊 補助率:** {subsidy['subsidy_rate']}")
                with col_b:
                    st.markdown(f"**🏢 実施機関:** {subsidy['organization']}")
                    if subsidy["matched_keywords"]:
                        st.markdown(
                            f"**🔑 マッチ:** {'、'.join(subsidy['matched_keywords'])}"
                        )
                st.markdown(f"**🎯 対象:** {subsidy['target']}")

                # 公式サイトへ直接遷移するボタン（改行しないよう十分な幅を確保）
                official_url = subsidy.get("official_url", "")
                link_cols = st.columns(2)
                with link_cols[0]:
                    if official_url:
                        st.link_button("🔗 公式サイトを開く", official_url,
                                       use_container_width=True)
                with link_cols[1]:
                    st.link_button("🔍 関連情報を検索", subsidy["search_url"],
                                   use_container_width=True)
    else:
        st.warning("該当する補助金が見つかりませんでした。")
        for err in result.get("errors", []):
            st.error(err)


# ===== ページ設定 =====
st.set_page_config(page_title="補助金マッチングツール", page_icon="💰", layout="wide")

st.markdown("""
<style>
    .keyword-tag {
        background-color: #e3f2fd; color: #1565c0; padding: 0.2rem 0.5rem;
        border-radius: 12px; font-size: 0.85rem; margin-right: 0.3rem;
        display: inline-block; margin-bottom: 0.3rem;
    }
    .summary-box {
        background-color: #f0f7ff; border-left: 4px solid #1565c0;
        padding: 0.8rem 1rem; border-radius: 4px; margin-bottom: 0.5rem;
    }
    .industry-tag {
        background-color: #e8f5e9; color: #2e7d32; padding: 0.25rem 0.7rem;
        border-radius: 12px; font-size: 0.9rem; margin-right: 0.4rem;
        display: inline-block; font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.title("💰 補助金マッチングツール")
st.markdown(
    "企業HPのURL **または** 企業名を入力すると、事業内容を解析し、"
    "最適な補助金を提案します。"
)
st.divider()

# サイドバー
with st.sidebar:
    st.header("⚙️ 設定")
    crawl_subpages = st.checkbox("サブページもクロールする", value=True,
                                  help="会社概要や事業内容ページも解析します")
    max_results = st.slider("表示する補助金の最大件数", 3, 20, 10)

    # ===== 管理者ログイン（手動登録は管理者のみ）=====
    st.divider()
    st.session_state.setdefault("is_admin", False)
    with st.expander("🔑 管理者ログイン", expanded=False):
        if st.session_state.is_admin:
            st.success("管理者としてログイン中です。")
            if st.button("ログアウト"):
                st.session_state.is_admin = False
                st.rerun()
        else:
            st.caption("補助金を手動登録するには管理者ログインが必要です。")
            pw = st.text_input("管理者パスワード", type="password", key="admin_pw")
            if st.button("ログイン"):
                if pw == ADMIN_PASSWORD:
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("パスワードが違います。")

    # 管理者のみ：補助金の手動登録UI
    if st.session_state.is_admin:
        render_admin_panel()

    st.divider()
    st.markdown("### 📖 使い方")
    st.markdown("""
    1. 企業HPのURL または 企業名を入力
    2. 企業名の場合は候補から正しいHPを選択
    3. 事業概要・業種・キーワードを自動分析
    4. 最適な補助金が候補として表示されます
    """)
    st.divider()
    st.markdown(
        "**データソース:** [Jグランツ](https://www.jgrants-portal.go.jp/)"
        "（デジタル庁）＋ 管理者が登録した補助金"
    )

# セッション状態の初期化
if "candidates" not in st.session_state:
    st.session_state.candidates = None
if "analyze_url" not in st.session_state:
    st.session_state.analyze_url = None

# 入力欄
col1, col2 = st.columns([2, 1])
with col1:
    query = st.text_input(
        "🔗 企業HPのURL または 企業名",
        placeholder="例: https://example.co.jp　または　株式会社サンプル",
        help="URLでも企業名でも検索できます",
    )
with col2:
    st.markdown("<br>", unsafe_allow_html=True)
    search_button = st.button("🔍 検索・解析", type="primary", use_container_width=True)

# === 検索ボタン押下時の処理 ===
if search_button and query:
    st.session_state.candidates = None
    st.session_state.analyze_url = None

    if is_url_input(query):
        # URL入力 → そのまま解析
        st.session_state.analyze_url = query.strip()
    else:
        # 企業名入力 → まず候補を検索
        with st.spinner("企業HPを検索中..."):
            search_result = call_search_api(query.strip())
        if search_result.get("error"):
            st.error(f"エラー: {search_result['error']}")
        elif not search_result.get("candidates"):
            st.warning("該当する企業HPが見つかりませんでした。正式名称やURLで再検索してください。")
        else:
            st.session_state.candidates = search_result["candidates"]

# === 企業名候補の選択UI ===
if st.session_state.candidates:
    st.subheader("🔎 該当しそうな企業HPを選んでください")
    st.caption("最も関連度の高い候補を上に表示しています。正しいものを選んで解析してください。")

    for i, cand in enumerate(st.session_state.candidates):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"**{cand['title']}**")
            st.caption(f"{cand['url']}")
            if cand.get("snippet"):
                st.caption(cand["snippet"][:100])
        with c2:
            if st.button("このHPで解析", key=f"cand_{i}"):
                st.session_state.analyze_url = cand["url"]
                st.session_state.candidates = None
                st.rerun()
        st.divider()

# === 解析の実行 ===
if st.session_state.analyze_url:
    with st.status("🔄 解析中...", expanded=True) as status:
        st.write(f"📡 {st.session_state.analyze_url} を解析しています...")
        result = call_analyze_api(
            st.session_state.analyze_url, crawl_subpages, max_results
        )

        if result.get("error"):
            status.update(label="❌ エラーが発生しました", state="error")
            st.error(f"エラー: {result['error']}")
            st.session_state.analyze_url = None
            st.stop()

        st.write(f"✅ {result['pages_crawled']}ページを解析しました")
        if not result.get("keywords"):
            status.update(label="⚠️ キーワードが見つかりませんでした", state="error")
            st.warning("キーワードを抽出できませんでした。別のHPをお試しください。")
            st.session_state.analyze_url = None
            st.stop()

        # 完了後はログを折りたたむ
        status.update(label="✅ 解析完了", state="complete", expanded=False)

    render_analysis(result, max_results)

elif search_button and not query:
    st.warning("⚠️ URLまたは企業名を入力してください。")
