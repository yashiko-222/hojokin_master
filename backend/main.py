"""
FastAPIバックエンド
企業HPの解析と補助金マッチングのAPIを提供する。
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加（modulesをインポートするため）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from modules import custom_store, rag
from modules.jgrants import check_api_availability
from modules.crawler import (
    crawl_and_extract,
    extract_mirasapo_subsidies,
    extract_subsidy_from_url,
)
from modules.matcher import match_subsidies
from modules.search import find_company_url, is_url, normalize_url


app = FastAPI(
    title="補助金マッチングAPI",
    description="企業HPまたは企業名を解析し、最適な補助金を提案するAPI",
    version="2.0.0",
)

# CORS設定。フロント（Streamlit）とバック（FastAPI）が別ドメインにデプロイされる
# ため、フロントのオリジンからのリクエストを許可する。
# 環境変数 ALLOWED_ORIGINS にカンマ区切りで許可オリジンを設定できる。
# 未設定時はすべて許可（開発用）。本番ではフロントのURLを指定すること。
_allowed = os.environ.get("ALLOWED_ORIGINS", "*")
_allow_origins = (
    ["*"] if _allowed.strip() == "*"
    else [o.strip() for o in _allowed.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== リクエスト/レスポンスモデル =====

class AnalyzeRequest(BaseModel):
    """解析リクエスト"""
    query: str = Field(..., description="企業HPのURL または 企業名・自然言語")
    crawl_subpages: bool = Field(True, description="サブページもクロールするか")
    max_results: int = Field(10, ge=1, le=50, description="返す補助金の最大件数")


class SearchRequest(BaseModel):
    """企業検索リクエスト"""
    query: str = Field(..., description="企業名・自然言語")


class Keyword(BaseModel):
    keyword: str
    score: float


class Industry(BaseModel):
    industry: str
    score: int


class CompanySize(BaseModel):
    size: str = "unknown"          # large / sme / unknown
    is_listed: bool = False
    signals: list[str] = []


class Candidate(BaseModel):
    title: str
    url: str
    snippet: str
    official_score: float


class SubsidyResult(BaseModel):
    id: str
    title: str
    subsidy_max_limit: str
    subsidy_rate: str
    target: str
    organization: str
    official_url: str = ""
    search_url: str
    eligible_scale: str = "sme"
    matched_keywords: list[str]
    description: str
    relevance_score: float
    recommendation_reason: str = ""
    # データ区分（builtin=Jグランツ内蔵 / manual=手動登録カスタム）
    source: str = "builtin"
    is_custom: bool = False
    target_industries: list[str] = []
    target_expenses: str = ""


class SearchResponse(BaseModel):
    """企業検索レスポンス"""
    url: str | None
    candidates: list[Candidate]
    error: str | None = None


class AnalyzeResponse(BaseModel):
    """解析レスポンス"""
    input_type: str            # "url" または "company_name"
    resolved_url: str          # 実際に解析したURL
    pages_crawled: int
    summary: str               # 事業概要の要約
    industries: list[Industry] # 推定業種
    company_size: CompanySize  # 企業規模の推定
    keywords: list[Keyword]
    search_keywords: list[str]
    total_found: int
    results: list[SubsidyResult]
    candidates: list[Candidate]  # 企業名検索時のHP候補
    notices: list[str] = []      # 規模フィルタ等の注記
    errors: list[str]
    error: str | None = None


# ===== エンドポイント =====

@app.get("/")
def health_check():
    """ヘルスチェック"""
    jgrants_status = check_api_availability()
    return {
        "status": "ok",
        "service": "補助金マッチングAPI",
        "rag_backend": rag.get_backend_name(),
        "jgrants_api": jgrants_status,
    }


# ===== カスタム補助金 管理API =====

class CustomSubsidyIn(BaseModel):
    """手動登録カスタム補助金の入力"""
    title: str = Field(..., description="補助金名")
    organization: str = Field("", description="実施機関")
    subsidy_max_limit: str = Field("", description="補助上限額")
    subsidy_rate: str = Field("", description="補助率")
    eligible_scale: str = Field("sme", description="対象規模 sme/all")
    target_industries: list[str] = Field(default_factory=list, description="対象業種")
    target_expenses: str = Field("", description="対象経費")
    description: str = Field("", description="概要")
    detail_text: str = Field("", description="詳細・公募要領テキスト（RAG対象）")
    official_url: str = Field("", description="公式URL")


@app.get("/api/custom/list")
def custom_list():
    """登録済みカスタム補助金の一覧を返す。"""
    return {"subsidies": custom_store.load_custom_subsidies()}


@app.post("/api/custom")
def custom_add(item: CustomSubsidyIn):
    """カスタム補助金を1件追加する。"""
    saved = custom_store.add_custom_subsidy(item.model_dump())
    return {"saved": saved}


class CustomUrlIn(BaseModel):
    """URLからのカスタム補助金登録リクエスト"""
    url: str = Field(..., description="補助金の公募ページURL")


@app.post("/api/custom/add_by_url")
def custom_add_by_url(item: CustomUrlIn):
    """
    補助金の公募ページURLから内容を自動抽出して登録する。
    タイトル・概要・詳細テキスト（RAG対象）を自動取得する。
    """
    url = item.url.strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "URLは http:// または https:// で始めてください。"}

    # 既に同じURLが登録済みなら重複登録しない
    existing = custom_store.find_by_url(url)
    if existing:
        return {
            "error": f"このURLは既に登録済みです（{existing.get('title', '')}）。",
            "duplicate": True,
            "existing": existing,
        }

    info = extract_subsidy_from_url(url)
    if info.get("error"):
        return {"error": info["error"]}

    saved = custom_store.add_custom_subsidy({
        "title": info["title"],
        "description": info["description"],
        "detail_text": info["detail_text"],
        "official_url": info["official_url"],
        # 規模・業種・金額は未指定（後から編集可能）。RAGは本文テキストで機能する。
        "eligible_scale": "sme",
    })
    return {"saved": saved}


@app.post("/api/custom/import_mirasapo")
def custom_import_mirasapo(max_items: int = 15):
    """
    ミラサポplus（中小企業庁の公式サイト）から補助金を一括取り込みする。
    既に同じURLが登録済みのものはスキップする。
    """
    result = extract_mirasapo_subsidies(max_items=max_items)
    if result.get("error"):
        return {"error": result["error"], "added": 0, "skipped": 0}

    added, skipped = 0, 0
    added_titles = []
    for sub in result["subsidies"]:
        if custom_store.find_by_url(sub.get("official_url", "")):
            skipped += 1
            continue
        custom_store.add_custom_subsidy(sub)
        added += 1
        added_titles.append(sub["title"])

    return {"added": added, "skipped": skipped, "titles": added_titles}


@app.post("/api/custom/import")
def custom_import(payload: list[CustomSubsidyIn]):
    """カスタム補助金を一括インポートする。"""
    count = custom_store.import_from_json([p.model_dump() for p in payload])
    return {"imported": count}


class CustomUpdateIn(BaseModel):
    """カスタム補助金の手動補足（更新）用。空欄は既存値を保持。"""
    title: str | None = None
    organization: str | None = None
    subsidy_max_limit: str | None = None
    subsidy_rate: str | None = None
    eligible_scale: str | None = None
    target_industries: list[str] | None = None
    target_expenses: str | None = None
    description: str | None = None


@app.post("/api/custom/{subsidy_id}/update")
def custom_update(subsidy_id: str, item: CustomUpdateIn):
    """カスタム補助金の金額・対象業種等を手動で補足更新する。"""
    updates = {k: v for k, v in item.model_dump().items() if v is not None}
    rec = custom_store.update_custom_subsidy(subsidy_id, updates)
    if rec is None:
        return {"error": "対象の補助金が見つかりませんでした。"}
    return {"updated": rec}


@app.delete("/api/custom/{subsidy_id}")
def custom_delete(subsidy_id: str):
    """カスタム補助金を削除する。"""
    ok = custom_store.delete_custom_subsidy(subsidy_id)
    return {"deleted": ok}


@app.post("/api/search", response_model=SearchResponse)
def search_company(request: SearchRequest):
    """
    企業名・自然言語から公式HPのURL候補を検索する。
    """
    result = find_company_url(request.query)
    return SearchResponse(
        url=result["url"],
        candidates=result["candidates"],
        error=result["error"],
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    """
    企業HPまたは企業名を解析して補助金候補を返す。

    1. 入力がURLか企業名かを判定
    2. 企業名ならWeb検索で公式HPを特定
    3. HPをクロールしてキーワード抽出・概要分析・業種推定
    4. 補助金をマッチング（概要・業種を加味）
    """
    query = request.query.strip()
    candidates: list[dict] = []
    default_size = {"size": "unknown", "is_listed": False, "signals": []}

    # ステップ1: 入力タイプ判定
    if is_url(query):
        input_type = "url"
        target_url = normalize_url(query)
    else:
        # 企業名 → Web検索で公式HPを特定
        input_type = "company_name"
        search_result = find_company_url(query)
        candidates = search_result["candidates"]

        if search_result["error"] or not search_result["url"]:
            return AnalyzeResponse(
                input_type=input_type,
                resolved_url="",
                pages_crawled=0,
                summary="",
                industries=[],
                company_size=default_size,
                keywords=[],
                search_keywords=[],
                total_found=0,
                results=[],
                candidates=candidates,
                notices=[],
                errors=[],
                error=(
                    search_result["error"]
                    or "企業HPを特定できませんでした。企業名を変えてお試しください。"
                ),
            )
        target_url = search_result["url"]

    # ステップ2: クロール＆キーワード抽出＆概要分析
    crawl_result = crawl_and_extract(
        target_url, crawl_subpages=request.crawl_subpages
    )

    if crawl_result["error"]:
        return AnalyzeResponse(
            input_type=input_type,
            resolved_url=target_url,
            pages_crawled=0,
            summary="",
            industries=[],
            company_size=default_size,
            keywords=[],
            search_keywords=[],
            total_found=0,
            results=[],
            candidates=candidates,
            notices=[],
            errors=[],
            error=crawl_result["error"],
        )

    keywords = crawl_result["keywords"]

    if not keywords:
        return AnalyzeResponse(
            input_type=input_type,
            resolved_url=target_url,
            pages_crawled=crawl_result["pages_crawled"],
            summary=crawl_result.get("summary", ""),
            industries=crawl_result.get("industries", []),
            company_size=crawl_result.get("company_size", default_size),
            keywords=[],
            search_keywords=[],
            total_found=0,
            results=[],
            candidates=candidates,
            notices=[],
            errors=[],
            error="キーワードを抽出できませんでした。URLを確認してください。",
        )

    # ステップ3: 補助金マッチング（概要・業種・企業規模を加味）
    match_result = match_subsidies(
        keywords,
        max_results=request.max_results,
        summary=crawl_result.get("summary", ""),
        industries=crawl_result.get("industries", []),
        company_size=crawl_result.get("company_size"),
        prefecture=crawl_result.get("prefecture"),
    )

    return AnalyzeResponse(
        input_type=input_type,
        resolved_url=target_url,
        pages_crawled=crawl_result["pages_crawled"],
        summary=crawl_result.get("summary", ""),
        industries=crawl_result.get("industries", []),
        company_size=crawl_result.get("company_size", default_size),
        keywords=keywords,
        search_keywords=match_result["search_keywords"],
        total_found=match_result["total_found"],
        results=match_result["results"],
        candidates=candidates,
        notices=match_result.get("notices", []),
        errors=match_result["errors"],
        error=None,
    )
