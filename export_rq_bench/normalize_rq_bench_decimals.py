# -*- coding: utf-8 -*-
"""
rq_bench 历史数据小数规范化（独立脚本，不依赖仓库其它模块）。

读取 MongoDB basic_rq.rq_bench，按规范四舍五入后写回：
  - open/high/low/close/pre_close/volume/amt → 2 位小数
  - pct_chg（涨跌幅比例）→ 4 位小数

依赖: pip install pandas pymongo

用法::

    python normalize_rq_bench_decimals.py --start 2022-06-20 --end 2022-06-30
    python normalize_rq_bench_decimals.py --start 2026-05-30 --end 2026-06-02 --dry-run

也可改下方 START_DATE / END_DATE 后直接::

    python normalize_rq_bench_decimals.py
"""
from __future__ import annotations

import argparse
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pandas as pd
import pymongo

# ---------------------------------------------------------------------------
# 配置（账号同 mongo_connect.py wonderwz27018_rw）
# ---------------------------------------------------------------------------
# mongodb_url = "mongodb://readwriter:readwrite_wonderwz@192.168.110.199:27017"
# mongodb_url = "mongodb://127.0.0.1:27017/"
mongodb_url = "mongodb://admin:admin_wonderwz@192.168.110.199:27017/"
START_DATE = "2020-01-02"
END_DATE = "2026-06-02"
MONGO_DB = "basic_rq"
COLLECTION = "rq_bench"
DATE_FMT = "%Y-%m-%d"
# ---------------------------------------------------------------------------

BENCH_PRICE_VOL_COLS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amt",
)
PCT_CHG_COL = "pct_chg"
FORMAT_COLS = (*BENCH_PRICE_VOL_COLS, PCT_CHG_COL)


def norm_date(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime(DATE_FMT)


def parse_date_arg(arg: str) -> str:
    raw = str(arg).strip()
    if len(raw) == 8 and raw.isdigit():
        ts = pd.Timestamp(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    else:
        ts = pd.Timestamp(raw.replace("/", "-"))
    return ts.strftime(DATE_FMT)


def parse_start_end_range(start: str, end: str) -> tuple[str, str]:
    start_s = parse_date_arg(start)
    end_s = parse_date_arg(end)
    if start_s > end_s:
        raise ValueError(f"起始日 {start_s} 不能晚于结束日 {end_s}")
    return start_s, end_s


def _round_half_up(value, places: int) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    quant = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def format_bench_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "date" in out:
        out["date"] = norm_date(out["date"])
    for col in BENCH_PRICE_VOL_COLS:
        if col in out:
            out[col] = _round_half_up(out[col], 2)
    if PCT_CHG_COL in out:
        out[PCT_CHG_COL] = _round_half_up(out[PCT_CHG_COL], 4)
    return out


def _values_differ(old: Any, new: Any) -> bool:
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    try:
        if pd.isna(old) and pd.isna(new):
            return False
    except (TypeError, ValueError):
        pass
    try:
        return float(old) != float(new)
    except (TypeError, ValueError):
        return old != new


def _build_update_fields(doc: dict[str, Any], formatted: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if _values_differ(doc.get("date"), formatted.get("date")):
        updates["date"] = formatted["date"]
    for col in FORMAT_COLS:
        if col not in formatted:
            continue
        if _values_differ(doc.get(col), formatted[col]):
            updates[col] = formatted[col]
    return updates


def fetch_docs_in_range(coll: Any, start: str, end: str) -> list[dict[str, Any]]:
    """
    直接从 rq_bench 读取并在内存中按 date 过滤（不依赖 economic.trade_dates）。

    兼容 date 为 YYYY-MM-DD 或 YYYY/MM/DD。
    """
    start_s, end_s = parse_start_end_range(start, end)
    docs: list[dict[str, Any]] = []
    for doc in coll.find({}):
        raw_date = doc.get("date")
        if raw_date is None:
            continue
        try:
            day = norm_date(str(raw_date))
        except (ValueError, TypeError):
            continue
        if start_s <= day <= end_s:
            docs.append(doc)
    return docs


def normalize_rq_bench_history(
    start: str,
    end: str,
    *,
    mongo_url: str = mongodb_url,
    mongo_db: str = MONGO_DB,
    collection: str = COLLECTION,
    dry_run: bool = False,
) -> dict[str, int]:
    start_s, end_s = parse_start_end_range(start, end)
    client = pymongo.MongoClient(mongo_url)
    coll = client[mongo_db][collection]

    docs = fetch_docs_in_range(coll, start_s, end_s)
    stats = {
        "matched": len(docs),
        "updated": 0,
        "unchanged": 0,
        "skipped_empty": 0,
    }
    if not docs:
        print(
            f"区间 {start_s} ~ {end_s}：未找到 rq_bench 文档\n"
            f"请确认 mongodb_url 指向含数据的实例（当前: "
            f"{mongo_url.split('@')[-1] if '@' in mongo_url else mongo_url}）"
        )
        return stats

    url_hint = mongo_url.split("@")[-1] if "@" in mongo_url else mongo_url
    print(
        f"Mongo: {mongo_db}.{collection}\n"
        f"URL: {url_hint}\n"
        f"区间: {start_s} ~ {end_s}\n"
        f"匹配: {len(docs)} 条"
        + (" [dry-run，不写库]" if dry_run else "")
    )

    for doc in docs:
        raw = {k: v for k, v in doc.items() if k != "_id"}
        if not raw:
            stats["skipped_empty"] += 1
            continue

        formatted = format_bench_row(raw)
        updates = _build_update_fields(doc, formatted)
        if not updates:
            stats["unchanged"] += 1
            continue

        stats["updated"] += 1
        label = f"{formatted.get('date')} {formatted.get('code', '')}"
        if dry_run:
            print(f"  [dry-run] {label}: {updates}")
            continue

        coll.update_one({"_id": doc["_id"]}, {"$set": updates})
        print(f"  [updated] {label}: {list(updates.keys())}")

    print(
        f"\n完成：匹配 {stats['matched']} | "
        f"更新 {stats['updated']} | 无变化 {stats['unchanged']} | "
        f"跳过 {stats['skipped_empty']}"
    )
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="rq_bench 历史数据小数规范化（独立脚本）")
    p.add_argument("--start", default=None, help=f"区间起（含），默认 {START_DATE}")
    p.add_argument("--end", default=None, help=f"区间止（含），默认 {END_DATE}")
    p.add_argument("--mongodb-url", default=mongodb_url, help="Mongo 连接串")
    p.add_argument("--mongo-db", default=MONGO_DB)
    p.add_argument("--collection", default=COLLECTION)
    p.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    args = p.parse_args()

    start = args.start or START_DATE
    end = args.end or END_DATE
    parse_date_arg(start)
    parse_date_arg(end)

    normalize_rq_bench_history(
        start,
        end,
        mongo_url=args.mongodb_url,
        mongo_db=args.mongo_db,
        collection=args.collection,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
