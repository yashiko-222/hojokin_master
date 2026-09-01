"""
企業HPクロール＆キーワード抽出モジュール
指定されたURLからWebページを取得し、日本語キーワードを抽出する。
"""

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from janome.tokenizer import Tokenizer


# Janomeトークナイザーの初期化（シングルトン）
_tokenizer = Tokenizer()

# 除外する一般的な単語（ストップワード）
STOP_WORDS = {
    "こと", "もの", "ため", "それ", "これ", "ここ", "そこ",
    "よう", "ところ", "なか", "うち", "ほう", "あと", "まま",
    "つもり", "はず", "わけ", "とき", "ほか", "だけ", "まで",
    "から", "より", "など", "くらい", "について", "として",
    "における", "に関する", "による", "に対する",
    "株式会社", "有限会社", "合同会社", "お問い合わせ",
    "トップ", "ページ", "サイト", "ホーム", "メニュー",
    "プライバシー", "ポリシー", "クッキー", "コピーライト",
    "all", "rights", "reserved", "copyright", "home", "menu",
    "top", "page", "site", "contact", "about", "news",
}

# 企業HPで重要になりやすいキーワードカテゴリ（本業・事業内容を表す具体語）
BUSINESS_KEYWORDS = {
    "製造", "開発", "設計", "生産", "加工", "建設", "施工",
    "ロボット", "自動化",
    "研究", "技術", "特許",
    "輸出", "海外", "グローバル", "国際",
    "人材", "育成", "雇用",
    "地域", "地方創生", "まちづくり",
    "農業", "漁業", "林業", "食品",
    "観光", "インバウンド", "宿泊",
    "医療", "福祉", "介護", "ヘルスケア",
    "リサイクル", "廃棄物",
    "設備投資", "事業承継", "事業再構築", "新分野展開",
    "販路開拓", "生産性向上",
    "小規模事業者", "中小企業", "スタートアップ", "創業",
}

# 汎用トレンドワード（どの業種のHPにも登場しやすい流行語）。
# 単発出現では本業を表さないため、加点を抑制する。
GENERIC_TREND_WORDS = {
    "IT", "DX", "AI", "IoT", "SDGs", "SDGS", "sdgs", "ESG",
    "環境", "デジタル", "デジタル化", "クラウド", "省エネ",
    "脱炭素", "カーボンニュートラル", "再生可能エネルギー",
    "イノベーション", "グリーン", "サステナビリティ", "サステナブル",
    "業務効率化", "働き方改革", "テレワーク", "オンライン",
}

# 業種推定用の辞書（業種名 -> 判定キーワード群）
INDUSTRY_KEYWORDS = {
    "製造業": ["製造", "工場", "生産", "加工", "部品", "組立", "ものづくり",
             "金属", "機械", "製品開発", "量産", "試作"],
    "IT・情報通信業": ["ソフトウェア", "システム開発", "アプリ", "web", "クラウド",
                    "SaaS", "AI", "DX", "プログラミング", "IT", "デジタル",
                    "データ", "セキュリティ"],
    "建設業": ["建設", "施工", "工事", "建築", "土木", "設計", "リフォーム",
             "住宅", "不動産開発"],
    "小売業": ["販売", "店舗", "ショップ", "小売", "通販", "EC", "商品",
             "アパレル", "雑貨"],
    "飲食業": ["飲食", "レストラン", "カフェ", "居酒屋", "料理", "メニュー",
             "食堂", "グルメ"],
    "卸売・商社": ["卸売", "商社", "仕入", "流通", "貿易", "輸出入", "問屋"],
    "医療・福祉": ["医療", "病院", "クリニック", "介護", "福祉", "看護",
                "リハビリ", "調剤", "ヘルスケア"],
    "農林水産業": ["農業", "農園", "漁業", "水産", "林業", "畜産", "栽培",
                "6次産業", "食品加工"],
    "運輸・物流業": ["運送", "物流", "配送", "輸送", "倉庫", "トラック",
                 "ロジスティクス"],
    "サービス業": ["サービス", "コンサルティング", "支援", "代行", "清掃",
                "警備", "人材"],
    "観光・宿泊業": ["観光", "宿泊", "ホテル", "旅館", "旅行", "インバウンド",
                 "ツアー"],
    "金融・保険業": ["金融", "保険", "融資", "投資", "証券", "銀行", "ファイナンス"],
    "教育業": ["教育", "学習", "研修", "スクール", "塾", "講座", "トレーニング"],
    "不動産業": ["不動産", "賃貸", "売買", "仲介", "物件", "管理"],
}


