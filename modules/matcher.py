"""
キーワードと補助金のマッチングロジックモジュール
抽出したキーワードを元に、最適な補助金を選定・ランキングする。
"""

from modules import rag
from modules.jgrants import (
    format_subsidy_info,
    get_custom_subsidies,
    search_subsidies_multi_keywords,
)


# 汎用トレンドワード（どの業種でも登場しやすく、単体では本業を表さない語）。
# これらの一致は加点を強く抑制し、本業に沿った補助金が上位に来るようにする。
GENERIC_TREND_WORDS = {
    "it", "dx", "ai", "iot", "sdgs", "esg", "環境", "デジタル", "デジタル化",
    "クラウド", "省エネ", "脱炭素", "カーボンニュートラル", "再生可能エネルギー",
    "イノベーション", "グリーン", "サステナビリティ", "サステナブル",
    "業務効率化", "働き方改革", "テレワーク", "オンライン",
}


# キーワードを補助金検索用のカテゴリにマッピング
KEYWORD_CATEGORY_MAP = {
    # IT・DX関連
    "IT": ["IT導入", "DX", "デジタル化"],
    "DX": ["DX", "デジタル化", "IT導入"],
    "AI": ["AI", "DX", "先端技術"],
    "IoT": ["IoT", "DX", "先端技術"],
    "デジタル": ["DX", "デジタル化", "IT導入"],
    "システム": ["IT導入", "業務効率化"],
    "ロボット": ["ロボット", "自動化", "省力化"],
    "自動化": ["自動化", "省力化", "生産性向上"],
    # 製造・設備関連
    "製造": ["ものづくり", "製造業", "設備投資"],
    "設備": ["設備投資", "ものづくり"],
    "生産": ["生産性向上", "ものづくり"],
    "加工": ["ものづくり", "製造業"],
    "工場": ["設備投資", "ものづくり"],
    # 省エネ・環境関連
    "省エネ": ["省エネ", "脱炭素", "環境"],
    "脱炭素": ["脱炭素", "カーボンニュートラル", "環境"],
    "環境": ["環境", "省エネ", "脱炭素"],
    "再生可能エネルギー": ["再生可能エネルギー", "脱炭素"],
    "リサイクル": ["環境", "リサイクル"],
    # 研究・開発関連
    "研究": ["研究開発", "技術革新"],
    "開発": ["研究開発", "新製品開発"],
    "技術": ["技術革新", "研究開発"],
    "イノベーション": ["技術革新", "イノベーション"],
    "特許": ["知的財産", "研究開発"],
    # 海外展開関連
    "輸出": ["海外展開", "輸出"],
    "海外": ["海外展開", "グローバル"],
    "グローバル": ["海外展開", "グローバル"],
    "国際": ["海外展開", "国際"],
    # 人材関連
    "人材": ["人材育成", "雇用"],
    "育成": ["人材育成", "研修"],
    "雇用": ["雇用", "人材確保"],
    "働き方": ["働き方改革", "雇用"],
    # 地域関連
    "地域": ["地域活性化", "地方創生"],
    "地方": ["地方創生", "地域活性化"],
    "まちづくり": ["まちづくり", "地域活性化"],
    # 業種関連
    "農業": ["農業", "一次産業"],
    "漁業": ["漁業", "水産業"],
    "食品": ["食品", "6次産業化"],
    "観光": ["観光", "インバウンド"],
    "医療": ["医療", "ヘルスケア"],
    "福祉": ["福祉", "介護"],
    "介護": ["介護", "福祉"],
    "建設": ["建設", "インフラ"],
    # 事業形態関連
    "創業": ["創業", "スタートアップ"],
    "事業承継": ["事業承継", "M&A"],
    "事業再構築": ["事業再構築", "新分野展開"],
    "販路": ["販路開拓", "販路拡大"],
    "小規模": ["小規模事業者", "持続化"],
    "中小企業": ["中小企業", "経営革新"],
}

