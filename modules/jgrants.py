"""
補助金情報取得モジュール
JグランツAPI（デジタル庁）＋内蔵データ＋手動登録カスタム補助金を扱う。
"""

import html as _html
import logging
import re
import urllib.parse

import requests

from modules import custom_store

logger = logging.getLogger(__name__)

# JグランツAPI（デジタル庁公開API・APIキー不要）
_JGRANTS_API_BASE = "https://api.jgrants-portal.go.jp/exp/v1/public"
_API_TIMEOUT = 10  # 秒

# JグランツAPIの必須パラメータ（キーワード検索時）。
# keyword に加えて sort / order / acceptance を送らないと 400 Bad request になる。
_REQUIRED_SEARCH_PARAMS = {
    "sort": "created_date",  # 並び順の基準
    "order": "DESC",         # 降順
    "acceptance": "1",       # 1=募集中のみ / 0=募集終了含む
}

# Jグランツ補助金の公開詳細ページURL
_JGRANTS_SUBSIDY_PAGE = "https://www.jgrants-portal.go.jp/subsidy/"


def _call_jgrants_api(params: dict) -> dict:
    """
    JグランツAPIを呼び出す共通関数。
    Returns: {"result": list[dict], "metadata": dict} または {"error": str}
    """
    try:
        resp = requests.get(
            f"{_JGRANTS_API_BASE}/subsidies",
            params=params,
            timeout=_API_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data
    except requests.exceptions.Timeout:
        logger.warning("JグランツAPI タイムアウト: %s", params)
        return {"error": "JグランツAPIがタイムアウトしました"}
    except requests.exceptions.RequestException as e:
        logger.warning("JグランツAPI エラー: %s", e)
        return {"error": str(e)}


def _normalize_api_subsidy(item: dict) -> dict:
    """
    JグランツAPIのレスポンス1件を内部形式に変換する。

    実際のAPIフィールド（一覧）:
      id, name, title, subsidy_max_limit(数値), target_area_search,
      target_number_of_employees, institution_name,
      acceptance_start_datetime, acceptance_end_datetime
    詳細エンドポイントでは detail / use_purpose / industry / subsidy_rate /
    front_subsidy_detail_page_url 等が追加される。
    """
    title = item.get("title", "") or item.get("name", "")
    subsidy_id = item.get("id", "")

    # 対象（募集期間・対象地域・従業員規模から構成）
    target_parts = []
    if item.get("target_area_search"):
        target_parts.append(f"対象地域: {item['target_area_search']}")
    if item.get("target_number_of_employees"):
        target_parts.append(item["target_number_of_employees"])
    period = _fmt_period(
        item.get("acceptance_start_datetime"),
        item.get("acceptance_end_datetime"),
    )
    if period:
        target_parts.append(f"募集期間: {period}")
    target = " / ".join(target_parts) or "情報なし"

    # 説明（詳細のHTMLをプレーンテキスト化。一覧には無いので use_purpose を補助的に使う）
    description = _strip_html(item.get("detail") or "")
    if not description and item.get("use_purpose"):
        description = str(item["use_purpose"])

    # 公式URL: 詳細ページURL、無ければ id からJグランツ詳細ページを組み立てる
    official_url = item.get("front_subsidy_detail_page_url") or (
        f"{_JGRANTS_SUBSIDY_PAGE}{subsidy_id}" if subsidy_id else ""
    )

    return {
        "id": f"jgrants-{subsidy_id}" if subsidy_id else f"jgrants-{urllib.parse.quote(title[:20])}",
        "title": title,
        "subsidy_max_limit": _fmt_amount(item.get("subsidy_max_limit")) or "情報なし",
        "subsidy_rate": item.get("subsidy_rate") or "情報なし",
        "target": target,
        "organization": item.get("institution_name") or "情報なし",
        "description": description,
        "official_url": official_url,
        "eligible_scale": _detect_scale(item),
        "category_keywords": _extract_api_keywords(item),
        "source": "jgrants_api",
        "is_custom": False,
        "matched_keywords": [],
    }


def _fmt_period(start: str | None, end: str | None) -> str:
    """ISO日時文字列を「YYYY/MM/DD〜YYYY/MM/DD」形式に整形する。"""
    def _date(s):
        if not s:
            return ""
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s))
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else ""

    s, e = _date(start), _date(end)
    if s and e:
        return f"{s}〜{e}"
    return s or e


