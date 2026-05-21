# -*- coding: utf-8 -*-
"""
定时任务用：按「单个 A 股交易日」刷新 basic_rq.rq_base_index。

逻辑与 get_rq_in_index.py 一致，默认更新 T-1（上一交易日），
且要求当日已在 rq_base_info 中存在（请先跑完当日 rq 基础信息更新）。

用法：
  python update_rq_in_index.py
  python update_rq_in_index.py --date 2026-05-15
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from time import perf_counter

import rqdatac as rq

from trade_date_utils import parse_explicit_date_arg, previous_trade_date

from get_rq_in_index import (
    build_index_frame,
    delete_index_in_range,
    fetch_index_sets,
    resolve_info_date,
    to_rq_date,
)
from update_rqbaseInfo import get_client, insert_db_from_df


def _log(msg: str) -> None:
    print(msg, flush=True)


def _norm_input_date(s: str) -> str:
    return str(s).strip().replace("/", "-")


def last_cn_trading_day_on_or_before(day: date) -> str:
    """<= day 的最近一个 cn 交易日，YYYY-MM-DD。"""
    end = day.strftime("%Y-%m-%d")
    days = rq.get_trading_dates("1990-01-01", end, market="cn")
    if not days:
        raise RuntimeError(f"无法解析 <= {end} 的 cn 交易日")
    last = days[-1]
    return last.strftime("%Y-%m-%d") if isinstance(last, date) else str(last)[:10]


def update_rq_base_index_one_day(
    trade_date: str,
    *,
    client_from: str = "wonderwz27018_rw",
    replace_existing: bool = True,
) -> int:
    """
    刷新 rq_base_index 中某一交易日的成分标记。

    :param trade_date: 日历上的交易日 YYYY-MM-DD（或非交易日的日期时将报错；请传入真实交易日）
    :return: 写入行数
    """
    td = _norm_input_date(trade_date)
    client = get_client(client_from)
    info_table = client["basic_rq"]["rq_base_info"]
    index_table = client["basic_rq"]["rq_base_index"]

    stored = resolve_info_date(info_table, td)
    if not stored:
        raise RuntimeError(f"rq_base_info 中无 {td}（含斜杠格式）数据，请先更新 rq_base_info")

    t0 = perf_counter()
    _log(f"=== update rq_base_index：目标交易日 {stored}（rq API 用 {to_rq_date(stored)}）===")

    if replace_existing:
        deleted = delete_index_in_range(index_table, td, td)
        _log(f"已删除当日 rq_base_index 旧数据：{deleted} 条")

    _log("  [1/3] 拉取指数成分 …")
    t_rq = perf_counter()
    index_sets = fetch_index_sets(to_rq_date(stored))
    _log(f"        米筐耗时 {perf_counter() - t_rq:.2f}s")

    _log("  [2/3] 读 rq_base_info 当日 …")
    t_m = perf_counter()
    df_day = build_index_frame(info_table, stored, index_sets)
    _log(f"        {len(df_day)} 行，耗时 {perf_counter() - t_m:.2f}s")
    if df_day.empty:
        raise RuntimeError(f"组装结果为空（date={stored}）")

    _log("  [3/3] 写入 rq_base_index …")
    t_w = perf_counter()
    insert_db_from_df(table=index_table, df=df_day)
    _log(f"        入库 {len(df_day)} 条，写库耗时 {perf_counter() - t_w:.2f}s")

    _log(f"完成，总耗时 {perf_counter() - t0:.2f}s")
    return len(df_day)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="每日更新 rq_base_index（单日）")
    p.add_argument(
        "--date",
        dest="trade_date",
        default=None,
        help="交易日 YYYY-MM-DD；省略则取 T-1（上一交易日）",
    )
    p.add_argument("--client-from", default="wonderwz27018_rw", help="Mongo 配置键，默认 wonderwz27018_rw")
    p.add_argument(
        "--no-delete",
        action="store_true",
        help="写入前不删除当日已有 rq_base_index（一般不建议）",
    )
    args = p.parse_args(argv)

    try:
        if args.trade_date:
            td = _norm_input_date(args.trade_date)
        else:
            csv_path = Path(__file__).resolve().parent / "trade_dates_all.csv"
            td = previous_trade_date(csv_path, fmt="%Y-%m-%d")
            _log(f"未指定 --date，使用 T-1 交易日：{td}")

        n = update_rq_base_index_one_day(
            td,
            client_from=args.client_from,
            replace_existing=not args.no_delete,
        )
        _log(f"退出码 0，写入 {n} 条")
        return 0
    except Exception as e:
        _log(f"失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