# 推定業種から補助金カテゴリキーワードへのマッピング（検索語生成に使用）
INDUSTRY_TO_CATEGORY = {
    "製造業": ["ものづくり", "製造", "設備投資", "生産性向上"],
    "IT・情報通信業": ["IT", "DX", "デジタル化", "業務効率化"],
    "建設業": ["設備投資", "省力化", "生産性向上"],
    "小売業": ["販路開拓", "IT", "業務効率化"],
    "飲食業": ["販路開拓", "小規模事業者", "業務効率化"],
    "卸売・商社": ["販路開拓", "海外展開", "IT"],
    "医療・福祉": ["人材育成", "IT", "省力化"],
    "農林水産業": ["6次産業化", "食品", "販路開拓"],
    "運輸・物流業": ["省力化", "自動化", "IT"],
    "サービス業": ["IT", "業務効率化", "販路開拓"],
    "観光・宿泊業": ["観光", "インバウンド", "販路開拓"],
    "金融・保険業": ["IT", "DX", "業務効率化"],
    "教育業": ["人材育成", "IT", "研修"],
    "不動産業": ["IT", "業務効率化", "設備投資"],
}

# 【A】業種 → 補助金ID の直接マッピング（重み付き）。
# 各業種で「特に相性が良い補助金」を明示し、重み(0.0〜1.0)で優先度を表現する。
# これにより業種ごとにランキング上位が変わり、企業間で結果が出し分けられる。
INDUSTRY_TO_SUBSIDY = {
    "製造業": {
        "monodukuri-hojo": 1.0, "shoryokuka-it": 0.7,
        "kenkyu-kaihatsu": 0.6, "sho-energy": 0.5, "jigyo-saikouchiku": 0.4,
    },
    "IT・情報通信業": {
        "shoryokuka-it": 1.0, "kenkyu-kaihatsu": 0.6,
        "jinzai-kaihatsu": 0.5, "jigyo-saikouchiku": 0.4,
    },
    "建設業": {
        "shoryokuka-it": 0.8, "monodukuri-hojo": 0.6,
        "jinzai-kaihatsu": 0.5, "sho-energy": 0.5,
    },
    "小売業": {
        "jizokuka-hojo": 1.0, "shoryokuka-it": 0.7,
        "jigyo-saikouchiku": 0.5,
    },
    "飲食業": {
        "jizokuka-hojo": 1.0, "shoryokuka-it": 0.6,
        "jigyo-saikouchiku": 0.5,
    },
    "卸売・商社": {
        "kaigai-tenkai": 0.9, "jizokuka-hojo": 0.7, "shoryokuka-it": 0.6,
    },
    "医療・福祉": {
        "jinzai-kaihatsu": 0.9, "shoryokuka-it": 0.7, "monodukuri-hojo": 0.4,
    },
    "農林水産業": {
        "chiiki-kasseika": 0.9, "monodukuri-hojo": 0.6, "jizokuka-hojo": 0.6,
    },
    "運輸・物流業": {
        "shoryokuka-it": 1.0, "sho-energy": 0.5, "jinzai-kaihatsu": 0.5,
    },
    "サービス業": {
        "shoryokuka-it": 0.8, "jizokuka-hojo": 0.7, "jinzai-kaihatsu": 0.6,
    },
    "観光・宿泊業": {
        "jizokuka-hojo": 0.9, "chiiki-kasseika": 0.8, "shoryokuka-it": 0.6,
    },
    "金融・保険業": {
        "jinzai-kaihatsu": 0.6, "shoryokuka-it": 0.4,
    },
    "教育業": {
        "jinzai-kaihatsu": 0.9, "shoryokuka-it": 0.6,
    },
    "不動産業": {
        "shoryokuka-it": 0.7, "jizokuka-hojo": 0.5, "jinzai-kaihatsu": 0.5,
    },
}


