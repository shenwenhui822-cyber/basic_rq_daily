# -*- coding: utf-8 -*-
"""
基于 basic_rq.rq_base_info 的 (date, code, code_rq)，
用米筐 index_components 生成 basic_rq.rq_base_index 宽基成分标记。

字段：date, code, code_rq, in_SZ50, in_HS300, in_ZZ500, in_ZZ1000, in_ZZ2000
时间范围：2026-04-01 ~ 2026-05-15 全部 A 股交易日（与 rq_base_info 对齐）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__, daily=True)

import argparse

from datetime import date
from time import perf_counter
from typing import Any

import pandas as pd
import rqdatac as rq

from usedbdef import get_client, insert_db_from_df

_RQ_INITIALIZED = False


def _init_rq() -> None:
    global _RQ_INITIALIZED
    if _RQ_INITIALIZED:
        return
    rq.init("18616633529", "wuzhi2020")
    _RQ_INITIALIZED = True


def _log(msg: str, *, flush: bool = True) -> None:
    print(msg, flush=flush)


INDEX_DEFS: list[tuple[str, str]] = [
    ("000016.XSHG", "in_SZ50"),
    ("000300.XSHG", "in_HS300"),
    ("000905.XSHG", "in_ZZ500"),
    ("000852.XSHG", "in_ZZ1000"),
]

CSI2000_CANDIDATES: tuple[str, ...] = ("932000.INDX", "932000.XSHG", "932000.CSI")
CSI2000_MIN_COMPONENTS: int = 500
FLAG_COLS = ["in_SZ50", "in_HS300", "in_ZZ500", "in_ZZ1000", "in_ZZ2000"]

# 一次性历史补齐区间（日更请用 update_rq_in_index.py，默认 T-1）
RANGE_START = "2026-01-01"
RANGE_END = "2026-03-31"


def to_rq_date(date_str: str) -> str:
    """rq_base_info 日期 -> 米筐 API 日期 YYYY-MM-DD"""
    return str(date_str).strip().replace("/", "-")

#index_components接口能够直接获取指数中的成分股
def resolve_csi2000_obid(as_of: str) -> str:
    last_err: Exception | None = None
    for oid in CSI2000_CANDIDATES:
        try:
            comp = rq.index_components(oid, date=as_of)
            if len(comp or []) >= CSI2000_MIN_COMPONENTS:
                return oid
        except Exception as e:
            last_err = e
    raise RuntimeError(f"无法解析中证2000指数代码，最后错误：{last_err}")


def fetch_index_sets(as_of: str) -> dict[str, set[str]]:
    """as_of: YYYY-MM-DD"""
    out: dict[str, set[str]] = {}
    for oid, col in INDEX_DEFS:
        comp = rq.index_components(oid, date=as_of)
        out[col] = set(comp or [])
    c2k = resolve_csi2000_obid(as_of)
    comp2k = rq.index_components(c2k, date=as_of)
    out["in_ZZ2000"] = set(comp2k or [])
    return out


def get_trading_dates_in_range(start: str = RANGE_START, end: str = RANGE_END) -> list[str]:
    days = rq.get_trading_dates(start, end, market="cn")
    return [d.strftime("%Y-%m-%d") if isinstance(d, date) else str(d)[:10] for d in days]


def resolve_info_date(info_table: Any, trade_date: str) -> str | None:
    """在 rq_base_info 中解析实际存储的 date 字段（横线/斜杠）"""
    for candidate in (trade_date, trade_date.replace("-", "/")):
        if info_table.count_documents({"date": candidate}, limit=1):
            return candidate
    return None


def list_dates_for_build(info_table: Any, start: str = RANGE_START, end: str = RANGE_END) -> list[str]:
    """按交易日历取日，并映射为 rq_base_info 中的 date 字符串"""
    out: list[str] = []
    missing: list[str] = []
    for trade_date in get_trading_dates_in_range(start, end):
        stored = resolve_info_date(info_table, trade_date)
        if stored:
            out.append(stored)
        else:
            missing.append(trade_date)
    if missing:
        raise RuntimeError(f"rq_base_info 缺少以下交易日数据：{missing}")
    return out


def delete_index_in_range(index_table: Any, start: str = RANGE_START, end: str = RANGE_END) -> int:
    """删除区间内 rq_base_index（兼容横线/斜杠 date）"""
    trade_dates = get_trading_dates_in_range(start, end)
    variants = {d for td in trade_dates for d in (td, td.replace("-", "/"))}
    deleted = index_table.delete_many({"date": {"$in": list(variants)}}).deleted_count
    return deleted


def build_index_frame(info_table: Any, date_str: str, index_sets: dict[str, set[str]]) -> pd.DataFrame:
    rows = list(
        info_table.find(
            {"date": date_str},
            {"_id": 0, "date": 1, "code": 1, "code_rq": 1},
        )
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for col in FLAG_COLS:
        df[col] = df["code_rq"].isin(index_sets[col]).astype("int32")

    return df[["date", "code", "code_rq"] + FLAG_COLS]


def build_rq_base_index(
    client_from: str = "wonderwz27018_rw",
    replace_existing: bool = True,
    start: str = RANGE_START,
    end: str = RANGE_END,
    *,
    retain_combined_df: bool = False,
) -> pd.DataFrame:
    """
    按交易日逐一：米筐摘录当日成分 -> 读取 rq_base_info 当日记录 -> 写 rq_base_index。

    :param retain_combined_df: True 时在内存中保留并返回全区间合并 DataFrame（大区间占用高）；默认仅累计行数日志。
    """
    _init_rq()
    client = get_client(client_from)
    info_table = client["basic_rq"]["rq_base_info"]
    index_table = client["basic_rq"]["rq_base_index"]

    dates = list_dates_for_build(info_table, start, end)
    trade_days = get_trading_dates_in_range(start, end)
    _log(f"区间 {start} ~ {end}：交易日 {len(trade_days)} 天，待写入 {len(dates)} 天（按日摘录+按日入库）")
    _log(f"首末日：{dates[0]} ~ {dates[-1]}")

    if replace_existing:
        deleted = delete_index_in_range(index_table, start, end)
        _log(f"已清理 rq_base_index 区间内旧数据：{deleted} 条")

    all_parts: list[pd.DataFrame] = []
    total_rows = 0
    for idx, date_str in enumerate(dates, start=1):
        rq_date = to_rq_date(date_str)
        t0 = perf_counter()
        _log("")
        _log(f"=== [{idx}/{len(dates)}] {date_str} (rq={rq_date}) ===")

        _log("  [1/3] 分日摘录：拉取当日各指数成分股 …")
        t_rq = perf_counter()
        index_sets = fetch_index_sets(rq_date)
        rq_s = perf_counter() - t_rq
        for col in FLAG_COLS:
            _log(f"        {col}: {len(index_sets[col])} 只")
        _log(f"        （米筐 API 耗时 {rq_s:.2f}s）")

        _log("  [2/3] 读 Mongo：rq_base_info 当日行 …")
        t_m = perf_counter()
        df_day = build_index_frame(info_table, date_str, index_sets)
        read_s = perf_counter() - t_m
        if df_day.empty:
            _log(f"        跳过：无记录（读库耗时 {read_s:.2f}s）；本日总耗时 {perf_counter() - t0:.2f}s")
            continue
        _log(f"        {len(df_day)} 行（读库/拼表耗时 {read_s:.2f}s）")

        _log(f"  [3/3] 写入 rq_base_index …")
        t_w = perf_counter()
        insert_db_from_df(table=index_table, df=df_day)
        w_s = perf_counter() - t_w
        _log(f"        入库 {len(df_day)} 条（写库耗时 {w_s:.2f}s）；本日总耗时 {perf_counter() - t0:.2f}s")

        total_rows += len(df_day)
        if retain_combined_df:
            all_parts.append(df_day)

    if total_rows == 0 and not retain_combined_df:
        _log("\n完成：无任何行写入 basic_rq.rq_base_index")
        return pd.DataFrame()

    if retain_combined_df and all_parts:
        df_all = pd.concat(all_parts, ignore_index=True)
        _log(f"\n完成：合计 {len(df_all)} 条 -> basic_rq.rq_base_index（已合并返回 DataFrame）")
        return df_all

    _log(f"\n完成：合计写入 {total_rows} 条 -> basic_rq.rq_base_index")
    return pd.DataFrame()


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="历史补齐 rq_base_index：宽基成分 0/1（须区间内 rq_base_info 已存在）",
    )
    p.add_argument(
        "--start",
        default=RANGE_START,
        help="区间起（含）：YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD",
    )
    p.add_argument("--end", default=RANGE_END, help="区间止（含），格式同 --start")
    p.add_argument(
        "--mongo-alias",
        default="wonderwz27018_rw",
        help="Mongo 连接别名",
    )
    p.add_argument(
        "--keep-existing",
        action="store_true",
        help="不先删除区间内旧 rq_base_index 记录",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    build_rq_base_index(
        client_from=args.mongo_alias,
        replace_existing=not args.keep_existing,
        start=args.start,
        end=args.end,
    )
