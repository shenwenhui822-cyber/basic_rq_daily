"""
按交易日拉取全市场基本财务字段（口径对齐 test_rq_basic_financial_simple.py）。

字段：
- mkt_cap_ard         <- market_cap_3
- total_shares        <- get_shares().total
- free_float_shares   <- get_shares().free_circulation
- or_ttm / gr_ttm     <- revenue_ttm_0
- netprofit_ttm       <- net_profit_parent_company_ttm_0
- operatecashflow_ttm <- cash_flow_from_operating_activities_ttm_0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__)
import time
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd
import rqdatac as rq

from rq_basic_financial_format import prepare_financial_df_for_mongo
from trade_date_utils import parse_explicit_date_arg, parse_start_end_range
from usedbdef import get_client, insert_db_from_df

DATE_FMT_DB = "%Y-%m-%d"
QUOTA_STOP_FRACTION = 0.6
VERBOSE = False

FACTOR_MAP = {
    "mkt_cap_ard": "market_cap_3",
    "or_ttm": "revenue_ttm_0",
    "netprofit_ttm": "net_profit_parent_company_ttm_0",
    "operatecashflow_ttm": "cash_flow_from_operating_activities_ttm_0",
}


_RQ_INITIALIZED = False


def _init_rq() -> None:
    global _RQ_INITIALIZED
    if _RQ_INITIALIZED:
        return
    try:
        rq.init("18616633529", "wuzhi2020")
        print("RQData 连接成功")
        _RQ_INITIALIZED = True
    except Exception as exc:
        print(f"RQData 连接失败：{exc}")
        raise


def _parse_input_date(s: str) -> date:
    return pd.Timestamp(s).date()


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def log_rq_quota_status(label: str = "") -> None:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    remaining = q.get("remaining_days")
    lic = q.get("license_type")
    if not VERBOSE:
        return
    prefix = f"[{label}] " if label else ""
    if limit <= 0:
        print(
            f"{prefix}流量探查: bytes_used={used}, bytes_limit=0(不限), "
            f"remaining_days={remaining}, license_type={lic}"
        )
        return
    pct = 100.0 * used / limit
    print(
        f"{prefix}流量探查: bytes_used={used}/{limit} ({pct:.2f}%), "
        f"remaining_days={remaining}, license_type={lic}"
    )


def check_rq_quota_or_exit(*, fraction: float = QUOTA_STOP_FRACTION, label: str = "") -> None:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    prefix = f"[{label}] " if label else ""
    if limit <= 0:
        return
    ratio = used / limit
    if ratio >= fraction:
        print(
            f"{prefix}❌ 当日流量占比 {ratio * 100:.2f}% ≥ 阈值 {fraction * 100:.0f}%，停止执行。",
            file=sys.stderr,
        )
        sys.exit(1)


def iter_year_segments(start_s: str, end_s: str) -> Iterable[tuple[str, str, int]]:
    d0 = _parse_input_date(start_s)
    d1 = _parse_input_date(end_s)
    if d0 > d1:
        raise ValueError(f"start 不能晚于 end：{d0} > {d1}")
    for year in range(d0.year, d1.year + 1):
        seg_start = max(d0, date(year, 1, 1))
        seg_end = min(d1, date(year, 12, 31))
        if seg_start <= seg_end:
            yield seg_start.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d"), year


def _extract_factor_series(df: pd.DataFrame, factor_name: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype="float64")
    if factor_name not in df.columns:
        return pd.Series(dtype="float64")
    s = df[factor_name]
    if isinstance(s.index, pd.MultiIndex) and "order_book_id" in s.index.names:
        s = s.reset_index().set_index("order_book_id")[factor_name]
    return s


def _fetch_factors_for_codes(codes: list[str], trade_date: str) -> pd.DataFrame:
    out = pd.DataFrame(index=pd.Index(codes, name="order_book_id"))
    for out_col, factor_name in FACTOR_MAP.items():
        try:
            fac_df = rq.get_factor(codes, factor_name, trade_date, trade_date, expect_df=True)
            out[out_col] = _extract_factor_series(fac_df, factor_name)
        except Exception as exc:
            print(f"  ⚠️ 因子 {factor_name} 拉取失败：{exc}")
            out[out_col] = np.nan
    # gr_ttm 与 or_ttm 同口径
    out["gr_ttm"] = out["or_ttm"]
    return out


def _fetch_shares_for_codes(codes: list[str], trade_date: str) -> pd.DataFrame:
    out = pd.DataFrame(index=pd.Index(codes, name="order_book_id"))
    try:
        sh = rq.get_shares(codes, start_date=trade_date, end_date=trade_date)
        if sh is not None and not sh.empty:
            tmp = sh.reset_index().set_index("order_book_id")
            out["total_shares"] = tmp["total"] if "total" in tmp.columns else np.nan
            if "free_circulation" in tmp.columns:
                out["free_float_shares"] = tmp["free_circulation"]
            elif "circulation_a" in tmp.columns:
                out["free_float_shares"] = tmp["circulation_a"]
            else:
                out["free_float_shares"] = np.nan
        else:
            out["total_shares"] = np.nan
            out["free_float_shares"] = np.nan
    except Exception as exc:
        print(f"  ⚠️ get_shares 拉取失败：{exc}")
        out["total_shares"] = np.nan
        out["free_float_shares"] = np.nan
    return out


def fetch_basic_financial_one_day(
    trade_date: str, *, max_ids_per_request: int = 1500
) -> pd.DataFrame:
    df_all = rq.all_instruments(type="CS", date=trade_date, market="cn")
    if df_all.empty:
        return pd.DataFrame()
    stock_codes = df_all["order_book_id"].dropna().astype(str).tolist()

    parts: list[pd.DataFrame] = []
    for i in range(0, len(stock_codes), max_ids_per_request):
        chunk = stock_codes[i : i + max_ids_per_request]
        fac = _fetch_factors_for_codes(chunk, trade_date)
        shares = _fetch_shares_for_codes(chunk, trade_date)
        merged = fac.join(shares, how="outer")
        parts.append(merged)

    out = pd.concat(parts, axis=0)
    out = out.reset_index().rename(columns={"order_book_id": "code_rq"})
    out["date"] = pd.Timestamp(trade_date).strftime(DATE_FMT_DB)
    out["code"] = out["code_rq"].map(_rq_code_to_display)
    cols = [
        "date",
        "code",
        "code_rq",
        "mkt_cap_ard",
        "total_shares",
        "free_float_shares",
        "or_ttm",
        "gr_ttm",
        "netprofit_ttm",
        "operatecashflow_ttm",
    ]
    out = out[cols].sort_values(["date", "code"]).reset_index(drop=True)
    return out


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return prepare_financial_df_for_mongo(df)


def save_day_to_mongo(
    df_day: pd.DataFrame,
    *,
    table,
    delete_before_insert: bool = True,
) -> None:
    if df_day is None or df_day.empty:
        return
    date_for_db = str(df_day["date"].iloc[0])
    if delete_before_insert:
        variants = [date_for_db]
        if "/" in date_for_db:
            variants.append(date_for_db.replace("/", "-"))
        dr = table.delete_many({"date": {"$in": variants}})
        if VERBOSE:
            print(f"  已删除旧记录：{dr.deleted_count}（date={variants}）")
    insert_db_from_df(table, _df_nan_to_none(df_day))


def run_pipeline_for_range(
    start_s: str,
    end_s: str,
    *,
    no_mongo: bool,
    mongo_alias: str,
    mongo_db: str,
    mongo_collection: str,
    segment_label: str,
) -> None:
    banner = segment_label or f"{start_s} ~ {end_s}"
    print(f"\n{banner} | 区间: {start_s} ~ {end_s}")
    check_rq_quota_or_exit(label=f"{banner} 开始前")

    d0 = _parse_input_date(start_s)
    d1 = _parse_input_date(end_s)
    trading_days = rq.get_trading_dates(start_date=d0, end_date=d1, market="cn")
    if not trading_days:
        print("无交易日，跳过。")
        return

    table = None
    if not no_mongo:
        client = get_client(mongo_alias)
        table = client[mongo_db][mongo_collection]
        if VERBOSE:
            print(f"Mongo 目标：{mongo_db}.{mongo_collection}（{mongo_alias}）")

    for idx, d in enumerate(trading_days, start=1):
        trade_date = d.strftime("%Y-%m-%d")
        print(f"{trade_date} ({idx}/{len(trading_days)})")
        check_rq_quota_or_exit(label=f"{trade_date} 拉取前")
        df_day = fetch_basic_financial_one_day(trade_date)
        print(f"  rows={len(df_day)}")
        if no_mongo:
            if VERBOSE:
                print("  已跳过 Mongo 写入（--no-mongo）")
            continue
        save_day_to_mongo(df_day, table=table, delete_before_insert=True)
        print("  done")

    log_rq_quota_status(f"{banner} 结束后")


def main(
    *,
    start_date: str,
    end_date: str,
    single_day: str | None = None,
    split_by_year: bool = True,
    mongo_db: str = "basic_rq",
    mongo_collection: str = "rq_basic_financial",
    no_mongo: bool = False,
    mongo_alias: str = "wonderwz27018_rw",
) -> None:
    _init_rq()
    if single_day:
        start_s = end_s = parse_explicit_date_arg(single_day, fmt=DATE_FMT_DB)
    else:
        start_s, end_s = parse_start_end_range(start_date, end_date, fmt=DATE_FMT_DB)

    print(f"总区间: {start_s} ~ {end_s}（含）")
    log_rq_quota_status("任务启动时")

    if single_day:
        run_pipeline_for_range(
            start_s,
            end_s,
            no_mongo=no_mongo,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            mongo_collection=mongo_collection,
            segment_label="单日",
        )
        return

    if not split_by_year:
        run_pipeline_for_range(
            start_s,
            end_s,
            no_mongo=no_mongo,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            mongo_collection=mongo_collection,
            segment_label="整段（未按年拆分）",
        )
        return

    segments = list(iter_year_segments(start_s, end_s))
    print(f"将按自然年分段执行，共 {len(segments)} 段。")
    for seg_start, seg_end, year in segments:
        run_pipeline_for_range(
            seg_start,
            seg_end,
            no_mongo=no_mongo,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            mongo_collection=mongo_collection,
            segment_label=f"{year} 年",
        )

    print("\n全部年段执行完毕。")
    log_rq_quota_status("全部任务结束后")


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="历史补齐 rq_basic_financial：按交易日区间拉取并落库",
    )
    p.add_argument(
        "--start",
        default="2026-05-12",
        help="区间起（含）：YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD",
    )
    p.add_argument("--end", default="2026-05-12", help="区间止（含），格式同 --start")
    p.add_argument(
        "--date",
        default=None,
        help="单日（含）；指定后忽略 --start / --end",
    )
    p.add_argument("--no-mongo", action="store_true", help="仅拉取，不写 Mongo")
    p.add_argument(
        "--mongo-alias",
        default="wonderwz27018_rw",
        help="get_client 别名，默认 wonderwz27018_rw",
    )
    p.add_argument("--no-split-year", action="store_true", help="不按年分段（谨慎使用）")
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    main(
        start_date=args.start,
        end_date=args.end,
        single_day=args.date,
        split_by_year=not args.no_split_year,
        no_mongo=args.no_mongo,
        mongo_alias=args.mongo_alias,
    )