def select_search_keywords(
    extracted_keywords: list[dict],
    industries: list[dict] | None = None,
    max_keywords: int = 5,
) -> list[str]:
    """
    抽出されたキーワードと推定業種から、補助金検索に最適なキーワードを選定する。

    Args:
        extracted_keywords: crawler.pyで抽出されたキーワードリスト
        industries: 推定業種リスト [{"industry": str, "score": int}, ...]
        max_keywords: 使用する最大キーワード数

    Returns:
        検索用キーワードのリスト
    """
    search_terms = []
    used_categories = set()

    # 推定業種から優先的に検索語を追加（概要分析の結果を反映）
    # 各業種のカテゴリ語を複数入れ、関連する補助金を取りこぼさない
    if industries:
        for ind_info in industries:
            industry = ind_info["industry"]
            for term in INDUSTRY_TO_CATEGORY.get(industry, []):
                if term not in used_categories:
                    search_terms.append(term)
                    used_categories.add(term)

    for kw_info in extracted_keywords:
        keyword = kw_info["keyword"]

        # カテゴリマップに存在する場合、対応する検索語を追加
        if keyword in KEYWORD_CATEGORY_MAP:
            for term in KEYWORD_CATEGORY_MAP[keyword]:
                if term not in used_categories:
                    search_terms.append(term)
                    used_categories.add(term)
                    break  # カテゴリごとに1つだけ追加

        # マップにない場合はそのまま使用（スコアが高いもの優先）
        elif kw_info["score"] >= 3.0 and keyword not in used_categories:
            search_terms.append(keyword)
            used_categories.add(keyword)

        if len(search_terms) >= max_keywords:
            break

    # 最低限のキーワードが確保できない場合、上位キーワードを追加
    if len(search_terms) < 3:
        for kw_info in extracted_keywords[:5]:
            keyword = kw_info["keyword"]
            if keyword not in used_categories:
                search_terms.append(keyword)
                used_categories.add(keyword)
            if len(search_terms) >= 3:
                break

    return search_terms


def calculate_relevance_score(
    subsidy: dict,
    keywords: list[dict],
    search_terms: list[str],
    summary: str = "",
    industries: list[dict] | None = None,
) -> float:
    """
    補助金と企業の関連度スコアを計算する。

    TF-IDF類似度（キーワード＋概要文）＋ カテゴリマッチ ＋ 業種マッチ ＋
    マッチキーワード数によるスコア。

    Args:
        subsidy: 補助金情報
        keywords: 企業から抽出したキーワードリスト
        search_terms: 検索に使用したキーワード
        summary: 企業概要の要約文
        industries: 推定業種リスト

    Returns:
        0.0〜1.0の関連度スコア
    """
    # 補助金のテキスト情報を結合（内蔵データのcategory_keywordsも含む）
    category_kw_text = " ".join(subsidy.get("category_keywords", []))
    subsidy_text = " ".join([
        subsidy.get("title", ""),
        subsidy.get("target", ""),
        subsidy.get("description", ""),
        category_kw_text,
    ])

    # 企業のキーワード＋概要文を比較テキストに（概要分析を反映）
    company_text = " ".join([kw["keyword"] for kw in keywords[:20]])
    if summary:
        company_text = f"{company_text} {summary}"

    # --- TF-IDF類似度（重みは控えめ。汎用テキストで差が出にくいため）---
    # scikit-learn を使わず純Python実装（rag.tfidf_cosine_similarity）で算出。
    similarity = 0.0
    if subsidy_text.strip() and company_text.strip():
        similarity = rag.tfidf_cosine_similarity(company_text, subsidy_text)

    # 【C】企業固有キーワードと補助金カテゴリ語の直接一致を評価。
    # 本業を表す具体語の一致は強く、汎用トレンド語の一致は弱く評価する。
    company_keywords_set = {kw["keyword"].lower() for kw in keywords[:25]}
    category_keywords_set = {ck.lower() for ck in subsidy.get("category_keywords", [])}
    matched_terms = company_keywords_set & category_keywords_set
    specific_matches = [t for t in matched_terms if t not in GENERIC_TREND_WORDS]
    generic_matches = [t for t in matched_terms if t in GENERIC_TREND_WORDS]
    # 本業具体語の一致: 強く加点 / 汎用語の一致: 上限を強く制限
    keyword_match_bonus = min(len(specific_matches) * 0.12, 0.5)
    generic_match_bonus = min(len(generic_matches) * 0.04, 0.12)

    # 汎用カテゴリ経由のマッチ（検索語ヒット）は弱めに評価。
    # かつ汎用トレンド語だけのヒットで跳ね上がらないよう、汎用語は除外して数える。
    matched_kw = subsidy.get("matched_keywords", [])
    non_generic_matched = [m for m in matched_kw if m.lower() not in GENERIC_TREND_WORDS]
    weak_keyword_bonus = min(len(non_generic_matched) * 0.05, 0.15)

    # 【A】業種 → 補助金ID の直接マッピングによるボーナス（本業重視で比重引き上げ）
    # 推定業種の順位に応じて重みを変える（1位を最重視）
    industry_bonus = 0.0
    if industries:
        rank_weight = [1.0, 0.6, 0.3]
        subsidy_id = subsidy.get("id", "")
        for rank, ind_info in enumerate(industries[:3]):
            id_weights = INDUSTRY_TO_SUBSIDY.get(ind_info["industry"], {})
            if subsidy_id in id_weights:
                industry_bonus += id_weights[subsidy_id] * rank_weight[rank] * 0.5
        industry_bonus = min(industry_bonus, 0.6)

    # 最終スコア（0〜1に正規化）。業種本業と固有語一致を主軸にする
    final_score = min(
        similarity * 0.4
        + keyword_match_bonus
        + generic_match_bonus
        + weak_keyword_bonus
        + industry_bonus,
        1.0,
    )
    return round(final_score, 4)


