"""
每日更新 basic_rq.rq_bench（用于定时任务）。

默认策略（auto-mode=today_if_trade）：
- 若 run-date 是交易日：更新 run-date 当天 bench
- 若 run-date 非交易日：更新 run-date 之前最近一个交易日 bench

可选策略（auto-mode=previous_trade）：
- 始终更新 run-date 之前最近一个交易日（适合次日早晨跑昨收）

用法（项目根目录，PYTHONPATH=.）：
  python -u MasterData/data_rq/update_daily_bench.py
  python -u MasterData/data_rq/update_daily_bench.py --run-date 2026-04-20
  python -u MasterData/data_rq/update_daily_bench.py --auto-mode previous_trade
  python -u MasterData/data_rq/update_daily_bench.py --pre-day 2026-04-17
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows 控制台默认 gbk 时，先切 stdout/stderr 到 utf-8，
# 避免 import update_rq_bench 时其 emoji 日志触发编码异常。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from DataBase.db_client import get_client_U
from MasterData.data_rq.update_rq_bench import create_indexes_rq_bench, update_rq_bench


def _norm_day(s: str) -> str:
    s = str(s).strip()
    if "/" in s:
        s = s.replace("/", "-")
    return s[:10]


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


def _is_trade_day(client, day: str) -> bool:
    return (
        client.economic.trade_dates.find_one(
            {"trade_date": _norm_day(day)},
            {"_id": 0, "trade_date": 1},
        )
        is not None
    )


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
    raise RuntimeError(f"无法根据 run-date={run_date} 解析 pre_trade_day（mode={auto_mode}）")


def main() -> None:
    p = argparse.ArgumentParser(description="每日更新 basic_rq.rq_bench（定时任务入口）")
    p.add_argument(
        "--pre-day",
        default=None,
        help="显式指定 pre_trade_day（YYYY-MM-DD）；指定后将忽略 --run-date/--auto-mode",
    )
    p.add_argument(
        "--run-date",
        default=dt.date.today().isoformat(),
        help="任务运行日（用于自动推导 pre_trade_day），默认今天",
    )
    p.add_argument(
        "--auto-mode",
        default="today_if_trade",
        choices=["today_if_trade", "previous_trade"],
        help="自动推导规则：today_if_trade(默认) / previous_trade",
    )
    p.add_argument("--mongo-alias", default="local", help="Mongo 别名，默认 local")
    p.add_argument("--mongo-db", default="basic_rq", help="数据库名，默认 basic_rq")
    p.add_argument("--no-index", action="store_true", help="跳过索引处理")
    p.add_argument("--dry-run", action="store_true", help="只打印将更新的日期，不实际写库")
    p.add_argument(
        "--min-date",
        default=os.environ.get("RQ_BENCH_MIN_DATE", "1990-01-01"),
        help="透传给 update_rq_bench 的最早写入门槛",
    )
    args = p.parse_args()

    client = get_client_U(args.mongo_alias)

    if args.pre_day:
        pre_trade_day = _norm_day(args.pre_day)
        print(f"[manual] pre_trade_day = {pre_trade_day}")
    else:
        pre_trade_day = resolve_pre_trade_day(client, args.run_date, args.auto_mode)
        print(f"[auto] run-date={_norm_day(args.run_date)} mode={args.auto_mode} -> pre_trade_day={pre_trade_day}")

    if args.dry_run:
        print("dry-run: 不执行写库。")
        return

    if not args.no_index:
        try:
            create_indexes_rq_bench(mongo_alias=args.mongo_alias, mongo_db=args.mongo_db)
        except Exception as e:
            print(f"索引处理提示（可忽略）: {e}")

    ok = update_rq_bench(
        pre_trade_day,
        mongo_alias=args.mongo_alias,
        mongo_db=args.mongo_db,
        min_date=str(args.min_date)[:10],
    )
    if ok:
        print(f"✅ 每日 bench 更新完成：{pre_trade_day}")
    else:
        print(f"❌ 每日 bench 更新失败：{pre_trade_day}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