def _strip_html(text: str) -> str:
    """HTMLタグを除去してプレーンテキスト化する（説明文の表示用）。"""
    if not text:
        return ""
    # ブロック要素は改行に置換してから全タグ除去
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    # 連続する空白・改行を圧縮
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _fmt_amount(val) -> str:
    """補助上限額を表示用文字列に変換する。"""
    if val is None:
        return ""
    if isinstance(val, (int, float)) and val > 0:
        if val >= 100_000_000:
            return f"最大{val / 100_000_000:.0f}億円"
        if val >= 10_000:
            return f"最大{val / 10_000:.0f}万円"
        return f"最大{val:,.0f}円"
    return str(val) if val else ""


def _detect_scale(item: dict) -> str:
    """APIレスポンスから対象企業規模を推定する。"""
    target_text = " ".join([
        str(item.get("target_number_of_employees") or ""),
        str(item.get("target_area_detail") or ""),
        str(item.get("detail") or ""),
    ]).lower()
    if "大企業" in target_text and "中小" not in target_text:
        return "large"
    if any(w in target_text for w in ["中小企業", "小規模", "中堅"]):
        return "sme"
    return "all"


def _extract_api_keywords(item: dict) -> list[str]:
    """APIレスポンスからカテゴリキーワードを抽出する。"""
    keywords: list[str] = []
    # industry フィールドは「製造業 / 建設業 / ...」形式なので分割する
    industry = item.get("industry")
    if industry and isinstance(industry, str):
        keywords.extend(
            part.strip() for part in re.split(r"[/、,]", industry) if part.strip()
        )
    # use_purpose フィールド（補助目的）
    purpose = item.get("use_purpose") or item.get("purpose") or ""
    if purpose and isinstance(purpose, str):
        keywords.append(purpose.strip())
    return keywords