def build_recommendation_reason(
    subsidy: dict,
    industries: list[dict] | None = None,
    company_size: dict | None = None,
) -> str:
    """
    補助金が推薦された理由の説明文を生成する。

    Args:
        subsidy: 補助金情報（matched_keywordsを含む）
        industries: 推定業種リスト
        company_size: 企業規模の推定結果

    Returns:
        推薦理由の文字列
    """
    reasons = []

    subsidy_id = subsidy.get("id", "")

    # 【A】業種→補助金IDの直接マッピングに基づく根拠（本業適合を最優先で提示）
    matched_industry = None
    if industries:
        for ind_info in industries[:2]:
            industry = ind_info["industry"]
            if subsidy_id in INDUSTRY_TO_SUBSIDY.get(industry, {}):
                matched_industry = industry
                break
    if matched_industry:
        reasons.append(f"本業（{matched_industry}）に適した制度")

    # マッチしたキーワードを、本業具体語と汎用トレンド語に分けて表現
    matched = subsidy.get("matched_keywords", [])
    specific = [m for m in matched if m.lower() not in GENERIC_TREND_WORDS]
    generic = [m for m in matched if m.lower() in GENERIC_TREND_WORDS]
    if specific:
        reasons.append(f"事業キーワード「{'、'.join(specific[:3])}」に合致")
    elif generic and not matched_industry:
        # 汎用語のみのヒットは、本業適合が確認できていない旨を明示
        reasons.append(
            f"「{'、'.join(generic[:2])}」に関連（本業との適合は要確認）"
        )

    # 企業規模の適合性
    scale = subsidy.get("eligible_scale", "sme")
    size = (company_size or {}).get("size", "unknown")
    if scale == "all":
        reasons.append("企業規模を問わず利用可能")
    elif scale == "sme" and size == "sme":
        reasons.append("中小企業向けの制度に該当")

    if not reasons:
        reasons.append("事業内容の分析結果から候補として抽出")

    return " / ".join(reasons)


