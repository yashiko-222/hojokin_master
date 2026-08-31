"""
RAG（検索拡張）用の埋め込み・類似度検索モジュール。

企業の事業概要テキストを「クエリ」、手動登録補助金の詳細テキストを「文書」とし、
ベクトル化してコサイン類似度で近い補助金を検索する。

埋め込みバックエンドは環境に応じて自動選択する:
  1. sentence-transformers（多言語E5）が利用可能ならそれを使用（意味ベクトル）
  2. 無ければ純Python実装の TF-IDF（文字n-gram）を使用（scikit-learn非依存）
最終手段として語の重なり率でも最低限のスコアを返し、必ず動作する。

※ 補助金の手動登録件数は多くない想定のため、Vector DB（Chroma等）は使わず
   オンメモリでコサイン類似度を計算する軽量構成とする。件数が増えた場合は
   本モジュールの内部だけを Chroma/FAISS に差し替えれば拡張できる。
"""

from __future__ import annotations

import math
import re
from collections import Counter


# ===== 純Python TF-IDF（scikit-learn非依存） =====
# scikit-learn の TfidfVectorizer(analyzer="char_wb", ngram_range=(2,4)) 相当を
# 標準ライブラリのみで実装する。scipy/numpy を引き込まないためメモリが軽い。

def _char_wb_ngrams(text: str, n_min: int = 2, n_max: int = 4) -> list[str]:
    """
    scikit-learn の char_wb 相当の文字n-gramを生成する。
    各単語を空白で囲み（境界を尊重）、n_min〜n_maxの文字窓を切り出す。
    """
    text = text.lower()
    tokens = text.split()
    ngrams: list[str] = []
    for tok in tokens:
        w = f" {tok} "
        length = len(w)
        for n in range(n_min, n_max + 1):
            if length < n:
                # scikit-learnは単語長がn未満なら単語全体(空白付き)を1つ採用
                if n == n_min:
                    ngrams.append(w)
                continue
            for i in range(length - n + 1):
                ngrams.append(w[i:i + n])
    return ngrams


def _tfidf_cosine(text_a: str, text_b: str) -> float:
    """
    2テキスト間の TF-IDF コサイン類似度を純Pythonで計算する。
    2文書コーパス（[a, b]）に対する char_wb TF-IDF を L2 正規化してコサインを取る。
    scikit-learn の既定（smooth_idf=True, sublinear_tf=False, norm="l2"）に合わせる。
    """
    if not text_a.strip() or not text_b.strip():
        return 0.0

    grams_a = _char_wb_ngrams(text_a)
    grams_b = _char_wb_ngrams(text_b)
    tf_a = Counter(grams_a)
    tf_b = Counter(grams_b)
    if not tf_a or not tf_b:
        return 0.0

    vocab = set(tf_a) | set(tf_b)
    n_docs = 2

    # IDF（smooth_idf=True）: idf = ln((1+n)/(1+df)) + 1
    def idf(term: str) -> float:
        df = (1 if term in tf_a else 0) + (1 if term in tf_b else 0)
        return math.log((1 + n_docs) / (1 + df)) + 1.0

    vec_a: dict[str, float] = {}
    vec_b: dict[str, float] = {}
    for term in vocab:
        w = idf(term)
        if tf_a.get(term):
            vec_a[term] = tf_a[term] * w
        if tf_b.get(term):
            vec_b[term] = tf_b[term] * w

    # L2正規化してドット積（＝コサイン類似度）
    na = math.sqrt(sum(v * v for v in vec_a.values()))
    nb = math.sqrt(sum(v * v for v in vec_b.values()))
    if na == 0 or nb == 0:
        return 0.0
    dot = sum(vec_a[t] * vec_b[t] for t in vec_a.keys() & vec_b.keys())
    return dot / (na * nb)


def tfidf_cosine_similarity(text_a: str, text_b: str) -> float:
    """2テキストのTF-IDFコサイン類似度（0〜1）。外部モジュール向け公開API。"""
    return _tfidf_cosine(text_a, text_b)


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
    return "TF-IDF (pure-python char n-gram)"


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

    # 2) 純Python TF-IDF（char_wb 2-4gram コサイン類似度）
    try:
        return [_tfidf_cosine(query, d) for d in documents]
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