def _fetch_subsidy_detail(subsidy_id: str) -> dict:
    """
    JグランツAPIの詳細エンドポイントで1件の詳細情報を取得する。
    一覧には無い subsidy_rate / detail / industry / 実際の上限額を補完するために使う。
    Returns: 詳細フィールドのdict（失敗時は空dict）
    """
    if not subsidy_id:
        return {}
    try:
        resp = requests.get(
            f"{_JGRANTS_API_BASE}/subsidies/id/{subsidy_id}",
            timeout=_API_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result") or []
        return result[0] if result and isinstance(result[0], dict) else {}
    except requests.exceptions.RequestException as e:
        logger.info("Jグランツ詳細取得失敗 id=%s: %s", subsidy_id, e)
        return {}


def _extract_total_count(data: dict, fallback: int) -> int:
    """APIレスポンスの metadata.resultset.count から総件数を取り出す。"""
    metadata = data.get("metadata") or {}
    resultset = metadata.get("resultset") or {}
    count = resultset.get("count")
    if isinstance(count, int):
        return count
    # 旧フィールド名の保険
    return data.get("totalCount") or data.get("total_count") or fallback


def check_api_availability() -> dict:
    """
    JグランツAPIの疎通確認を行う。
    keyword は2文字以上が必須。sort/order/acceptance も必須。
    Returns: {"available": bool, "message": str}
    """
    result = _call_jgrants_api({"keyword": "補助金", **_REQUIRED_SEARCH_PARAMS})
    if result.get("error"):
        return {"available": False, "message": result["error"]}
    return {"available": True, "message": "JグランツAPI 接続OK"}


def search_subsidies_from_api(keyword: str, limit: int = 10) -> dict:
    """
    JグランツAPIでキーワード検索する。
    Returns: {"subsidies": list[dict], "total_count": int, "error": str|None}
    """
    # keyword は2文字以上が必須。1文字の場合はAPIが400を返すためスキップ。
    if len(keyword.strip()) < 2:
        return {"subsidies": [], "total_count": 0, "error": None}

    data = _call_jgrants_api({"keyword": keyword, **_REQUIRED_SEARCH_PARAMS})
    if data.get("error"):
        return {"subsidies": [], "total_count": 0, "error": data["error"]}

    raw_list = [
        item for item in (data.get("result") or data.get("subsidies") or [])
        if isinstance(item, dict)
    ][:limit]

    # 一覧には金額・補助率・説明が無いため、返す件数分だけ詳細を取得して補完する
    subsidies = []
    for item in raw_list:
        detail = _fetch_subsidy_detail(item.get("id", ""))
        merged = {**item, **detail} if detail else item
        subsidies.append(_normalize_api_subsidy(merged))

    total = _extract_total_count(data, len(subsidies))
    return {"subsidies": subsidies, "total_count": total, "error": None}


def get_custom_subsidies() -> list[dict]:
    """手動登録されたカスタム補助金を読み込む（source="manual"）。"""
    return custom_store.load_custom_subsidies()


def _google_search_url(query: str) -> str:
    """Google検索URLを生成する（公式URLが無い場合のフォールバック）。"""
    return f"https://www.google.com/search?q={urllib.parse.quote(query + ' 補助金 公式')}"


def _jgrants_search_url(query: str) -> str:
    """Jグランツポータルの検索URLを生成する。"""
    return (
        "https://www.jgrants-portal.go.jp/subsidies?keyword="
        + urllib.parse.quote(query)
    )


# 主要な補助金の内蔵データ（2025-2026年時点の最新名称）
BUILTIN_SUBSIDIES = [
    {
        "id": "shoryokuka-it",
        "title": "中小企業省力化投資補助金（カタログ型）",
        "subsidy_max_limit": "最大1,500万円",
        "subsidy_rate": "1/2",
        "target": "人手不足に悩む中小企業がITツール・ロボット等の省力化製品を導入する費用を支援",
        "organization": "中小企業庁 / 中小企業基盤整備機構",
        "category_keywords": ["IT", "DX", "デジタル化", "システム", "ソフトウェア",
                             "クラウド", "業務効率化", "IT導入", "テレワーク",
                             "ロボット", "AI", "自動化", "省力化", "IoT",
                             "スマート工場", "先端技術", "人手不足"],
        "description": "人手不足の中小企業に対し、省力化に資するITツールやロボット等のカタログ製品の導入を支援（旧IT導入補助金を統合）",
        "official_url": "https://shoryokuka.smrj.go.jp/",
        "eligible_scale": "sme",
    },
    {
        "id": "monodukuri-hojo",
        "title": "ものづくり・商業・サービス生産性向上促進補助金",
        "subsidy_max_limit": "最大1,250万円（通常枠）",
        "subsidy_rate": "1/2〜2/3",
        "target": "中小企業・小規模事業者等が革新的な製品・サービスの開発、生産プロセス改善を行う際の設備投資等を支援",
        "organization": "全国中小企業団体中央会",
        "category_keywords": ["製造", "ものづくり", "設備投資", "生産性向上", "開発",
                             "試作", "新製品", "新サービス", "革新的", "加工"],
        "description": "革新的サービス開発・試作品開発・生産プロセスの改善を行うための設備投資等を支援",
        "official_url": "https://portal.monodukuri-hojo.jp/",
        "eligible_scale": "sme",
    },
    {
        "id": "jizokuka-hojo",
        "title": "小規模事業者持続化補助金",
        "subsidy_max_limit": "最大200万円（特別枠）",
        "subsidy_rate": "2/3",
        "target": "小規模事業者が販路開拓等に取り組む費用を支援",
        "organization": "日本商工会議所 / 全国商工会連合会",
        "category_keywords": ["販路開拓", "小規模事業者", "販路拡大", "広告",
                             "ウェブサイト", "展示会", "チラシ", "販売促進"],
        "description": "小規模事業者の販路開拓等の取組を支援",
        "official_url": "https://s23.jizokukahojokin.info/",
        "eligible_scale": "sme",
    },
    {
        "id": "jigyo-saikouchiku",
        "title": "事業再構築補助金",
        "subsidy_max_limit": "最大1億円",
        "subsidy_rate": "1/2〜3/4",
        "target": "新分野展開、事業転換、業種転換、業態転換、事業再編に取り組む中小企業等を支援",
        "organization": "中小企業庁",
        "category_keywords": ["事業再構築", "新分野展開", "事業転換", "業態転換",
                             "新規事業", "業種転換", "事業再編"],
        "description": "経済社会の変化に対応するための事業再構築を支援",
        "official_url": "https://jigyou-saikouchiku.go.jp/",
        "eligible_scale": "sme",
    },
    {
        "id": "sho-energy",
        "title": "省エネルギー投資促進に向けた支援補助金",
        "subsidy_max_limit": "最大15億円",
        "subsidy_rate": "1/3〜1/2",
        "target": "省エネルギー性能の高い設備への更新を支援",
        "organization": "一般社団法人環境共創イニシアチブ（SII）",
        "category_keywords": ["省エネ", "脱炭素", "エネルギー", "環境", "設備更新",
                             "カーボンニュートラル", "CO2削減", "電力"],
        "description": "省エネルギー性能の高い設備への更新等を支援し、エネルギー消費効率の改善を促進",
        "official_url": "https://sii.or.jp/",
        "eligible_scale": "all",
    },
    {
        "id": "kaigai-tenkai",
        "title": "海外展開・事業再編資金",
        "subsidy_max_limit": "最大7,200万円（融資）",
        "subsidy_rate": "低利融資",
        "target": "海外展開を図る中小企業の設備資金・運転資金を支援",
        "organization": "日本政策金融公庫",
        "category_keywords": ["海外展開", "輸出", "グローバル", "国際", "貿易",
                             "インバウンド", "海外進出"],
        "description": "海外展開を図る中小企業者向けの融資制度",
        "official_url": "https://www.jfc.go.jp/",
        "eligible_scale": "sme",
    },
    {
        "id": "jinzai-kaihatsu",
        "title": "人材開発支援助成金",
        "subsidy_max_limit": "経費の最大75%＋賃金助成",
        "subsidy_rate": "45%〜75%",
        "target": "従業員のキャリア形成を促進するための職業訓練等を実施する事業主を支援",
        "organization": "厚生労働省",
        "category_keywords": ["人材育成", "研修", "教育", "訓練", "スキルアップ",
                             "人材", "雇用", "キャリア"],
        "description": "職務に関連した職業訓練を実施した場合に訓練経費や賃金の一部を助成",
        "official_url": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/koyou_roudou/koyou/kyufukin/d01-1.html",
        "eligible_scale": "all",
    },
    {
        "id": "sougyou-shien",
        "title": "創業支援等事業者補助金",
        "subsidy_max_limit": "最大1,000万円",
        "subsidy_rate": "2/3",
        "target": "新たに創業する者や第二創業を行う者を支援",
        "organization": "中小企業庁",
        "category_keywords": ["創業", "起業", "スタートアップ", "開業",
                             "第二創業", "ベンチャー", "新規事業"],
        "description": "創業支援等事業計画に従って行う事業を支援",
        "official_url": "https://www.chusho.meti.go.jp/",
        "eligible_scale": "sme",
    },
    {
        "id": "jigyou-shoukei",
        "title": "事業承継・引継ぎ補助金",
        "subsidy_max_limit": "最大800万円",
        "subsidy_rate": "1/2〜2/3",
        "target": "事業承継やM&Aを契機とした経営革新等への挑戦に要する費用を支援",
        "organization": "中小企業庁",
        "category_keywords": ["事業承継", "M&A", "後継者", "引継ぎ", "経営革新",
                             "第二創業"],
        "description": "事業承継やM&Aを契機とした経営革新等の費用を補助",
        "official_url": "https://jsh.go.jp/",
        "eligible_scale": "sme",
    },
    {
        "id": "chiiki-kasseika",
        "title": "地域経済循環創造事業交付金",
        "subsidy_max_limit": "最大5,000万円",
        "subsidy_rate": "1/2",
        "target": "地域の資源と資金を活用して地域密着型事業を立ち上げる民間事業者を支援",
        "organization": "総務省",
        "category_keywords": ["地域活性化", "地方創生", "まちづくり", "地域",
                             "観光", "農業", "6次産業化", "地方"],
        "description": "地域密着型事業の立ち上げを支援",
        "official_url": "https://www.soumu.go.jp/",
        "eligible_scale": "all",
    },
    {
        "id": "kenkyu-kaihatsu",
        "title": "成長型中小企業等研究開発支援事業（Go-Tech事業）",
        "subsidy_max_limit": "最大9,750万円",
        "subsidy_rate": "2/3",
        "target": "中小企業等が大学・公設試験研究機関等と連携して行う研究開発を支援",
        "organization": "中小企業庁",
        "category_keywords": ["研究開発", "技術革新", "イノベーション", "特許",
                             "大学連携", "産学連携", "先端技術"],
        "description": "ものづくり基盤技術及びサービスの高度化に向けた研究開発を支援",
        "official_url": "https://www.chusho.meti.go.jp/keiei/sapoin/index.html",
        "eligible_scale": "sme",
    },
    {
        "id": "green-hojo",
        "title": "GX（グリーントランスフォーメーション）関連補助金",
        "subsidy_max_limit": "事業規模による",
        "subsidy_rate": "1/3〜2/3",
        "target": "脱炭素・GXに取り組む企業の設備導入・技術開発を支援",
        "organization": "経済産業省 / 環境省",
        "category_keywords": ["脱炭素", "GX", "グリーン", "再生可能エネルギー",
                             "太陽光", "EV", "蓄電池", "水素"],
        "description": "GXに向けた設備導入・技術開発等を支援",
        "official_url": "https://www.meti.go.jp/policy/energy_environment/global_warming/",
        "eligible_scale": "all",
    },
]


def search_subsidies_builtin(keyword: str) -> list[dict]:
    """内蔵データからキーワードに一致する補助金を検索する。"""
    results = []
    keyword_lower = keyword.lower()

    for subsidy in BUILTIN_SUBSIDIES:
        category_match = any(
            keyword_lower in cat_kw.lower() or cat_kw.lower() in keyword_lower
            for cat_kw in subsidy["category_keywords"]
        )
        text_match = (
            keyword_lower in subsidy["title"].lower()
            or keyword_lower in subsidy.get("description", "").lower()
            or keyword_lower in subsidy.get("target", "").lower()
        )
        if category_match or text_match:
            results.append(subsidy.copy())

    return results


def search_subsidies(keyword: str, limit: int = 10) -> dict:
    """
    キーワードで補助金を検索する。
    JグランツAPIを優先し、失敗時は内蔵データにフォールバックする。
    """
    # 1) JグランツAPI
    api_result = search_subsidies_from_api(keyword, limit=limit)
    if not api_result["error"] and api_result["subsidies"]:
        return api_result

    # 2) フォールバック: 内蔵データ
    if api_result["error"]:
        logger.info("JグランツAPI失敗、内蔵データにフォールバック: %s", api_result["error"])
    builtin_results = search_subsidies_builtin(keyword)
    return {
        "subsidies": builtin_results[:limit],
        "total_count": len(builtin_results),
        "error": api_result["error"],  # エラー情報は呼び出し元に伝える
    }


def search_subsidies_multi_keywords(
    keywords: list[str], limit_per_keyword: int = 5
) -> dict:
    """
    複数キーワードで補助金を検索し、結果を統合する。
    JグランツAPIと内蔵データの両方を検索してマージする。
    """
    all_subsidies: dict[str, dict] = {}
    keyword_results: dict[str, list] = {}
    errors: list[str] = []
    api_errors: list[str] = []

    for keyword in keywords:
        # --- JグランツAPI ---
        api_result = search_subsidies_from_api(keyword, limit=limit_per_keyword)
        if api_result["error"]:
            api_errors.append(api_result["error"])
        api_subsidies = api_result["subsidies"]

        # --- 内蔵データ（常に検索してAPIの不足を補う）---
        builtin_subsidies = search_subsidies_builtin(keyword)[:limit_per_keyword]

        combined = api_subsidies + builtin_subsidies
        keyword_results[keyword] = combined

        for subsidy in combined:
            sub_id = subsidy.get("id", "")
            if not sub_id:
                continue
            if sub_id not in all_subsidies:
                subsidy["matched_keywords"] = [keyword]
                all_subsidies[sub_id] = subsidy
            elif keyword not in all_subsidies[sub_id]["matched_keywords"]:
                all_subsidies[sub_id]["matched_keywords"].append(keyword)

    # APIエラーは重複排除して1件だけ通知
    if api_errors:
        unique_err = api_errors[0]
        errors.append(f"JグランツAPI: {unique_err}（内蔵データで補完しています）")

    return {
        "subsidies": list(all_subsidies.values()),
        "keyword_results": keyword_results,
        "errors": errors,
    }


def format_subsidy_info(subsidy: dict) -> dict:
    """補助金情報を表示用にフォーマットする。"""
    title = subsidy.get("title", "タイトル不明")

    # 公式サイトURL（直接遷移用）。無ければJグランツポータル検索にフォールバック
    official_url = subsidy.get("official_url") or _jgrants_search_url(title)
    # 補助的な検索リンク（公式URLが古い場合の保険）
    search_url = _google_search_url(title)

    return {
        "id": subsidy.get("id", "N/A"),
        "title": title,
        "subsidy_max_limit": subsidy.get("subsidy_max_limit", "情報なし"),
        "subsidy_rate": subsidy.get("subsidy_rate", "情報なし"),
        "target": subsidy.get("target", "情報なし"),
        "organization": subsidy.get("organization", "情報なし"),
        "official_url": official_url,
        "search_url": search_url,
        "eligible_scale": subsidy.get("eligible_scale", "sme"),
        "matched_keywords": subsidy.get("matched_keywords", []),
        "description": subsidy.get("description", ""),
        # データ区分（Jグランツ内蔵 / 手動登録カスタム）
        "source": subsidy.get("source", "builtin"),
        "is_custom": subsidy.get("is_custom", False),
        # カスタム補助金の付加情報（RAG・表示用）
        "target_industries": subsidy.get("target_industries", []),
        "target_expenses": subsidy.get("target_expenses", ""),
    }
