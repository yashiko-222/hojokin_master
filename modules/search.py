"""
Web検索モジュール
企業名や自然言語のクエリから、該当企業の公式HPのURLを特定する。
Bing検索（APIキー不要）を利用する。
"""

import base64
import binascii
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup


BING_SEARCH_URL = "https://www.bing.com/search"
WIKIPEDIA_API = "https://ja.wikipedia.org/w/api.php"

# 公式サイトとして採用しないドメイン（取引所・政府DB等）
_WIKI_LINK_BLOCKLIST = [
    "jpx.co.jp", "sec.gov", "edinet", "wikidata.org", "wikimedia",
    "twitter.com", "x.com", "facebook.com", "google.com",
]

# 公式HP判定で減点したいドメイン（ポータル・SNS・求人等）
NON_OFFICIAL_DOMAINS = [
    "wikipedia.org", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "linkedin.com", "youtube.com", "note.com",
    "indeed.com", "rikunabi.com", "mynavi.jp", "en-japan.com",
    "baseconnect.in", "houjin.jp", "alarmbox.jp", "meti.go.jp",
    "nikkei.com", "prtimes.jp", "google.com", "yahoo.co.jp",
    "amazon.co.jp", "rakuten.co.jp", "tabelog.com", "ekiten.jp",
    "job-medley.com", "hellowork", "townwork.net",
    "tokubai.co.jp", "kakaku.com", "mercari.com", "yahoo.com",
    "matome", "naver.jp", "ameblo.jp", "hatenablog.com",
    "oempartsonline.com", "americantoyota.com", "advanceautoparts.com",
    "carid.com",
    # 企業情報データベース・まとめ系（第三者サイト）
    "fact-board.co.jp", "salesnow.jp", "cardboard.jp", "buffett-code.com",
    "strainer.jp", "ullet.com", "eol.co.jp", "gaiax-socialmedialab.jp",
    "en-hyouban.com", "openwork.jp", "vorkers.com", "kaisha-search",
    "kaishalog.com", "companypicks.jp", "jobtalk.jp",
    # 株価・投資情報・企業データベース系
    "minkabu.jp", "kabutan.jp", "nikkei.com", "traders.co.jp",
    "manabushokan.com", "companydata", "tsujigawa.com", "ullet",
    "buffett-code", "shikiho.jp", "irbank.net", "stocks.finance",
]


def is_url(text: str) -> bool:
    """入力文字列がURLかどうかを判定する。"""
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return True
    # www. で始まる、または ドメイン形式（example.co.jp 等）
    domain_pattern = re.compile(
        r"^(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(/.*)?$"
    )
    return bool(domain_pattern.match(text))


def normalize_url(text: str) -> str:
    """URL文字列にスキームを補完する。"""
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return text
    return f"https://{text}"