def fetch_page(url: str, timeout: int = 15) -> str:
    """
    指定URLのHTMLを取得する。

    Args:
        url: 取得するページのURL
        timeout: タイムアウト秒数

    Returns:
        HTMLテキスト
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def extract_text_from_html(html: str) -> str:
    """
    HTMLからテキストを抽出する（スクリプト・スタイル等を除外）。

    Args:
        html: HTMLテキスト

    Returns:
        クリーンなテキスト
    """
    soup = BeautifulSoup(html, "html.parser")

    # 不要なタグを削除
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    # メタ情報からも抽出
    meta_texts = []
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        if content and meta.get("name") in ["description", "keywords"]:
            meta_texts.append(content)

    # タイトル
    title = soup.title.string if soup.title else ""

    # 本文テキスト
    body_text = soup.get_text(separator="\n", strip=True)

    # 全テキストを結合
    full_text = f"{title}\n{' '.join(meta_texts)}\n{body_text}"
    return full_text


def extract_keywords(text: str, top_n: int = 30) -> list[dict]:
    """
    テキストから重要なキーワードを抽出する。

    Args:
        text: 解析するテキスト
        top_n: 返すキーワードの最大数

    Returns:
        キーワードとスコアのリスト [{"keyword": str, "score": float}, ...]
    """
    # Janomeで形態素解析
    tokens = _tokenizer.tokenize(text)

    # 名詞・形容詞・動詞（サ変接続含む）を抽出
    words = []
    for token in tokens:
        part_of_speech = token.part_of_speech.split(",")
        base_form = token.base_form if token.base_form != "*" else token.surface

        # 名詞（一般、サ変接続、固有名詞）を対象
        if part_of_speech[0] == "名詞" and part_of_speech[1] in [
            "一般", "サ変接続", "固有名詞", "形容動詞語幹"
        ]:
            if len(base_form) >= 2 and base_form.lower() not in STOP_WORDS:
                words.append(base_form)

    # 出現回数をカウント
    word_counts = Counter(words)

    # スコア計算：出現回数をベースに、本業語は加点・汎用トレンド語は抑制
    scored_words = {}
    for word, count in word_counts.items():
        score = float(count)

        if word in GENERIC_TREND_WORDS:
            # 汎用トレンドワード（DX/IT/環境等）。
            # 単発〜少数の出現では本業を表さないため加点しない。
            # 何度も出る（＝本当に主要テーマ）場合のみ控えめに加点。
            if count >= 4:
                score *= 1.2
            else:
                # ボーナスなし。むしろ単発なら軽く抑制して具体語を上位に
                score = min(score, 2.0)
        elif word in BUSINESS_KEYWORDS:
            # 本業・事業内容を表す具体語は加点
            score *= 2.0

        scored_words[word] = score

    # スコア順にソート
    sorted_words = sorted(scored_words.items(), key=lambda x: x[1], reverse=True)

    # 上位N件を返す
    results = []
    for word, score in sorted_words[:top_n]:
        results.append({"keyword": word, "score": round(score, 2)})

    return results


def estimate_industries(text: str, keywords: list[dict]) -> list[dict]:
    """
    テキストとキーワードから業種を推定する。

    Args:
        text: 解析対象テキスト
        keywords: 抽出済みキーワード

    Returns:
        [{"industry": str, "score": int}, ...]（スコア順、上位のみ）
    """
    text_lower = text.lower()
    keyword_set = {kw["keyword"].lower() for kw in keywords}

    industry_scores = {}
    for industry, ind_keywords in INDUSTRY_KEYWORDS.items():
        score = 0
        for ind_kw in ind_keywords:
            ind_kw_lower = ind_kw.lower()
            # 本文中の出現回数
            score += text_lower.count(ind_kw_lower)
            # 抽出キーワードに含まれれば加点
            if ind_kw_lower in keyword_set:
                score += 5
        if score > 0:
            industry_scores[industry] = score

    sorted_industries = sorted(
        industry_scores.items(), key=lambda x: x[1], reverse=True
    )

    # スコアが有意な上位3業種を返す
    results = [
        {"industry": ind, "score": sc}
        for ind, sc in sorted_industries[:3]
        if sc >= 2
    ]
    return results


# 上場・大企業を示すシグナル語
LISTED_SIGNALS = [
    "上場", "東証", "東京証券取引所", "プライム市場", "スタンダード市場",
    "グロース市場", "証券コード", "有価証券報告書", "株主総会",
    "適時開示", "決算説明", "コーポレートガバナンス", "IR情報",
    "投資家情報", "統合報告書", "四半期報告", "配当", "ＩＲ",
    "ir information", "investor relations", "stock exchange",
    "nasdaq", "tse", "ipo",
]
# 大企業寄りのシグナル語（グループ・グローバル大規模）
BIG_COMPANY_SIGNALS = [
    "グループ会社", "連結", "海外拠点", "本社ビル", "従業員数",
    "資本金", "設立", "事業所",
]

# 中小企業基本法に基づく業種別の中小企業の上限
# （資本金 または 従業員数 のいずれかを満たせば中小企業）
# 単位: capital=円, employees=人
SME_CRITERIA = {
    # 製造業・建設業・運輸業・その他: 3億円 / 300人
    "manufacturing_other": {"capital": 300_000_000, "employees": 300},
    # 卸売業: 1億円 / 100人
    "wholesale": {"capital": 100_000_000, "employees": 100},
    # サービス業: 5,000万円 / 100人
    "service": {"capital": 50_000_000, "employees": 100},
    # 小売業: 5,000万円 / 50人
    "retail": {"capital": 50_000_000, "employees": 50},
}

# 推定業種名 → 中小企業基本法の区分
INDUSTRY_TO_SME_CATEGORY = {
    "製造業": "manufacturing_other",
    "建設業": "manufacturing_other",
    "運輸・物流業": "manufacturing_other",
    "農林水産業": "manufacturing_other",
    "IT・情報通信業": "manufacturing_other",
    "金融・保険業": "manufacturing_other",
    "教育業": "manufacturing_other",
    "不動産業": "manufacturing_other",
    "医療・福祉": "manufacturing_other",
    "卸売・商社": "wholesale",
    "サービス業": "service",
    "観光・宿泊業": "service",
    "小売業": "retail",
    "飲食業": "service",  # 飲食サービス業はサービス業基準（緩い側）
}


def _extract_capital_yen(text: str) -> int | None:
    """テキストから資本金を抽出し、円単位で返す。取れなければ None。"""
    # 「資本金 3億円」「資本金 3億5,000万円」
    m = re.search(r"資本金[^0-9]{0,8}([0-9,]+)\s*億(?:\s*([0-9,]+)\s*万)?", text)
    if m:
        try:
            oku = int(m.group(1).replace(",", ""))
            man = int(m.group(2).replace(",", "")) if m.group(2) else 0
            return oku * 100_000_000 + man * 10_000
        except ValueError:
            pass
    # 「資本金 5,000万円」
    m = re.search(r"資本金[^0-9]{0,8}([0-9,]+)\s*万", text)
    if m:
        try:
            return int(m.group(1).replace(",", "")) * 10_000
        except ValueError:
            pass
    # 「資本金 50,000,000円」
    m = re.search(r"資本金[^0-9]{0,8}([0-9,]{4,})\s*円", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_employees(text: str) -> int | None:
    """テキストから従業員数を抽出する。取れなければ None。"""
    m = re.search(r"(従業員|社員)[数（(]*\s*[:：]?\s*([0-9,]{2,})\s*[名人]", text)
    if m:
        try:
            return int(m.group(2).replace(",", ""))
        except ValueError:
            pass
    return None


def estimate_company_size(text: str, industries: list[dict] | None = None) -> dict:
    """
    HPのテキストと推定業種から、企業規模（中小企業か否か）を推定する。

    中小企業基本法に基づき、業種別の「資本金 または 従業員数」の
    いずれか一方でも基準内なら中小企業と判定する（or条件）。
    複数業種に該当する場合は、中小に該当しやすい（基準が緩い）方を採用する。

    Args:
        text: クロールした全テキスト
        industries: 推定業種リスト [{"industry": str, "score": int}, ...]

    Returns:
        {
            "size": "large" | "sme" | "unknown",  # 大企業/中小/不明
            "is_listed": bool,                     # 上場企業の可能性
            "signals": list[str],                  # 判定根拠
        }
    """
    text_lower = text.lower()
    found_listed = [sig for sig in LISTED_SIGNALS if sig.lower() in text_lower]

    is_listed = len(found_listed) >= 2 or (
        len(found_listed) >= 1 and any(
            s in text for s in ["上場", "東証", "証券コード", "有価証券報告書"]
        )
    )

    # 業種別の中小基準を決定（複数業種なら各上限の最大＝最も緩い方を採用）
    categories = []
    if industries:
        for ind in industries:
            cat = INDUSTRY_TO_SME_CATEGORY.get(ind.get("industry", ""))
            if cat:
                categories.append(cat)
    if not categories:
        # 業種不明時は最も緩い基準（製造業・その他: 3億円/300人）で判定
        categories = ["manufacturing_other"]

    cap_limit = max(SME_CRITERIA[c]["capital"] for c in categories)
    emp_limit = max(SME_CRITERIA[c]["employees"] for c in categories)

    # 資本金・従業員数を抽出
    capital = _extract_capital_yen(text)
    employees = _extract_employees(text)

    signals = list(found_listed)

    # or条件による中小判定
    is_sme_by_number = False
    is_large_by_number = False
    got_number = capital is not None or employees is not None

    if capital is not None and capital <= cap_limit:
        is_sme_by_number = True
    if employees is not None and employees <= emp_limit:
        is_sme_by_number = True
    if got_number and not is_sme_by_number:
        # 取得できた数値がいずれも基準を超えている → 大企業
        is_large_by_number = True

    # 判定根拠の文言を作成
    if capital is not None:
        oku_val = capital / 100_000_000
        cap_str = f"{oku_val:.0f}" if oku_val == int(oku_val) else f"{oku_val:.1f}"
        signals.append(f"資本金 約{cap_str}億円")
    if employees is not None:
        signals.append(f"従業員数 約{employees}人")

    # 規模の総合判定（上場か否かとは独立に、資本金・従業員数の事実で判定）
    # ※ 上場していても規模が中小のケース（上場中小企業）があるため、
    #    is_listed は size に直結させず、別軸のフラグとして保持する。
    if is_large_by_number:
        size = "large"
    elif is_sme_by_number:
        size = "sme"
    elif "中小企業" in text or "小規模事業者" in text:
        size = "sme"
    else:
        size = "unknown"

    if is_listed:
        signals.insert(0, "上場企業の可能性")

    return {
        "size": size,
        "is_listed": is_listed,
        "signals": signals[:6],
    }


def extract_summary(html: str, max_length: int = 300) -> str:
    """
    企業HPから事業概要の要約文を抽出する。
    meta descriptionや主要な見出し・段落を優先的に取得する。

    Args:
        html: HTMLテキスト
        max_length: 要約の最大文字数

    Returns:
        事業概要の文字列
    """
    soup = BeautifulSoup(html, "html.parser")

    summary_parts = []

    def _add(text: str, min_len: int = 20, max_len: int = 400):
        """重複を避けて要約候補を追加する。"""
        t = (text or "").strip()
        t = re.sub(r"\s+", " ", t)
        if min_len <= len(t) <= max_len and all(t not in p and p not in t
                                                for p in summary_parts):
            summary_parts.append(t)
            return True
        return False

    # 1. meta description を最優先
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        _add(meta_desc["content"], min_len=10)

    # 2. og:description
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        _add(og_desc["content"], min_len=10)

    # 不要タグを除去
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    # 3. 事業内容・会社概要らしき見出し直後の文章（複数箇所を合成）
    business_headings = [
        "事業内容", "事業案内", "私たち", "について", "会社概要", "サービス",
        "業務内容", "ビジネス", "製品", "商品", "強み", "特長", "特徴",
        "取り組み", "ソリューション", "できること", "提供",
    ]
    for heading in soup.find_all(["h1", "h2", "h3"]):
        if len(summary_parts) >= 4:
            break
        heading_text = heading.get_text(strip=True)
        if any(bh in heading_text for bh in business_headings):
            # 見出し直後の段落・divから本文を取得
            nxt = heading.find_next(["p", "div", "li"])
            if nxt:
                _add(nxt.get_text(strip=True))

    # 4. メインコンテンツの意味のある段落（不足時の補完）
    if len(summary_parts) < 2:
        main = soup.find("main") or soup.find(
            attrs={"id": re.compile(r"main|content", re.I)}) or soup
        for p in main.find_all("p"):
            if len(summary_parts) >= 3:
                break
            _add(p.get_text(strip=True), min_len=30)

    summary = " / ".join(summary_parts)
    if len(summary) > max_length:
        summary = summary[:max_length] + "…"

    return summary or "事業概要を抽出できませんでした。"


# 重要サブページ判定用（URLパス・アンカーテキスト共通）
# キーワード → 優先度スコア（大きいほど優先的に巡回）
PRIORITY_PAGE_HINTS = {
    # 事業内容・サービス・製品（本業把握に最重要）
    "service": 5, "business": 5, "product": 5, "solution": 4,
    "事業": 5, "事業内容": 5, "サービス": 5, "製品": 5, "商品": 5,
    "ソリューション": 4, "strength": 4, "強み": 4, "特長": 3, "技術": 4,
    # 会社概要（規模・業種把握）
    "about": 3, "company": 3, "corporate": 3, "profile": 3,
    "会社概要": 3, "企業情報": 3, "会社案内": 3, "私たち": 2,
}


def get_internal_links(html: str, base_url: str, max_links: int = 6) -> list[str]:
    """
    ページ内の内部リンクを取得する（サブページもクロールするため）。
    「事業内容」「サービス」「会社概要」等の重要ページを優先度スコアで並べ替える。

    Args:
        html: HTMLテキスト
        base_url: ベースURL
        max_links: 取得する最大リンク数

    Returns:
        内部リンクURL（重要度順）のリスト
    """
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc

    # URL → スコア（アンカーテキストとURLパスの両方で判定）
    link_scores: dict[str, int] = {}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        # 同一ドメインのHTMLページのみ（画像・PDF等を除外）
        if parsed.netloc != base_domain:
            continue
        if any(full_url.lower().endswith(ext) for ext in
               [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4"]):
            continue
        # トップページ自身は除外
        if full_url.rstrip("/") == base_url.rstrip("/"):
            continue

        anchor = a_tag.get_text(strip=True).lower()
        target = (parsed.path.lower() + " " + anchor)

        score = 0
        for hint, pts in PRIORITY_PAGE_HINTS.items():
            if hint.lower() in target:
                score = max(score, pts)

        # 既出URLはより高いスコアを採用
        if full_url not in link_scores or score > link_scores[full_url]:
            link_scores[full_url] = score

    # スコア降順で並べ替え（同点はURLの浅さ優先）
    def _depth(u: str) -> int:
        return len([p for p in urlparse(u).path.split("/") if p])

    sorted_links = sorted(
        link_scores.keys(),
        key=lambda u: (link_scores[u], -_depth(u)),
        reverse=True,
    )

    # スコア0（重要語なし）のページは、重要ページが足りない場合のみ補完
    prioritized = [u for u in sorted_links if link_scores[u] > 0]
    fillers = [u for u in sorted_links if link_scores[u] == 0]
    result = prioritized[:max_links]
    if len(result) < max_links:
        result += fillers[: max_links - len(result)]
    return result


# 都道府県リスト（HP本文からの所在地抽出・地域フィルタ用）
_PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


def extract_prefecture(text: str) -> str | None:
    """
    HP本文から企業所在地の都道府県を推定する。
    「本社」「所在地」「住所」の近くに出現する都道府県を優先し、
    無ければ本文中で最も多く出現する都道府県を採用する。
    抽出できなければ None（この場合は地域フィルタを適用しない）。
    """
    if not text:
        return None

    # 1) 所在地キーワードの近傍を優先
    for anchor in ("本社", "所在地", "住所", "本店"):
        idx = text.find(anchor)
        if idx != -1:
            window = text[idx: idx + 60]
            for pref in _PREFECTURES:
                if pref in window:
                    return pref

    # 2) 本文中の出現回数が最も多い都道府県
    counts = {p: text.count(p) for p in _PREFECTURES if p in text}
    if counts:
        return max(counts, key=counts.get)
    return None


def crawl_and_extract(url: str, crawl_subpages: bool = True) -> dict:
    """
    企業HPをクロールしてキーワードを抽出するメイン関数。

    Args:
        url: 企業HPのURL
        crawl_subpages: サブページもクロールするか

    Returns:
        {
            "url": str,
            "keywords": list[dict],
            "summary": str,           # 事業概要の要約
            "industries": list[dict], # 推定業種
            "company_size": dict,     # 企業規模の推定（size/is_listed/signals）
            "prefecture": str | None, # 推定所在地（都道府県）。取れなければNone
            "pages_crawled": int,
            "error": str or None
        }
    """
    try:
        # メインページを取得
        html = fetch_page(url)
        all_text = extract_text_from_html(html)
        pages_crawled = 1

        # トップページから事業概要を抽出
        summary = extract_summary(html)

        # サブページもクロール（並列取得で高速化）
        if crawl_subpages:
            internal_links = get_internal_links(html, url)

            def _fetch_sub(link: str) -> str | None:
                # 遅いページを待ちすぎないようタイムアウトは短めに設定
                try:
                    return extract_text_from_html(fetch_page(link, timeout=6))
                except Exception:
                    return None  # 個別ページの取得失敗は無視

            if internal_links:
                # ネットワーク待ちが主なのでスレッドで同時取得する
                with ThreadPoolExecutor(max_workers=len(internal_links)) as executor:
                    for sub_text in executor.map(_fetch_sub, internal_links):
                        if sub_text:
                            all_text += f"\n{sub_text}"
                            pages_crawled += 1

        # キーワード抽出
        keywords = extract_keywords(all_text)

        # 業種推定
        industries = estimate_industries(all_text, keywords)

        # 企業規模の推定（業種別の中小企業基準で判定）
        company_size = estimate_company_size(all_text, industries)

        # 所在地（都道府県）を推定。取れなければ None（地域フィルタ非適用）
        prefecture = extract_prefecture(all_text)

        return {
            "url": url,
            "keywords": keywords,
            "summary": summary,
            "industries": industries,
            "company_size": company_size,
            "prefecture": prefecture,
            "pages_crawled": pages_crawled,
            "error": None,
        }

    except requests.exceptions.RequestException as e:
        return {
            "url": url,
            "keywords": [],
            "summary": "",
            "industries": [],
            "company_size": {"size": "unknown", "is_listed": False, "signals": []},
            "prefecture": None,
            "pages_crawled": 0,
            "error": f"ページの取得に失敗しました: {str(e)}",
        }
    except Exception as e:
        return {
            "url": url,
            "keywords": [],
            "summary": "",
            "industries": [],
            "company_size": {"size": "unknown", "is_listed": False, "signals": []},
            "prefecture": None,
            "pages_crawled": 0,
            "error": f"エラーが発生しました: {str(e)}",
        }


def extract_subsidy_from_url(url: str) -> dict:
    """
    補助金の公募ページURLから、登録用の情報を自動抽出する。

    タイトル（ページタイトル/h1）、概要（extract_summary）、
    詳細テキスト（本文＝RAG検索対象）を取得する。

    Args:
        url: 補助金の公募ページURL

    Returns:
        {
            "title": str, "description": str, "detail_text": str,
            "official_url": str, "error": str | None
        }
    """
    try:
        html = fetch_page(url)
    except Exception as e:  # noqa: BLE001
        return {
            "title": "", "description": "", "detail_text": "",
            "official_url": url,
            "error": f"ページの取得に失敗しました: {e}",
        }

    soup = BeautifulSoup(html, "html.parser")

    # タイトル: <title> → 無ければ最初の h1
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    # サイト名の付随部分（区切り文字以降）を落として簡潔に
    title = re.split(r"[|｜\-–—:：]", title)[0].strip() if title else "取得した補助金"

    description = extract_summary(html)
    # 本文テキスト（RAG検索対象）。長すぎる場合は先頭を採用
    detail_text = extract_text_from_html(html)
    if len(detail_text) > 2000:
        detail_text = detail_text[:2000]

    return {
        "title": title,
        "description": description,
        "detail_text": detail_text,
        "official_url": url,
        "error": None,
    }


# ミラサポplus（経産省・中小企業庁の公式サイト）の補助金一覧ページ
MIRASAPO_SUBSIDY_LIST = "https://mirasapo-plus.go.jp/subsidy/"
# 補助金個別ページでない（ガイド・説明）ページのパス断片
_MIRASAPO_EXCLUDE = ["/subsidy/guide", "/subsidy/"]


def _collect_mirasapo_links(listing_html: str) -> list[str]:
    """ミラサポplusの一覧HTMLから個別補助金ページのURLを収集する。"""
    soup = BeautifulSoup(listing_html, "html.parser")
    urls = []
    seen = set()
    for a in soup.select("a[href]"):
        href = urljoin(MIRASAPO_SUBSIDY_LIST, a.get("href", ""))
        parsed = urlparse(href)
        if parsed.netloc != "mirasapo-plus.go.jp":
            continue
        path = parsed.path.rstrip("/")
        # /subsidy/ 配下の個別ページのみ（一覧トップ・ガイドは除外）
        if not path.startswith("/subsidy/"):
            continue
        if path in ("/subsidy", "/subsidy/guide"):
            continue
        key = path  # 末尾スラッシュ有無を正規化して重複排除
        if key in seen:
            continue
        seen.add(key)
        urls.append(f"https://mirasapo-plus.go.jp{path}/")
    return urls


def extract_mirasapo_subsidies(max_items: int = 15) -> dict:
    """
    ミラサポplusの補助金一覧から、個別補助金の情報を一括抽出する。

    Args:
        max_items: 取得する最大件数

    Returns:
        {
            "subsidies": list[dict],  # 補助金レコード（source="mirasapo"）
            "error": str | None,
        }
    """
    try:
        listing_html = fetch_page(MIRASAPO_SUBSIDY_LIST)
    except Exception as e:  # noqa: BLE001
        return {"subsidies": [], "error": f"一覧の取得に失敗しました: {e}"}

    links = _collect_mirasapo_links(listing_html)[:max_items]
    subsidies = []
    for url in links:
        info = extract_subsidy_from_url(url)
        if info.get("error") or not info.get("title"):
            continue
        title = info["title"]
        # 「補助金とは」等の総説ページはスキップ
        if title.strip() in ("補助金", "補助金とは"):
            continue
        # 公募終了・受付終了の補助金はスキップ（古い情報の混入を防ぐ）
        check_text = (info.get("description", "") + " "
                      + info.get("detail_text", ""))
        closed_words = [
            "公募を終了", "公募は終了", "公募終了", "受付を終了", "受付は終了",
            "受付終了", "募集を終了", "募集は終了", "募集終了", "終了しました",
            "受付は終了しました", "申請受付は終了",
        ]
        if any(w in check_text for w in closed_words):
            continue
        subsidies.append({
            "title": title,
            "description": info.get("description", ""),
            "detail_text": info.get("detail_text", ""),
            "official_url": info.get("official_url", url),
            "eligible_scale": "sme",
            "source": "mirasapo",
        })

    return {"subsidies": subsidies, "error": None}
