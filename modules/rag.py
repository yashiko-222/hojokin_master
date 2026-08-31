"""
RAG（検索拡張）用の埋め込み・類似度検索モジュール。

企業の事業概要テキストを「クエリ」、手動登録補助金の詳細テキストを「文書」とし、
ベクトル化してコサイン類似度で近い補助金を検索する。

埋め込みバックエンドは環境に応じて自動選択する:
  1. sentence-transformers（多言語E5）が利用可能ならそれを使用（意味ベクトル）
  2. 無ければ scikit-learn の TF-IDF にフォールバック（文字n-gram）
どちらも無い場合でも語の重なり率で最低限のスコアを返し、必ず動作する。

※ 補助金の手動登録件数は多くない想定のため、Vector DB（Chroma等）は使わず
   オンメモリでコサイン類似度を計算する軽量構成とする。件数が増えた場合は
   本モジュールの内部だけを Chroma/FAISS に差し替えれば拡張できる。
"""

from __future__ import annotations

import math
import re


# ===== 埋め込みバックエンドの遅延ロード =====
_SBERT_MODEL = None
_SBERT_TRIED = False


def _get_sbert():
    """sentence-transformers モデルを遅延ロードする。使えなければ None。"""
    global _SBERT_MODEL, _SBERT_TRIED
    if _SBERT_TRIED:
        return _SBERT_MODEL
    _SBERT_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        # 軽量な多言語モデル（日本語対応）
        _SBERT_MODEL = SentenceTransformer("intfloat/multilingual-e5-small")
    except Exception:
        _SBERT_MODEL = None
    return _SBERT_MODEL


def get_backend_name() -> str:
    """現在有効な埋め込みバックエンド名を返す（UI表示・デバッグ用）。"""
    if _get_sbert() is not None:
        return "sentence-transformers (multilingual-e5-small)"
    try:
        import sklearn  # noqa: F401
        return "TF-IDF (scikit-learn)"
    except Exception:
        return "語重なり (フォールバック)"


def _cosine(a, b) -> float:
    """2ベクトルのコサイン類似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _token_overlap_scores(query: str, documents: list[str]) -> list[float]:
    """最終フォールバック: 文字2-gramの重なり率で類似度を近似する。"""
    def grams(text: str) -> set:
        t = re.sub(r"\s+", "", text.lower())
        return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else {t}

    q = grams(query)
    if not q:
        return [0.0] * len(documents)
    scores = []
    for doc in documents:
        d = grams(doc)
        if not d:
            scores.append(0.0)
            continue
        inter = len(q & d)
        scores.append(inter / math.sqrt(len(q) * len(d)))
    return scores


def rank_by_similarity(query: str, documents: list[str]) -> list[float]:
    """
    クエリと各文書の類似度スコア（0〜1目安）のリストを返す。

    Args:
        query: 企業の事業概要＋キーワードを結合したテキスト
        documents: 補助金の詳細テキストのリスト

    Returns:
        documents と同じ長さの類似度スコアのリスト
    """
    if not documents:
        return []
    if not query or not query.strip():
        return [0.0] * len(documents)

    # 1) sentence-transformers（意味ベクトル）
    model = _get_sbert()
    if model is not None:
        try:
            # E5系は "query: " / "passage: " プレフィックスを推奨
            q_emb = model.encode([f"query: {query}"], normalize_embeddings=True)[0]
            d_embs = model.encode(
                [f"passage: {d}" for d in documents], normalize_embeddings=True
            )
            return [float(_cosine(q_emb, d)) for d in d_embs]
        except Exception:
            pass

    # 2) TF-IDF（scikit-learn）
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        corpus = [query] + documents
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        matrix = vectorizer.fit_transform(corpus)
        sims = cosine_similarity(matrix[0:1], matrix[1:])[0]
        return [float(s) for s in sims]
    except Exception:
        pass

    # 3) 最終フォールバック（語の重なり）
    return _token_overlap_scores(query, documents)


def build_company_query(summary: str, keywords: list[dict] | None = None) -> str:
    """
    企業の事業概要とキーワードから、RAG検索用のクエリテキストを合成する。
    """
    parts = []
    if summary:
        parts.append(summary)
    if keywords:
        parts.append(" ".join(kw["keyword"] for kw in keywords[:20]))
    return " ".join(parts).strip()


def build_subsidy_document(subsidy: dict) -> str:
    """
    補助金データから、RAG検索対象の文書テキストを合成する。
    タイトル・概要・対象業種・対象経費・詳細テキストを結合する。
    """
    parts = [
        subsidy.get("title", ""),
        subsidy.get("description", ""),
        subsidy.get("target", ""),
        " ".join(subsidy.get("target_industries", []) or []),
        subsidy.get("target_expenses", ""),
        subsidy.get("detail_text", ""),
        " ".join(subsidy.get("category_keywords", []) or []),
    ]
    return " ".join(p for p in parts if p).strip()