def _score_custom_subsidy(
    subsidy: dict,
    keywords: list[dict],
    summary: str,
    industries: list[dict] | None,
    rag_sim_norm: float,
) -> float:
    """
    手動登録カスタム補助金の適合スコアを計算する（RAG類似度＋文脈適合）。

    企業概要とのRAG類似度を主軸に、対象業種の一致・対象経費と企業キーワードの
    一致を加味する。
    """
    # RAG類似度（候補内で0〜1に正規化済み）を主軸
    score = rag_sim_norm * 0.5

    # 対象業種の一致（推定業種の順位で重み付け）
    target_inds = {t for t in subsidy.get("target_industries", [])}
    if industries and target_inds:
        rank_weight = [0.3, 0.18, 0.1]
        for rank, ind in enumerate(industries[:3]):
            if ind.get("industry") in target_inds:
                score += rank_weight[rank]
                break

    # 対象経費・詳細テキストと企業キーワードの一致（本業具体語のみ）
    context_text = (
        subsidy.get("target_expenses", "") + " "
        + subsidy.get("detail_text", "") + " "
        + subsidy.get("description", "")
    ).lower()
    hit = 0
    for kw in keywords[:20]:
        w = kw["keyword"].lower()
        if w in GENERIC_TREND_WORDS:
            continue
        if len(w) >= 2 and w in context_text:
            hit += 1
    score += min(hit * 0.05, 0.2)

    return round(min(score, 1.0), 4)


def _build_custom_reason(
    subsidy: dict, industries: list[dict] | None, company_size: dict | None
) -> str:
    """カスタム補助金の推薦理由を生成する。"""
    reasons = ["手動登録された補助金（RAG検索でマッチ）"]

    target_inds = set(subsidy.get("target_industries", []))
    if industries and target_inds:
        for ind in industries[:2]:
            if ind.get("industry") in target_inds:
                reasons.append(f"対象業種「{ind['industry']}」に合致")
                break

    if subsidy.get("target_expenses"):
        reasons.append(f"対象経費: {subsidy['target_expenses'][:40]}")

    scale = subsidy.get("eligible_scale", "sme")
    size = (company_size or {}).get("size", "unknown")
    if scale == "all":
        reasons.append("企業規模を問わず利用可能")
    elif scale == "sme" and size == "sme":
        reasons.append("中小企業向けの制度に該当")

    return " / ".join(reasons)


