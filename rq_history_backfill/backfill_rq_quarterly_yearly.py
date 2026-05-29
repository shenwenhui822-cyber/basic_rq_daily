"""
历史区间逐交易日拉米筐 PIT，写入 basic_rq.rq_quarterly / rq_yearly；
可选在结束后跑一次 ffill_rq_quarterly_yearly_to_universe 按票补齐 universe。

前提：对应日期 rq_base_info 已有 code_rq；economic.trade_dates 可用。

用法（basic_rq_daily 根目录）::

    python rq_history_backfill/backfill_rq_quarterly_yearly.py --start 2015-01-05 --end 2026-05-27
    python rq_history_backfill/backfill_rq_quarterly_yearly.py --start 2015-01-05 --end 2026-05-27 --skip-existing
    python rq_history_backfill/backfill_rq_quarterly_yearly.py --ffill-only --start 2015-01-05 --end 2026-05-27
    python rq_history_backfill/backfill_rq_quarterly_yearly.py --start 2026-05-26 --end 2026-05-26 --collections quarterly
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import pymongo

_PKG_ROOT = Path(__file__).resolve().parents[1]
_BENCH_ROOT = _PKG_ROOT / "bench_quarterly_yearly"
for _p in (_PKG_ROOT, _BENCH_ROOT):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from mongo_connect import get_client
from trade_date_utils import mongo_trade_date_range, parse_explicit_date_arg, parse_start_end_range

from MasterData.data_rq.ffill_rq_quarterly_yearly_missing import (
    _load_all_trade_days_sorted,
    _norm_date,
    _trade_days_in_sorted,
)
from MasterData.data_rq.ffill_rq_quarterly_yearly_to_universe import ffill_fin_to_universe
from MasterData.data_rq.update_rq_quarterly_yearly_bench import (
    update_rq_quarterly,
    update_rq_yearly,
)


def _ensure_fin_indexes(client, mongo_db: str) -> None:
    db = client[mongo_db]
    for name in ("rq_quarterly", "rq_yearly"):
        t = db[name]
        t.create_index(
            [("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)],
            background=True,
            unique=True,
        )
        t.create_index(
            [("rptdate", pymongo.ASCENDING), ("code_w", pymongo.ASCENDING)],
            background=True,
        )
        t.create_index([("stm_issuingdate", pymongo.ASCENDING)], background=True)


def _norm_day(s: str) -> str:
    return parse_explicit_date_arg(s)


def _day_variants(s: str) -> list[str]:
    s = _norm_day(s)
    y, m, d = s.split("-")
    return list(dict.fromkeys([s, f"{y}/{m}/{d}"]))


def _trade_day_pairs(client, start: str, end: str) -> list[tuple[str, str]]:
    """(pre_trade_day, today) 列表；today 为 pre 的下一交易日。"""
    all_td = _load_all_trade_days_sorted(client)
    trade_list = _trade_days_in_sorted(all_td, start, end)
    if len(trade_list) < 2:
        if len(trade_list) == 1:
            nxt = _trade_days_in_sorted(all_td, trade_list[0], "2099-12-31")
            if len(nxt) >= 2:
                return [(trade_list[0], nxt[1])]
        return []
    pairs: list[tuple[str, str]] = []
    idx = {d: i for i, d in enumerate(all_td)}
    for d in trade_list:
        i = idx.get(d)
        if i is None or i + 1 >= len(all_td):
            continue
        nxt = all_td[i + 1]
        if _norm_date(nxt) and _norm_date(nxt) > _norm_date(d):
            pairs.append((d, nxt))
    return pairs


def _day_has_data(coll, day: str) -> bool:
    return coll.find_one({"date": {"$in": _day_variants(day)}}, {"_id": 1}) is not None


def backfill_rq_quarterly_yearly(
    *,
    start: str,
    end: str,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    collections: str = "both",
    skip_existing: bool = False,
    do_quarterly: bool = True,
    do_yearly: bool = True,
) -> tuple[int, int, int, int]:
    client = get_client(mongo_alias)
    db = client[mongo_db]
    pairs = _trade_day_pairs(client, start, end)
    if not pairs:
        print(f"区间 {start} ~ {end} 无可用 (pre, today) 交易日对，退出")
        return 0, 0, 0, 0

    print(
        f"补齐区间: pre={pairs[0][0]} ~ {pairs[-1][0]}，"
        f"共 {len(pairs)} 个交易日（today 取下一交易日）"
    )

    try:
        _ensure_fin_indexes(client, mongo_db)
    except Exception as e:
        print(f"索引提示（可忽略）: {e}")

    q_ok = q_skip = y_ok = y_skip = 0
    q_coll = db["rq_quarterly"]
    y_coll = db["rq_yearly"]

    for i, (pre, today) in enumerate(pairs, start=1):
        print(f"\n=== [{i}/{len(pairs)}] pre={pre} today={today} ===")

        if do_quarterly:
            if skip_existing and _day_has_data(q_coll, pre):
                print(f"  [skip] rq_quarterly 已有 date={pre}")
                q_skip += 1
            else:
                if update_rq_quarterly(
                    pre,
                    today,
                    mongo_alias=mongo_alias,
                    mongo_db=mongo_db,
                    target_coll="rq_quarterly",
                ):
                    q_ok += 1
                else:
                    print(f"  [warn] rq_quarterly 失败 pre={pre}")

        if do_yearly:
            if skip_existing and _day_has_data(y_coll, pre):
                print(f"  [skip] rq_yearly 已有 date={pre}")
                y_skip += 1
            else:
                if update_rq_yearly(
                    pre,
                    today,
                    mongo_alias=mongo_alias,
                    mongo_db=mongo_db,
                    target_coll="rq_yearly",
                ):
                    y_ok += 1
                else:
                    print(f"  [warn] rq_yearly 失败 pre={pre}")

    print(
        f"\n拉数完成：quarterly ok={q_ok} skip={q_skip}；"
        f"yearly ok={y_ok} skip={y_skip}"
    )
    return q_ok, q_skip, y_ok, y_skip


def run_ffill_to_universe(
    *,
    start: str,
    end: str,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    collections: str = "both",
    insert_chunk: int = 2000,
    progress_every: int = 10,
) -> None:
    client = get_client(mongo_alias)
    all_td = _load_all_trade_days_sorted(client)
    trade_list = _trade_days_in_sorted(all_td, start, end)
    if not trade_list:
        print(f"ffill 区间无交易日: {start} ~ {end}")
        return

    db = client[mongo_db]
    base_coll = db["rq_base_info"]
    names: list[tuple[str, str]] = []
    if collections in ("both", "quarterly"):
        names.append(("rq_quarterly", "季报"))
    if collections in ("both", "yearly"):
        names.append(("rq_yearly", "年报"))

    for coll_name, label in names:
        print(f"\n=== ffill {coll_name}（{label}）{trade_list[0]} ~ {trade_list[-1]} ===")
        di, rows = ffill_fin_to_universe(
            db[coll_name],
            base_coll,
            trade_list,
            dry_run=False,
            verbose=False,
            insert_chunk=max(100, insert_chunk),
            progress_every=max(0, progress_every),
            mongo_attempts=6,
            mongo_delay=1.25,
        )
        print(f"  小结：有插入的交易日 {di} 个，插入行数约 {rows}")


def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="历史补齐 rq_quarterly / rq_yearly + 可选 universe ffill")
    p.add_argument("--start", default="2015-01-05")
    p.add_argument("--end", default=None, help="默认 rq_base_info 最晚日")
    p.add_argument("--mongo-alias", default="wonderwz27018_rw")
    p.add_argument("--mongo-db", default="basic_rq")
    p.add_argument(
        "--collections",
        default="both",
        choices=("both", "quarterly", "yearly"),
    )
    p.add_argument("--skip-existing", action="store_true", help="已有该 date 则跳过（断点续跑）")
    p.add_argument("--no-ffill", action="store_true", help="拉数后不跑 to_universe")
    p.add_argument("--ffill-only", action="store_true", help="只跑 to_universe，不拉米筐")
    p.add_argument("--insert-chunk", type=int, default=2000)
    p.add_argument("--progress-every", type=int, default=10)
    return p.parse_args()


def _default_end(client, mongo_db: str) -> str:
    doc = client[mongo_db]["rq_base_info"].find_one({}, {"date": 1}, sort=[("date", -1)])
    if not doc:
        raise SystemExit("rq_base_info 为空，无法推断 --end")
    return _norm_day(str(doc["date"]))


if __name__ == "__main__":
    args = _cli()
    client = get_client(args.mongo_alias)
    start_s = _norm_day(args.start)
    end_s = _norm_day(args.end) if args.end else _default_end(client, args.mongo_db)
    if start_s > end_s:
        raise SystemExit(f"start {start_s} > end {end_s}")

    do_q = args.collections in ("both", "quarterly")
    do_y = args.collections in ("both", "yearly")

    if not args.ffill_only:
        backfill_rq_quarterly_yearly(
            start=start_s,
            end=end_s,
            mongo_alias=args.mongo_alias,
            mongo_db=args.mongo_db,
            collections=args.collections,
            skip_existing=args.skip_existing,
            do_quarterly=do_q,
            do_yearly=do_y,
        )

    if args.ffill_only or not args.no_ffill:
        run_ffill_to_universe(
            start=start_s,
            end=end_s,
            mongo_alias=args.mongo_alias,
            mongo_db=args.mongo_db,
            collections=args.collections,
            insert_chunk=args.insert_chunk,
            progress_every=args.progress_every,
        )

    print("\n[done]")
