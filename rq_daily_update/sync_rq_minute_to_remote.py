# -*- coding: utf-8 -*-
"""
将 rq_minute.rq_minute_none_YYYY 按交易日增量同步到远端 MongoDB（默认 T-1）。

源库：wonderwz27018_rw @ 192.168.110.199:27018
目标：wonderwz203_19_rw @ 114.80.62.203:27019

集合按交易日年份分表，如 2026-06-03 → rq_minute_none_2026。

用法（basic_rq_daily 根目录）：
  python rq_daily_update/sync_rq_minute_to_remote.py
  python rq_daily_update/sync_rq_minute_to_remote.py --date 2026-06-03
  python rq_daily_update/sync_rq_minute_to_remote.py --start 2026-01-05 --end 2026-03-31
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__, daily=True)

from usedbdef import get_client

MINUTE_DB = "rq_minute"
MINUTE_COLLECTION_PREFIX = "rq_minute_none_"
DEFAULT_SOURCE_ALIAS = "wonderwz27018_rw"
DEFAULT_TARGET_ALIAS = "wonderwz203_19_rw"
INSERT_BATCH = 5000
PROGRESS_EVERY = 50_000


def _log(msg: str) -> None:
    print(msg, flush=True)


def date_variants(d: str) -> list[str]:
    d = str(d).strip()
    return list({d, d.replace("-", "/"), d.replace("/", "-")})


def minute_collection_for_date(trade_date: str) -> str:
    """按交易日年份返回集合名，如 ``rq_minute_none_2026``。"""
    normalized = str(trade_date).strip().replace("/", "-")[:10]
    year = normalized[:4]
    if not year.isdigit():
        raise ValueError(f"无法从交易日解析年份: {trade_date!r}")
    return f"{MINUTE_COLLECTION_PREFIX}{year}"


def sync_minute_for_date(
    *,
    src_col: Any,
    dst_col: Any,
    trade_date: str,
    batch_size: int = INSERT_BATCH,
) -> dict[str, int]:
    filt = {"date": {"$in": date_variants(trade_date)}}
    src_count = src_col.count_documents(filt)
    deleted = dst_col.delete_many(filt).deleted_count

    inserted = 0
    batch: list[dict[str, Any]] = []
    for doc in src_col.find(filt, batch_size=batch_size):
        doc.pop("_id", None)
        batch.append(doc)
        if len(batch) >= batch_size:
            dst_col.insert_many(batch, ordered=False)
            inserted += len(batch)
            batch = []
            if inserted % PROGRESS_EVERY < batch_size:
                _log(f"      …已写入 {inserted}/{src_count}")
    if batch:
        dst_col.insert_many(batch, ordered=False)
        inserted += len(batch)

    return {"source": src_count, "deleted": deleted, "inserted": inserted}


def sync_rq_minute_for_dates(
    trade_dates: list[str],
    *,
    source_alias: str = DEFAULT_SOURCE_ALIAS,
    target_alias: str = DEFAULT_TARGET_ALIAS,
    mongo_db: str = MINUTE_DB,
) -> dict[str, Any]:
    src_client = get_client(source_alias)
    dst_client = get_client(target_alias)
    src_db = src_client[mongo_db]
    dst_db = dst_client[mongo_db]

    per_date: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for trade_date in trade_dates:
        coll_name = minute_collection_for_date(trade_date)
        _log(f"\n=== 同步分钟线 {trade_date} → {mongo_db}.{coll_name} ===")
        if coll_name not in src_db.list_collection_names():
            err = f"{trade_date}/{coll_name}: 源库无该集合"
            _log(f"  {err}")
            errors.append(err)
            per_date[trade_date] = {
                "collection": coll_name,
                "source": 0,
                "deleted": 0,
                "inserted": 0,
            }
            continue

        stats = sync_minute_for_date(
            src_col=src_db[coll_name],
            dst_col=dst_db[coll_name],
            trade_date=trade_date,
        )
        stats["collection"] = coll_name
        per_date[trade_date] = stats
        _log(
            f"  源 {stats['source']} 条，"
            f"删目标 {stats['deleted']} 条，写入 {stats['inserted']} 条"
        )
        if stats["source"] == 0:
            errors.append(f"{trade_date}/{coll_name}: 源库无数据")
        elif stats["source"] != stats["inserted"]:
            errors.append(
                f"{trade_date}/{coll_name}: 源 {stats['source']} 条，"
                f"实际写入 {stats['inserted']} 条"
            )

    ok = not errors
    return {
        "ok": ok,
        "source_alias": source_alias,
        "target_alias": target_alias,
        "mongo_db": mongo_db,
        "trade_dates": trade_dates,
        "per_date": per_date,
        "errors": errors,
    }


def sync_rq_minute_t_minus_one(
    *,
    source_alias: str = DEFAULT_SOURCE_ALIAS,
    target_alias: str = DEFAULT_TARGET_ALIAS,
    mongo_trade_alias: str | None = None,
    fmt: str = "%Y-%m-%d",
) -> dict[str, Any]:
    from trade_date_utils import previous_trade_date

    alias = mongo_trade_alias or source_alias
    target = previous_trade_date(mongo_alias=alias, fmt=fmt)
    return sync_rq_minute_for_dates(
        [target],
        source_alias=source_alias,
        target_alias=target_alias,
    )


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="同步 rq_minute.rq_minute_none_YYYY 到远端 MongoDB"
    )
    p.add_argument("--date", help="单个交易日 YYYY-MM-DD；省略且未指定区间时取 T-1")
    p.add_argument("--start", help="区间起（含），与 --end 合用")
    p.add_argument("--end", help="区间止（含）")
    p.add_argument(
        "--source-alias",
        default=DEFAULT_SOURCE_ALIAS,
        help=f"源 Mongo 别名，默认 {DEFAULT_SOURCE_ALIAS}",
    )
    p.add_argument(
        "--target-alias",
        default=DEFAULT_TARGET_ALIAS,
        help=f"目标 Mongo 别名，默认 {DEFAULT_TARGET_ALIAS}",
    )
    p.add_argument(
        "--mongo-trade-alias",
        default=None,
        help="解析 T-1 / 交易日历时用的别名（默认与 --source-alias 相同）",
    )
    return p.parse_args()


def main() -> int:
    args = _cli_args()
    trade_alias = args.mongo_trade_alias or args.source_alias

    try:
        if args.start or args.end:
            if not args.start or not args.end:
                raise ValueError("区间同步须同时指定 --start 与 --end")
            from trade_date_utils import list_trade_dates, parse_start_end_range

            start_s, end_s = parse_start_end_range(args.start, args.end)
            dates = list_trade_dates(start_s, end_s, mongo_alias=trade_alias)
            result = sync_rq_minute_for_dates(
                dates,
                source_alias=args.source_alias,
                target_alias=args.target_alias,
            )
        elif args.date:
            result = sync_rq_minute_for_dates(
                [str(args.date).strip().replace("/", "-")],
                source_alias=args.source_alias,
                target_alias=args.target_alias,
            )
        else:
            result = sync_rq_minute_t_minus_one(
                source_alias=args.source_alias,
                target_alias=args.target_alias,
                mongo_trade_alias=trade_alias,
            )

        if result["errors"]:
            _log("\n同步完成但有异常：")
            for err in result["errors"]:
                _log(f"  - {err}")
            return 1

        _log("\n同步成功")
        return 0
    except Exception as e:
        _log(f"失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
