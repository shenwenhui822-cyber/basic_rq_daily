"""
每日更新 basic_rq.rq_bench（基准指数行情，对标 Wind w_bench）。

默认 T-1（上一交易日）；入库 date：YYYY-MM-DD。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
for _p in (_PKG_ROOT, _PKG_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from rq_paths import bootstrap

bootstrap(__file__)

import argparse

from rq_history_backfill.backfill_rq_bench import create_indexes_rq_bench, update_rq_bench
from trade_date_utils import is_trade_day, parse_explicit_date_arg, previous_trade_date
from usedbdef import get_client

DATE_FMT_DB = "%Y-%m-%d"


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="日更 rq_bench（默认 T-1）")
    p.add_argument("--date", "-d", default=None, help="目标交易日；默认上一交易日")
    p.add_argument("--mongo-alias", default="wonderwz27018_rw")
    p.add_argument("--mongo-db", default="basic_rq")
    p.add_argument("--min-date", default="1990-01-01", help="早于此日不写 bench")
    p.add_argument("--index", action="store_true", help="仅建 (date,code) 索引")
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    client = get_client(args.mongo_alias)

    if args.index:
        create_indexes_rq_bench(
            mongo_alias=args.mongo_alias,
            mongo_db=args.mongo_db,
            client=client,
        )
        print("rq_bench 索引已处理")
        raise SystemExit(0)

    target = (
        parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
        if args.date
        else previous_trade_date(mongo_alias=args.mongo_alias, client=client, fmt=DATE_FMT_DB)
    )

    if not is_trade_day(target, mongo_alias=args.mongo_alias, client=client):
        print(f"{target} 不是交易日，跳过")
        raise SystemExit(0)

    create_indexes_rq_bench(
        mongo_alias=args.mongo_alias,
        mongo_db=args.mongo_db,
        client=client,
    )
    ok = update_rq_bench(
        target,
        mongo_alias=args.mongo_alias,
        mongo_db=args.mongo_db,
        min_date=str(args.min_date)[:10],
        client=client,
    )
    raise SystemExit(0 if ok else 1)
