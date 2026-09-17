# -*- coding: utf-8 -*-
"""
将清单内 ETF 的 ``basic_rq.rq_daily_price_none`` 按交易日同步到远端。

源：wonderwz27018_rw @ 192.168.110.199:27018
目标：wonderwz203_19_rw @ 114.80.62.203:27019

只覆盖清单 ETF（按 code_rq），不影响同日其它股票记录。

用法（仓库根目录）：
  python ETF_price_date/sync_etf_daily_price_to_remote.py
  python ETF_price_date/sync_etf_daily_price_to_remote.py --date 2026-09-16
  python ETF_price_date/sync_etf_daily_price_to_remote.py --start 2026-09-01 --end 2026-09-16
  python ETF_price_date/sync_etf_daily_price_to_remote.py --start 2026-09-01 --end 2026-09-16 --code 159830,510300
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

bootstrap(__file__)

from trade_date_utils import (
    list_trade_dates,
    parse_explicit_date_arg,
    parse_start_end_range,
    previous_trade_date,
)
from usedbdef import get_client

# 与 load_etf_daily_price.DEFAULT_ETF_CODES 保持一致（同步脚本不登录米筐）
DEFAULT_ETF_CODES: tuple[str, ...] = (
    "159830",
    "159845",
    "159919",
    "159922",
    "159925",
    "159934",
    "159937",
    "510050",
    "510100",
    "510300",
    "510310",
    "510330",
    "510350",
    "510360",
    "510500",
    "510510",
    "510580",
    "511010",
    "511090",
    "511130",
    "511260",
    "512100",
    "512500",
    "515330",
    "515380",
    "515390",
    "515660",
    "518600",
    "518660",
    "588080",
)

MONGO_DB = "basic_rq"
COLLECTION = "rq_daily_price_none"
DEFAULT_SOURCE_ALIAS = "wonderwz27018_rw"
DEFAULT_TARGET_ALIAS = "wonderwz203_19_rw"
INSERT_BATCH = 2000
DATE_FMT_DB = "%Y-%m-%d"


def _log(msg: str) -> None:
    print(msg, flush=True)


def date_variants(d: str) -> list[str]:
    d = str(d).strip()
    return list({d, d.replace("-", "/"), d.replace("/", "-")})


def etf_code_to_code_rq(code: str) -> str:
    """与拉取脚本约定一致：15/16→XSHE，其余→XSHG。"""
    raw = str(code).strip().upper()
    if raw.endswith(".XSHE") or raw.endswith(".XSHG"):
        return raw
    digits = raw
    if digits.startswith("SZ") or digits.startswith("SH"):
        digits = digits[2:]
    digits = "".join(ch for ch in digits if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"无法解析 ETF 代码: {code!r}")
    if digits.startswith(("15", "16")):
        return f"{digits}.XSHE"
    return f"{digits}.XSHG"


def parse_etf_codes(raw: list[str] | None) -> list[str]:
    if not raw:
        codes = list(DEFAULT_ETF_CODES)
    else:
        codes = []
        for item in raw:
            for part in str(item).split(","):
                c = part.strip()
                if c:
                    codes.append(c)
        codes = list(dict.fromkeys(codes)) or list(DEFAULT_ETF_CODES)
    return [etf_code_to_code_rq(c) for c in codes]


def ensure_indexes(table: Any) -> None:
    table.create_index(
        [("date", 1), ("code_rq", 1)],
        unique=True,
        background=True,
        name="uniq_date_code_rq",
    )
    table.create_index([("date", 1)], background=True, name="idx_date")


def sync_etf_for_date(
    *,
    src_col: Any,
    dst_col: Any,
    trade_date: str,
    code_rqs: list[str],
    batch_size: int = INSERT_BATCH,
) -> dict[str, int]:
    filt = {
        "date": {"$in": date_variants(trade_date)},
        "code_rq": {"$in": code_rqs},
    }
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
    if batch:
        dst_col.insert_many(batch, ordered=False)
        inserted += len(batch)

    return {"source": src_count, "deleted": deleted, "inserted": inserted}


def sync_etf_daily_for_dates(
    trade_dates: list[str],
    *,
    code_rqs: list[str],
    source_alias: str = DEFAULT_SOURCE_ALIAS,
    target_alias: str = DEFAULT_TARGET_ALIAS,
    mongo_db: str = MONGO_DB,
) -> dict[str, Any]:
    src_client = get_client(source_alias)
    dst_client = get_client(target_alias)
    src_col = src_client[mongo_db][COLLECTION]
    dst_col = dst_client[mongo_db][COLLECTION]
    ensure_indexes(dst_col)

    per_date: dict[str, dict[str, int]] = {}
    errors: list[str] = []

    _log(f"同步 ETF 数: {len(code_rqs)} | {source_alias} → {target_alias}")
    for trade_date in trade_dates:
        _log(f"\n=== 同步 ETF 日线 {trade_date} ===")
        stats = sync_etf_for_date(
            src_col=src_col,
            dst_col=dst_col,
            trade_date=trade_date,
            code_rqs=code_rqs,
        )
        per_date[trade_date] = stats
        _log(
            f"  源 {stats['source']} 条，"
            f"删目标 {stats['deleted']} 条，写入 {stats['inserted']} 条"
        )
        if stats["source"] == 0:
            errors.append(f"{trade_date}: 源库无 ETF 数据")
        elif stats["source"] != stats["inserted"]:
            errors.append(
                f"{trade_date}: 源 {stats['source']} 条，实际写入 {stats['inserted']} 条"
            )
        elif stats["source"] < len(code_rqs):
            errors.append(
                f"{trade_date}: 源仅 {stats['source']}/{len(code_rqs)} 只 ETF"
            )

    return {
        "ok": not errors,
        "source_alias": source_alias,
        "target_alias": target_alias,
        "mongo_db": mongo_db,
        "collection": COLLECTION,
        "code_rqs": code_rqs,
        "trade_dates": trade_dates,
        "per_date": per_date,
        "errors": errors,
    }


def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="同步清单 ETF 日线到远端 wonderwz203_19_rw（支持单日/区间）"
    )
    p.add_argument("--date", "-d", default=None, help="单个交易日；与 --start/--end 互斥")
    p.add_argument("--start", default=None, help="区间起（含）")
    p.add_argument("--end", default=None, help="区间止（含）")
    p.add_argument(
        "--code",
        action="append",
        dest="codes",
        help="ETF 代码，可重复或逗号分隔；省略则用内置清单",
    )
    p.add_argument("--source-alias", default=DEFAULT_SOURCE_ALIAS)
    p.add_argument("--target-alias", default=DEFAULT_TARGET_ALIAS)
    p.add_argument(
        "--mongo-trade-alias",
        default=None,
        help="解析 T-1 / 交易日历别名（默认与 --source-alias 相同）",
    )
    return p.parse_args()


def main() -> int:
    args = _cli()
    trade_alias = args.mongo_trade_alias or args.source_alias
    code_rqs = parse_etf_codes(args.codes)

    try:
        if args.start or args.end:
            if not args.start or not args.end:
                raise ValueError("区间须同时指定 --start 与 --end")
            if args.date:
                raise ValueError("--date 与 --start/--end 不能同时使用")
            start_s, end_s = parse_start_end_range(args.start, args.end, fmt=DATE_FMT_DB)
            dates = list_trade_dates(start_s, end_s, mongo_alias=trade_alias)
            if not dates:
                raise ValueError(f"区间 {start_s}~{end_s} 无交易日")
            _log(f"目标区间: {start_s} ~ {end_s}（{len(dates)} 个交易日）")
        elif args.date:
            dates = [parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)]
            _log(f"目标日期: {dates[0]}")
        else:
            dates = [previous_trade_date(mongo_alias=trade_alias, fmt=DATE_FMT_DB)]
            _log(f"目标日期: {dates[0]}（T-1）")

        result = sync_etf_daily_for_dates(
            dates,
            code_rqs=code_rqs,
            source_alias=args.source_alias,
            target_alias=args.target_alias,
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
