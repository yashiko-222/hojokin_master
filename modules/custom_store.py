"""
手動登録カスタム補助金のストア（JSONによる永続化・CRUD）。

Jグランツに載っていない自治体助成金や最新公募情報を手動で追加・保存する。
保存先: data/custom_subsidies.json
各レコードには source="manual", is_custom=True を付与して区別する。
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path


# プロジェクトルート直下の data/custom_subsidies.json
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_JSON_PATH = _DATA_DIR / "custom_subsidies.json"

# 初期サンプル（初回のみ生成）
_SAMPLE = [
    {
        "id": "custom-sample-dx",
        "source": "manual",
        "is_custom": True,
        "title": "【サンプル】○○市 中小企業DX・業務効率化設備導入助成金",
        "organization": "○○市 産業振興課",
        "subsidy_max_limit": "最大300万円",
        "subsidy_rate": "1/2",
        "eligible_scale": "sme",
        "target_industries": ["製造業", "小売業", "サービス業"],
        "target_expenses": "システム導入費、クラウド利用料、専門家経費、設備費",
        "description": "自社のDX推進・業務効率化のための設備・システム導入を支援する市の独自助成金",
        "detail_text": (
            "本助成金は市内に事業所を有する中小企業を対象に、業務のデジタル化・"
            "自動化に資するシステムや設備の導入経費の一部を助成します。"
            "対象経費はソフトウェア導入費、クラウドサービス利用料、"
            "IT専門家によるコンサルティング費用等。従業員の生産性向上、"
            "受発注業務のデジタル化、在庫管理システムの導入などが対象となります。"
        ),
        "official_url": "https://example.city.lg.jp/dx-subsidy",
    },
    {
        "id": "custom-sample-jinzai",
        "source": "manual",
        "is_custom": True,
        "title": "【サンプル】○○県 ものづくり人材育成研修助成金",
        "organization": "○○県 労働政策課",
        "subsidy_max_limit": "最大100万円",
        "subsidy_rate": "2/3",
        "eligible_scale": "sme",
        "target_industries": ["製造業", "建設業"],
        "target_expenses": "研修受講料、講師謝金、教材費",
        "description": "製造・建設分野の技能人材を育成するための社内外研修を支援する県の助成金",
        "detail_text": (
            "県内の製造業・建設業の中小企業が、社員のスキルアップや技能承継の"
            "ために実施する研修・講習の費用を助成します。溶接・加工技術の習得、"
            "施工管理の資格取得研修、若手技術者の育成プログラム等が対象です。"
        ),
        "official_url": "https://example.pref.lg.jp/jinzai-subsidy",
    },
]


def _ensure_file() -> None:
    """データファイルが無ければサンプル付きで作成する。"""
    if not _JSON_PATH.exists():
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _JSON_PATH.write_text(
            json.dumps(_SAMPLE, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_custom_subsidies() -> list[dict]:
    """保存済みの手動登録補助金をすべて読み込む。"""
    _ensure_file()
    try:
        data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = [data]
        # 念のためフラグを補完
        for rec in data:
            rec.setdefault("source", "manual")
            rec.setdefault("is_custom", True)
        return data
    except (json.JSONDecodeError, OSError):
        return []


def _save_all(records: list[dict]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _JSON_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _normalize(record: dict) -> dict:
    """入力レコードを内部形式に正規化し、必須フラグ・IDを補完する。"""
    rec = dict(record)
    # データ提供元を尊重（manual=手動 / mirasapo=ミラサポplus等）。既定はmanual
    rec["source"] = record.get("source", "manual")
    rec["is_custom"] = True
    if not rec.get("id"):
        rec["id"] = f"custom-{uuid.uuid4().hex[:8]}"

    # target_industries は文字列（カンマ/読点区切り）でも配列でも受け付ける
    ti = rec.get("target_industries", [])
    if isinstance(ti, str):
        rec["target_industries"] = [
            s.strip() for s in re.split(r"[,、\s/]+", ti) if s.strip()
        ]

    # 表示用の既定値
    rec.setdefault("organization", "情報なし")
    rec.setdefault("subsidy_max_limit", "情報なし")
    rec.setdefault("subsidy_rate", "情報なし")
    rec.setdefault("eligible_scale", "sme")
    rec.setdefault("target_expenses", "")
    rec.setdefault("description", "")
    rec.setdefault("detail_text", "")
    rec.setdefault("official_url", "")
    # RAGのため target（対象）に対象経費・業種をまとめておく
    rec.setdefault(
        "target",
        f"対象業種: {'、'.join(rec.get('target_industries', []))} / "
        f"対象経費: {rec.get('target_expenses', '')}",
    )
    return rec


def _normalize_url(url: str) -> str:
    """URL比較用に正規化する（小文字化・末尾スラッシュ除去）。"""
    u = (url or "").strip().lower()
    return u.rstrip("/")


def find_by_url(url: str) -> dict | None:
    """
    指定URLと同じ official_url を持つ登録済みレコードを返す。
    無ければ None。
    """
    target = _normalize_url(url)
    if not target:
        return None
    for rec in load_custom_subsidies():
        if _normalize_url(rec.get("official_url", "")) == target:
            return rec
    return None


def add_custom_subsidy(record: dict) -> dict:
    """
    カスタム補助金を1件追加する。

    Returns:
        保存された（正規化済みの）レコード
    """
    records = load_custom_subsidies()
    normalized = _normalize(record)
    # 同一IDがあれば置き換え、なければ追加
    replaced = False
    for i, r in enumerate(records):
        if r.get("id") == normalized["id"]:
            records[i] = normalized
            replaced = True
            break
    if not replaced:
        records.append(normalized)
    _save_all(records)
    return normalized


def import_from_json(payload) -> int:
    """
    JSON（配列または単一オブジェクト）を一括インポートする。

    Returns:
        追加した件数
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("インポートするデータは配列またはオブジェクトである必要があります。")

    count = 0
    for rec in payload:
        if isinstance(rec, dict) and (rec.get("title")):
            add_custom_subsidy(rec)
            count += 1
    return count


def update_custom_subsidy(subsidy_id: str, updates: dict) -> dict | None:
    """
    既存カスタム補助金の一部フィールドを更新する（手動補足用）。

    Args:
        subsidy_id: 対象ID
        updates: 更新するフィールド（title/subsidy_max_limit/subsidy_rate/
                 eligible_scale/target_industries/target_expenses/description等）

    Returns:
        更新後のレコード。対象が無ければ None。
    """
    records = load_custom_subsidies()
    for i, rec in enumerate(records):
        if rec.get("id") == subsidy_id:
            # target_industries は文字列でも配列でも受け付ける
            if "target_industries" in updates and isinstance(
                updates["target_industries"], str
            ):
                updates["target_industries"] = [
                    s.strip() for s in re.split(r"[,、\s/]+", updates["target_industries"])
                    if s.strip()
                ]
            # None・空文字は無視（既存値を保持）
            for k, v in updates.items():
                if v is None:
                    continue
                if isinstance(v, str) and v == "":
                    continue
                rec[k] = v
            records[i] = rec
            _save_all(records)
            return rec
    return None


def delete_custom_subsidy(subsidy_id: str) -> bool:
    """IDを指定してカスタム補助金を削除する。"""
    records = load_custom_subsidies()
    new_records = [r for r in records if r.get("id") != subsidy_id]
    if len(new_records) != len(records):
        _save_all(new_records)
        return True
    return False