def match_subsidies(
    extracted_keywords: list[dict],
    max_results: int = 10,
    summary: str = "",
    industries: list[dict] | None = None,
    company_size: dict | None = None,
) -> dict:
    """
    抽出キーワード・概要・業種・企業規模から補助金を検索し、関連度順にランキングする。

    Args:
        extracted_keywords: crawler.pyで抽出されたキーワードリスト
        max_results: 返す最大件数
        summary: 企業概要の要約文
        industries: 推定業種リスト
        company_size: 企業規模の推定結果（size/is_listed/signals）

    Returns:
        {
            "results": list[dict],  # 関連度順の補助金リスト（推薦理由付き）
            "search_keywords": list[str],
            "total_found": int,
            "notices": list[str],   # 規模フィルタ等の注記
            "errors": list[str],
        }
    """
    # 検索キーワードを選定（業種も加味）
    search_terms = select_search_keywords(extracted_keywords, industries)

    if not search_terms:
        return {
            "results": [],
            "search_keywords": [],
            "total_found": 0,
            "notices": [],
            "errors": ["キーワードが抽出できませんでした。URLを確認してください。"],
        }

    notices = []
    size_info = company_size or {}
    size_val = size_info.get("size", "unknown")
    is_listed = bool(size_info.get("is_listed"))
    # 資本金・従業員数から明確に大企業と判定できるケース
    definitely_large = size_val == "large"
    # 上場しているが規模は中小/不明のケース（＝上場中小企業の可能性）
    listed_not_large = is_listed and size_val != "large"

    def _scale_ok(subsidy: dict) -> bool:
        """
        企業規模の適合判定。
        明確な大企業のみ中小限定制度を除外する。上場でも規模が中小/不明の
        場合は除外せず候補に残し、注記で「みなし大企業の可能性」を伝える。
        """
        return not (definitely_large and subsidy.get("eligible_scale", "sme") == "sme")

    # ===== 1. Jグランツ内蔵データの検索・スコアリング =====
    # 速度優先で1キーワードあたりの取得件数を絞る（API呼び出し総数を削減）。
    api_result = search_subsidies_multi_keywords(search_terms, limit_per_keyword=3)
    builtin_excluded = 0
    scored_subsidies = []
    for subsidy in api_result["subsidies"]:
        if not _scale_ok(subsidy):
            builtin_excluded += 1
            continue
        score = calculate_relevance_score(
            subsidy, extracted_keywords, search_terms, summary, industries
        )
        reason = build_recommendation_reason(subsidy, industries, company_size)
        formatted = format_subsidy_info(subsidy)
        formatted["relevance_score"] = score
        formatted["recommendation_reason"] = reason
        scored_subsidies.append(formatted)

    # ===== 2. 手動登録カスタム補助金の RAG 検索・スコアリング =====
    customs = get_custom_subsidies()
    custom_excluded = 0
    custom_scored = []
    if customs:
        eligible_customs = []
        for c in customs:
            if _scale_ok(c):
                eligible_customs.append(c)
            else:
                custom_excluded += 1

        if eligible_customs:
            query = rag.build_company_query(summary, extracted_keywords)
            docs = [rag.build_subsidy_document(c) for c in eligible_customs]
            sims = rag.rank_by_similarity(query, docs)
            max_sim = max(sims) if sims else 0.0
            for c, sim in zip(eligible_customs, sims):
                sim_norm = (sim / max_sim) if max_sim > 0 else 0.0
                score = _score_custom_subsidy(
                    c, extracted_keywords, summary, industries, sim_norm
                )
                reason = _build_custom_reason(c, industries, company_size)
                formatted = format_subsidy_info(c)
                formatted["relevance_score"] = score
                formatted["recommendation_reason"] = reason
                custom_scored.append(formatted)

    # ===== 3. 統合ランキング =====
    all_scored = scored_subsidies + custom_scored
    all_scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    results = all_scored[:max_results]

    total_found = len(api_result["subsidies"]) + len(custom_scored)

    # ===== 4. 注記・エラー =====
    signals = size_info.get("signals", [])
    signal_note = f"（判定根拠: {'、'.join(signals[:3])}）" if signals else ""

    if definitely_large:
        # 資本金・従業員数から大企業と判定 → 中小限定を除外
        notices.append(
            "大企業と判定しました" + signal_note +
            "。中小企業・小規模事業者に限定される制度は候補から除外しています。"
        )
        total_excluded = builtin_excluded + custom_excluded
        if total_excluded:
            notices.append(
                f"規模要件により{total_excluded}件の中小限定制度を除外しました。"
            )
    elif listed_not_large:
        # 上場だが規模は中小/不明 → 除外せず、みなし大企業の注意喚起のみ
        size_word = {"sme": "規模は中小企業に該当する可能性",
                     "unknown": "規模は不明"}.get(size_val, "規模は不明")
        notices.append(
            f"上場企業の可能性があります（{size_word}）{signal_note}。"
            "上場企業は多くの補助金で「みなし大企業」として対象外になる場合があります。"
            "一方で上場中小企業向けの制度もあるため、各制度の対象要件を必ずご確認ください。"
            "（該当する可能性のある制度は除外せず表示しています）"
        )
    if custom_scored:
        notices.append(
            f"手動登録のカスタム補助金 {len(custom_scored)} 件を候補に含めています。"
        )

    errors = list(api_result["errors"])
    if not results:
        errors.append(
            "条件に合致する補助金が見つかりませんでした。"
            "上場・大企業の場合は個別に公募情報をご確認ください。"
        )

    return {
        "results": results,
        "search_keywords": search_terms,
        "total_found": total_found,
        "notices": notices,
        "errors": errors,
    }