def search_wikipedia_official(company_query: str) -> list[dict]:
    """
    Wikipediaの企業記事から公式サイトのURLを取得する（Bingのフォールバック）。

    Args:
        company_query: 企業名

    Returns:
        [{"title": str, "url": str, "snippet": str}, ...]（0〜1件）
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SubsidyMatcher/1.0)"}

    try:
        # 1. 記事タイトルを検索
        search_resp = requests.get(
            WIKIPEDIA_API,
            params={
                "action": "query", "list": "search",
                "srsearch": company_query, "format": "json", "srlimit": 3,
            },
            headers=headers,
            timeout=15,
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("query", {}).get("search", [])
        if not hits:
            return []

        # 企業名との一致度が高い記事を選ぶ
        core = _core_company_name(company_query)
        title = hits[0]["title"]
        for h in hits:
            if core and core in h["title"]:
                title = h["title"]
                break

        # 2. 記事HTMLのinfoboxから公式サイトを抽出
        page = requests.get(
            f"https://ja.wikipedia.org/wiki/{urllib.parse.quote(title)}",
            headers=headers,
            timeout=15,
        )
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")

        infobox = soup.select_one("table.infobox")
        if not infobox:
            return []

        official_url = None

        # 「公式サイト」「外部リンク」行を優先的に探す
        for row in infobox.select("tr"):
            header = row.select_one("th")
            if header and ("公式" in header.get_text() or "ウェブサイト" in header.get_text()):
                link = row.select_one("a.external")
                if link and link.get("href", "").startswith("http"):
                    official_url = link["href"]
                    break

        # 見つからなければ、ブロックリスト外の最初のexternalリンク
        if not official_url:
            for a in infobox.select("a.external"):
                href = a.get("href", "")
                if href.startswith("http") and not any(
                    b in href for b in _WIKI_LINK_BLOCKLIST
                ):
                    official_url = href
                    break

        if not official_url:
            return []

        return [{
            "title": f"{title}（Wikipedia記載の公式サイト）",
            "url": official_url,
            "snippet": "Wikipediaの企業記事から取得した公式サイトです。",
        }]

    except (requests.exceptions.RequestException, ValueError, KeyError):
        return []


def _score_official_url(
    url: str, company_query: str, title: str = "", snippet: str = ""
) -> float:
    """
    検索結果が対象企業の公式HPらしいかスコアリングする。
    高いほど公式HPの可能性が高い。

    最重要: タイトル/スニペットに企業名が含まれるか（別企業の除外）。
    """
    score = 0.0
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    # ポータル・SNS・求人・企業DB等は強く減点（厳格化）
    for bad in NON_OFFICIAL_DOMAINS:
        if bad in domain:
            score -= 8.0
            break

    # === 企業名マッチ（最重要）===
    # クエリから会社形態語・一般語を除いた核となる企業名を抽出
    core_name = _core_company_name(company_query)
    core_ns = core_name.replace(" ", "")
    title_lower = title.lower()
    snippet_lower = snippet.lower()

    # タイトルに企業名（コア）が含まれれば大きく加点
    if core_name and (core_name.lower() in title_lower
                      or core_ns.lower() in title_lower):
        score += 4.0
    elif core_name and (core_name.lower() in snippet_lower
                        or core_ns.lower() in snippet_lower):
        score += 1.5
    else:
        # タイトルにもスニペットにも企業名が無い → 別企業の可能性大、減点
        score -= 3.5

    # === 企業名とドメイン（SLD）の一致 ===
    labels_all = domain.split(".")
    sld_name = labels_all[-3] if domain.endswith(
        (".co.jp", ".or.jp", ".ne.jp")) and len(labels_all) >= 3 \
        else (labels_all[-2] if len(labels_all) >= 2 else "")
    # 英数字コア（英語社名やローマ字表記）とSLDの部分一致
    query_ascii = re.sub(r"[^a-zA-Z0-9]", "", (core_ns or company_query).lower())
    if query_ascii and len(query_ascii) >= 3:
        if query_ascii == sld_name:
            score += 3.0          # 完全一致は本体の可能性が非常に高い
        elif query_ascii[:5] in sld_name or sld_name in query_ascii:
            score += 2.0

    # 「公式」「コーポレート」等がタイトルにあれば加点
    if any(w in title for w in ["公式", "オフィシャル", "コーポレート", "会社概要"]):
        score += 1.2

    # 会社概要・コーポレートページのURLパスを加点
    if any(seg in path for seg in
           ["/company", "/corporate", "/about", "/profile", "/kaisha", "/gaiyo"]):
        score += 1.0

    # 日本企業の公式ドメインでよく使われるTLDを加点（比重引き上げ）
    if domain.endswith(".co.jp"):
        score += 2.0
    elif domain.endswith(".jp"):
        score += 1.0
    elif domain.endswith(".com"):
        score += 0.4

    # サブドメインが多い（子会社サイト・事業部サイト等）は減点し本体を優先
    # 例: www.nintendo.co.jp は許容、store.healthcare.omron.co.jp は減点
    labels = domain.split(".")
    sub_count = max(len(labels) - 3, 0) if domain.endswith((".co.jp", ".or.jp", ".ne.jp")) \
        else max(len(labels) - 2, 0)
    if labels and labels[0] == "www":
        sub_count = max(sub_count - 1, 0)
    score -= sub_count * 1.0

    # 第2レベルドメイン（企業名部分）が短いほど本体の可能性が高い
    # 例: sony < sonymusic, nintendo < nintendo-sales
    # ハイフン付き接尾辞（-sales, -music 等）は子会社とみなし減点
    sld = labels[-3] if domain.endswith((".co.jp", ".or.jp", ".ne.jp")) and len(labels) >= 3 \
        else (labels[-2] if len(labels) >= 2 else "")
    if "-" in sld:
        score -= 1.0

    # トップページ（パスが浅い）ほど公式HP本体の可能性が高い
    path = urllib.parse.urlparse(url).path.strip("/")
    depth = len([p for p in path.split("/") if p])
    score -= depth * 0.4
    if depth == 0:
        score += 1.0

    return score


def _core_company_name(company_query: str) -> str:
    """
    企業名クエリから、会社形態語・一般的な検索語・記号を除いた核となる名称を取り出す。
    法人格が元から付いていない略称入力でも、正しくコアを切り出す。
    例: 「株式会社サイボウズ 公式」 -> 「サイボウズ」 / 「トヨタ」 -> 「トヨタ」
    """
    text = company_query.strip()

    # 記号・括弧類を空白へ
    text = re.sub(r"[『』「」（）()\[\]【】,、。・／/]", " ", text)

    # 会社形態語（先頭・末尾・中間いずれの位置でも除去）
    corp_words = [
        "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
        "一般社団法人", "公益社団法人", "一般財団法人", "公益財団法人",
        "特定非営利活動法人", "医療法人", "学校法人", "社会福祉法人",
        "独立行政法人", "地方独立行政法人",
    ]
    for w in corp_words:
        text = text.replace(w, " ")

    # 検索補助語を除去
    aux_words = [
        "公式サイト", "公式ホームページ", "公式", "ホームページ",
        "オフィシャルサイト", "オフィシャル", "コーポレートサイト",
        "会社概要", "企業情報", "サイト", "の", "HP", "hp",
    ]
    for w in aux_words:
        text = text.replace(w, " ")

    # 連続空白を1つに詰め、前後空白を除去
    core = re.sub(r"\s+", " ", text).strip()
    # 空白が残る場合（例: 「トヨタ 自動車」）は結合した形も後段で使えるよう素直に返す
    return core


def search_web(query: str, max_results: int = 10) -> list[dict]:
    """
    Bingでウェブ検索を行い、結果を返す。

    Args:
        query: 検索クエリ（企業名や自然言語）
        max_results: 取得する最大件数

    Returns:
        [{"title": str, "url": str, "snippet": str}, ...]
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9",
    }

    try:
        response = requests.get(
            BING_SEARCH_URL,
            params={"q": query, "setlang": "ja", "mkt": "ja-JP"},
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    seen_domains = set()

    for li in soup.select("li.b_algo"):
        # 広告・スポンサーリンクを除外（Canva等の検索連動広告対策）
        if _is_ad_element(li):
            continue

        link_el = li.select_one("h2 a")
        if not link_el or not link_el.get("href"):
            continue

        title = link_el.get_text(strip=True)
        raw_href = link_el.get("href", "")
        real_url = _extract_real_url(raw_href)
        if not real_url:
            continue

        # 登録ドメイン単位（www.除去）で重複排除
        reg_domain = _registrable_domain(real_url)
        if reg_domain in seen_domains:
            continue
        seen_domains.add(reg_domain)

        # スニペット取得
        snippet_el = li.select_one(".b_caption p") or li.select_one("p")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

        results.append({
            "title": title,
            "url": real_url,
            "snippet": snippet,
        })

        if len(results) >= max_results:
            break

    return results


def _is_ad_element(li) -> bool:
    """検索結果要素が広告・スポンサーリンクかどうか判定する。"""
    # 広告ラベル要素の存在をチェック
    if li.select_one(".b_adSlug, .b_ad, [aria-label*='広告'], [aria-label*='Ad']"):
        return True
    classes = " ".join(li.get("class", []))
    if "b_ad" in classes or "ad_" in classes:
        return True
    return False


def _registrable_domain(url: str) -> str:
    """
    URLから登録ドメイン（www.やサブドメインを正規化した単位）を取り出す。
    例: www.canva.com -> canva.com, kintone.cybozu.co.jp -> cybozu.co.jp
    """
    domain = urllib.parse.urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    labels = domain.split(".")
    # co.jp/or.jp/ne.jp/go.jp 等の2段TLDは末尾3ラベルを登録ドメインとする
    if domain.endswith((".co.jp", ".or.jp", ".ne.jp", ".go.jp", ".ac.jp",
                        ".com.cn", ".co.uk")):
        return ".".join(labels[-3:]) if len(labels) >= 3 else domain
    # 通常TLDは末尾2ラベル
    return ".".join(labels[-2:]) if len(labels) >= 2 else domain


def _extract_real_url(href: str) -> str:
    """
    BingのリダイレクトURL（bing.com/ck/a?...&u=a1<base64>）から
    実際のURLを取り出す。通常URLはそのまま返す。
    """
    if not href:
        return ""

    parsed = urllib.parse.urlparse(href)

    # Bingのリダイレクト形式
    if "bing.com" in parsed.netloc and "/ck/" in parsed.path:
        query_params = urllib.parse.parse_qs(parsed.query)
        u_param = query_params.get("u", [""])[0]
        # 先頭の "a1" を除いてbase64デコード（Bingのフォーマット）
        if u_param.startswith("a1"):
            b64 = u_param[2:]
            # URLセーフbase64、パディング補完
            b64 += "=" * (-len(b64) % 4)
            try:
                decoded = base64.urlsafe_b64decode(b64).decode("utf-8")
                if decoded.startswith(("http://", "https://")):
                    return decoded
            except (binascii.Error, UnicodeDecodeError, ValueError):
                return ""
        return ""

    # 通常のURL
    if href.startswith(("http://", "https://")):
        return href
    return ""


def find_company_url(company_query: str) -> dict:
    """
    企業名や自然言語クエリから、最も公式HPらしいURLを特定する。

    Args:
        company_query: 企業名や検索クエリ

    Returns:
        {
            "url": str or None,        # 特定された公式HPのURL
            "candidates": list[dict],  # 全候補（スコア順）
            "error": str or None,
        }
    """
    # 候補として表示する最低スコア（これ未満は企業名と無関係とみなし除外）
    MIN_CANDIDATE_SCORE = 1.5
    # 自動確定とみなす高スコア
    CONFIRM_SCORE = 3.0

    # 複数の検索クエリを順に試す（Bingが無関係な結果を返す場合の対策）。
    # 法人格を内部補完し、公式コーポレートサイトがヒットしやすいクエリを作る。
    core = _core_company_name(company_query)
    core_nospace = core.replace(" ", "") if core else ""
    base = core if core else company_query
    base_ns = core_nospace if core_nospace else company_query

    raw_variants = [
        f"{company_query} 公式サイト",
        f"株式会社{base_ns} 会社概要",
        f"{base} コーポレートサイト",
        f"{base} 公式 企業サイト",
        f"{company_query}",
    ]
    # 重複を除いて順序を保持
    seen_q = set()
    query_variants = []
    for q in raw_variants:
        q = q.strip()
        if q and q not in seen_q:
            seen_q.add(q)
            query_variants.append(q)

    best_scored: list[dict] = []
    for search_query in query_variants:
        results = search_web(search_query, max_results=10)
        if not results:
            continue

        scored = []
        for r in results:
            score = _score_official_url(
                r["url"], company_query, r.get("title", ""), r.get("snippet", "")
            )
            scored.append({**r, "official_score": round(score, 2)})
        scored.sort(key=lambda x: x["official_score"], reverse=True)

        # 企業名がしっかりマッチした候補が得られたら確定
        if scored and scored[0]["official_score"] >= CONFIRM_SCORE:
            filtered = [c for c in scored
                        if c["official_score"] >= MIN_CANDIDATE_SCORE]
            return {
                "url": scored[0]["url"],
                "candidates": filtered[:5],
                "error": None,
            }

        if scored and (not best_scored or
                       scored[0]["official_score"] > best_scored[0]["official_score"]):
            best_scored = scored

    # Bingで良い結果が出なかった場合、Wikipediaから公式サイトを取得
    wiki_results = search_wikipedia_official(company_query)
    if wiki_results:
        wiki = wiki_results[0]
        wiki_scored = {
            **wiki,
            "official_score": round(
                _score_official_url(
                    wiki["url"], company_query, wiki["title"], wiki["snippet"]
                ) + 3.0,  # Wikipedia由来は信頼度ボーナス
                2,
            ),
        }
        # 企業名にマッチしたBing候補のみ統合
        good_bing = [c for c in (best_scored or [])
                     if c["official_score"] >= MIN_CANDIDATE_SCORE]
        merged = [wiki_scored] + good_bing
        return {
            "url": wiki_scored["url"],
            "candidates": merged[:5],
            "error": None,
        }

    # 企業名にマッチした候補のみを残す（Canva等の無関係サイトを除外）
    good_candidates = [c for c in best_scored
                       if c["official_score"] >= MIN_CANDIDATE_SCORE]

    if not good_candidates:
        return {
            "url": None,
            "candidates": [],
            "error": (
                "入力された企業の公式HPを特定できませんでした。"
                "企業名を正式名称（例: 「〇〇株式会社」）で入力するか、"
                "URLを直接入力してください。"
            ),
        }

    return {
        "url": good_candidates[0]["url"],
        "candidates": good_candidates[:5],
        "error": None,
    }
