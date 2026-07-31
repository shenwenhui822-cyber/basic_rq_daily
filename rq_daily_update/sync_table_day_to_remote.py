# -*- coding: utf-8 -*-
"""
将 basic_rq 指定表、指定交易日的数据同步到远端 MongoDB 114.80.62.203:27019。

源：wonderwz27018_rw @ 192.168.110.199:27018
目标：wonderwz203_19_rw @ 114.80.62.203:27019

用法（basic_rq_daily 根目录）：
  # 列出可同步的表
  python rq_daily_update/sync_table_day_to_remote.py --list

  # 同步单表单日
  python rq_daily_update/sync_table_day_to_remote.py --table rq_quarterly --date 2026-07-01

  # 同步多表同一天（逗号分隔或重复 --table）
  python rq_daily_update/sync_table_day_to_remote.py --table rq_quarterly,rq_yearly --date 2026-07-01
  python rq_daily_update/sync_table_day_to_remote.py --table rq_quarterly --table rq_yearly --date 2026-07-01

  # 仅预览，不写远端
  python rq_daily_update/sync_table_day_to_remote.py --table rq_quarterly --date 2026-07-01 --dry-run

  # 自定义源/目标别名
  python rq_daily_update/sync_table_day_to_remote.py --table rq_bench --date 2026-07-02 \\
      --source-alias wonderwz27018_rw --target-alias wonderwz203_19_rw
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__, daily=True)

from sync_basic_rq_to_remote import (  # noqa: E402
    BASIC_RQ_COLLECTIONS,
    BASIC_RQ_DB,
    DEFAULT_SOURCE_ALIAS,
    DEFAULT_TARGET_ALIAS,
    date_variants,
    sync_basic_rq_for_dates,
)
from usedbdef import get_client  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def _norm_date(s: str) -> str:
    return str(s).strip().replace("/", "-")[:10]


def _parse_tables(raw: list[str] | None) -> list[str]:
    if not raw:
        raise ValueError("须指定 --table（表名），可用 --list 查看可选表")
    out: list[str] = []
    for item in raw:
        for part in str(item).split(","):
            name = part.strip()
            if name:
                out.append(name)
    # 去重保序
    return list(dict.fromkeys(out))


def _validate_tables(tables: list[str]) -> None:
    unknown = set(tables) - set(BASIC_RQ_COLLECTIONS)
    if unknown:
        raise ValueError(
            f"未知表: {sorted(unknown)}；可选: {', '.join(BASIC_RQ_COLLECTIONS)}"
        )


def preview_sync(
    *,
    tables: list[str],
    trade_date: str,
    source_alias: str = DEFAULT_SOURCE_ALIAS,
    target_alias: str = DEFAULT_TARGET_ALIAS,
    mongo_db: str = BASIC_RQ_DB,
) -> dict[str, dict[str, int]]:
    src_client = get_client(source_alias)
    dst_client = get_client(target_alias)
    src_db = src_client[mongo_db]
    dst_db = dst_client[mongo_db]
    filt = {"date": {"$in": date_variants(trade_date)}}

    stats: dict[str, dict[str, int]] = {}
    for name in tables:
        src_count = src_db[name].count_documents(filt)
        dst_count = dst_db[name].count_documents(filt)
        stats[name] = {"source": src_count, "target_before": dst_count}
    return stats


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="同步 basic_rq 指定表、指定交易日到远端 114.80.62.203:27019",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "可选表：\n  "
            + "\n  ".join(BASIC_RQ_COLLECTIONS)
        ),
    )
    p.add_argument(
        "--table",
        "--collection",
        dest="tables",
        action="append",
        help="表名（basic_rq 集合名），可重复或逗号分隔",
    )
    p.add_argument(
        "--date",
        required=False,
        help="交易日 YYYY-MM-DD（必填，除非 --list）",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="列出可同步的表并退出",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计源/目标条数，不写入远端",
    )
    p.add_argument(
        "--source-alias",
        default=DEFAULT_SOURCE_ALIAS,
        help=f"源 Mongo 别名，默认 {DEFAULT_SOURCE_ALIAS}",
    )
    p.add_argument(
        "--target-alias",
        default=DEFAULT_TARGET_ALIAS,
        help=f"目标 Mongo 别名，默认 {DEFAULT_TARGET_ALIAS}（114.80.62.203:27019）",
    )
    p.add_argument(
        "--mongo-db",
        default=BASIC_RQ_DB,
        help=f"数据库名，默认 {BASIC_RQ_DB}",
    )
    return p.parse_args()


def main() -> int:
    args = _cli_args()

    if args.list:
        _log(f"可同步表（库 {args.mongo_db}，目标 {args.target_alias} @ 114.80.62.203:27019）：")
        for name in BASIC_RQ_COLLECTIONS:
            _log(f"  - {name}")
        return 0

    if not args.date:
        _log("错误：须指定 --date YYYY-MM-DD")
        return 2

    try:
        tables = _parse_tables(args.tables)
        _validate_tables(tables)
        trade_date = _norm_date(args.date)
    except ValueError as e:
        _log(f"错误：{e}")
        return 2

    _log(
        f"同步 {args.mongo_db}：{', '.join(tables)} | date={trade_date} | "
        f"{args.source_alias} -> {args.target_alias}"
    )

    if args.dry_run:
        stats = preview_sync(
            tables=tables,
            trade_date=trade_date,
            source_alias=args.source_alias,
            target_alias=args.target_alias,
            mongo_db=args.mongo_db,
        )
        _log("\n[dry-run] 预览：")
        for name, s in stats.items():
            _log(
                f"  {name}: 源 {s['source']} 条，目标现有 {s['target_before']} 条"
            )
        empty = [n for n, s in stats.items() if s["source"] == 0]
        if empty:
            _log(f"\n[dry-run] 警告：源库无数据 — {', '.join(empty)}")
            return 1
        _log("\n[dry-run] 未写入远端")
        return 0

    result = sync_basic_rq_for_dates(
        [trade_date],
        source_alias=args.source_alias,
        target_alias=args.target_alias,
        mongo_db=args.mongo_db,
        collections=tables,
        with_minute=False,
    )

    if result["errors"]:
        _log("\n同步完成但有异常：")
        for err in result["errors"]:
            _log(f"  - {err}")
        return 1

    _log("\n同步成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
