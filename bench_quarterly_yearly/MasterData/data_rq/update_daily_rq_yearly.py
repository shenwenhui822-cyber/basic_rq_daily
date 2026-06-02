"""
每日更新 + 按票补齐 basic_rq.rq_yearly。

执行内容：
1) 先更新 pre_trade_day 当日 rq_yearly（米筐 PIT）
2) 再按 rq_base_info 当日 universe 逐票前向补齐历史缺口（backfill）

默认用于自动任务：每天跑一次即可持续修复历史缺失。

用法（项目根目录，PYTHONPATH=.）：
  python -u MasterData/data_rq/update_daily_rq_yearly.py
  python -u MasterData/data_rq/update_daily_rq_yearly.py --run-date 2026-04-20
  python -u MasterData/data_rq/update_daily_rq_yearly.py --auto-mode previous_trade
  python -u MasterData/data_rq/update_daily_rq_yearly.py --pre-day 2026-04-17
  python -u MasterData/data_rq/update_daily_rq_yearly.py --backfill-start 2020-01-02
  python -u MasterData/data_rq/update_daily_rq_yearly.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import pymongo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows 控制台默认 gbk 时，避免被其他模块中的 emoji 日志触发编码异常。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from DataBase.db_client import get_client_U
from MasterData.data_rq.ffill_rq_quarterly_yearly_missing import (
    _load_all_trade_days_sorted,
    _norm_date,
    _trade_days_in_sorted,
)
from MasterData.data_rq.ffill_rq_quarterly_yearly_to_universe import ffill_fin_to_universe
from MasterData.data_rq.update_rq_quarterly_yearly_bench import update_rq_yearly


def _norm_day(s: str) -> str:
    s = str(s).strip()
    if "/" in s:
        s = s.replace("/", "-")
    return s[:10]


def _is_trade_day(client, day: str) -> bool:
    return (
        client.economic.trade_dates.find_one(
            {"trade_date": _norm_day(day)},
            {"_id": 0, "trade_date": 1},
        )
        is not None
    )


def _last_trade_day_on_or_before(client, day: str) -> str | None:
    doc = client.economic.trade_dates.find_one(
        {"trade_date": {"$lte": _norm_day(day)}},
        {"_id": 0, "trade_date": 1},
        sort=[("trade_date", -1)],
    )
    return doc["trade_date"] if doc else None


def _previous_trade_day(client, day: str) -> str | None:
    doc = client.economic.trade_dates.find_one(
        {"trade_date": {"$lt": _norm_day(day)}},
        {"_id": 0, "trade_date": 1},
        sort=[("trade_date", -1)],
    )
    return doc["trade_date"] if doc else None


def _next_trade_day(client, day: str) -> str | None:
    doc = client.economic.trade_dates.find_one(
        {"trade_date": {"$gt": _norm_day(day)}},
        {"_id": 0, "trade_date": 1},
        sort=[("trade_date", 1)],
    )
    return doc["trade_date"] if doc else None


def resolve_pre_trade_day(client, run_date: str, auto_mode: str) -> str:
    run_date = _norm_day(run_date)
    if auto_mode == "today_if_trade":
        if _is_trade_day(client, run_date):
            return run_date
        d = _last_trade_day_on_or_before(client, run_date)
        if d:
            return d
    elif auto_mode == "previous_trade":
        d = _previous_trade_day(client, run_date)
        if d:
            return d
    raise RuntimeError(f"无法根据 run-date={run_date} 推导 pre_trade_day（mode={auto_mode}）")


def ensure_indexes_yearly(client, mongo_db: str, coll_name: str = "rq_yearly") -> None:
    t = client[mongo_db][coll_name]
    t.create_index([("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)], background=True, unique=True)
    t.create_index([("rptdate", pymongo.ASCENDING), ("code_w", pymongo.ASCENDING)], background=True)
    t.create_index([("stm_issuingdate", pymongo.ASCENDING)], background=True)


def run_backfill_yearly(
    *,
    client,
    mongo_db: str,
    start: str,
    end: str,
    dry_run: bool,
    insert_chunk: int,
    progress_every: int,
    mongo_retries: int,
    mongo_retry_delay: float,
    verbose: bool,
) -> tuple[int, int]:
    start = _norm_date(start) or _norm_day(start)
    end = _norm_date(end) or _norm_day(end)
    if start > end:
        print(f"[backfill] 区间为空（start={start} > end={end}），跳过")
        return 0, 0

    all_td = _load_all_trade_days_sorted(client)
    trade_list = _trade_days_in_sorted(all_td, start, end)
    if not trade_list:
        print(f"[backfill] 区间无交易日：{start} ~ {end}")
        return 0, 0

    print(f"[backfill] rq_yearly 逐票补齐区间：{trade_list[0]} ~ {trade_list[-1]}（{len(trade_list)} 个交易日）")
    db = client[mongo_db]
    days, rows = ffill_fin_to_universe(
        db["rq_yearly"],
        db["rq_base_info"],
        trade_list,
        dry_run=dry_run,
        verbose=verbose,
        insert_chunk=max(100, int(insert_chunk)),
        progress_every=max(0, int(progress_every)),
        mongo_attempts=max(1, int(mongo_retries)),
        mongo_delay=max(0.1, float(mongo_retry_delay)),
    )
    print(f"[backfill] 完成：有插入的交易日 {days} 个，插入行数约 {rows}（dry_run={dry_run}）")
    return days, rows


def main() -> None:
    p = argparse.ArgumentParser(description="每日更新 + 按票补齐 rq_yearly")
    p.add_argument("--pre-day", default=None, help="显式 pre_trade_day（YYYY-MM-DD）；指定后忽略 run-date/auto-mode")
    p.add_argument("--run-date", default=dt.date.today().isoformat(), help="任务运行日，默认今天")
    p.add_argument(
        "--auto-mode",
        default="previous_trade",
        choices=["today_if_trade", "previous_trade"],
        help="auto 推导规则：previous_trade(默认,T-1) / today_if_trade",
    )
    p.add_argument("--mongo-alias", default="local", help="Mongo 别名，默认 local")
    p.add_argument("--mongo-db", default="basic_rq", help="数据库名，默认 basic_rq")
    p.add_argument("--no-index", action="store_true", help="跳过索引处理")
    p.add_argument("--no-backfill", action="store_true", help="只做当日日更，不做历史逐票补齐")
    p.add_argument("--backfill-start", default="2020-01-02", help="backfill 起始日（含），默认 2020-01-02")
    p.add_argument(
        "--backfill-end",
        default=None,
        help="backfill 截止日（含），默认使用本次 pre_trade_day",
    )
    p.add_argument("--insert-chunk", type=int, default=2000, help="backfill insert_many 每批条数")
    p.add_argument("--progress-every", type=int, default=10, help="backfill 进度打印频率（每 N 个交易日）")
    p.add_argument("--mongo-retries", type=int, default=6, help="backfill Mongo 重试次数")
    p.add_argument("--mongo-retry-delay", type=float, default=1.25, help="backfill Mongo 重试基数秒")
    p.add_argument("--verbose", action="store_true", help="打印更详细 backfill 日志")
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的动作，不写库")
    args = p.parse_args()

    client = get_client_U(args.mongo_alias)

    if args.pre_day:
        pre_trade_day = _norm_day(args.pre_day)
        print(f"[manual] pre_trade_day={pre_trade_day}")
    else:
        pre_trade_day = resolve_pre_trade_day(client, args.run_date, args.auto_mode)
        print(f"[auto] run-date={_norm_day(args.run_date)} mode={args.auto_mode} -> pre_trade_day={pre_trade_day}")

    today_for_update = _next_trade_day(client, pre_trade_day)
    if not today_for_update:
        raise SystemExit(f"无法找到 {pre_trade_day} 的下一交易日，update_rq_yearly 无法执行")

    backfill_start = _norm_day(args.backfill_start)
    backfill_end = _norm_day(args.backfill_end) if args.backfill_end else pre_trade_day

    if args.dry_run:
        print(f"[dry-run] 将执行 update_rq_yearly(pre={pre_trade_day}, today={today_for_update})")
        if args.no_backfill:
            print("[dry-run] no-backfill=true，跳过 backfill")
        else:
            print(f"[dry-run] 将执行 rq_yearly 逐票 backfill: {backfill_start} ~ {backfill_end}")
        return

    if not args.no_index:
        try:
            ensure_indexes_yearly(client, args.mongo_db, "rq_yearly")
        except Exception as e:
            print(f"索引处理提示（可忽略）: {e}")

    ok, _rows = update_rq_yearly(
        pre_trade_day,
        today_for_update,
        mongo_alias=args.mongo_alias,
        mongo_db=args.mongo_db,
        target_coll="rq_yearly",
    )
    if not ok:
        raise SystemExit(f"rq_yearly 日更失败：pre={pre_trade_day} today={today_for_update}")
    print(f"[daily] rq_yearly 已更新：pre={pre_trade_day} today={today_for_update}")

    if args.no_backfill:
        print("[done] 已完成当日日更（未执行 backfill）")
        return

    run_backfill_yearly(
        client=client,
        mongo_db=args.mongo_db,
        start=backfill_start,
        end=backfill_end,
        dry_run=False,
        insert_chunk=args.insert_chunk,
        progress_every=args.progress_every,
        mongo_retries=args.mongo_retries,
        mongo_retry_delay=args.mongo_retry_delay,
        verbose=args.verbose,
    )
    print("[done] 日更 + backfill 已完成")


if __name__ == "__main__":
    main()
