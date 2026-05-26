"""
历史补齐 basic_rq.rq_daily_price_none（全市场不复权日线）。

按区间从 RQ 拉基础表 lookup → 分批 get_price → 落库 Mongo。
长区间默认按自然年分段以降低流量压力。

用法（工作目录为 basic_rq_daily 根目录）::

    python rq_history_backfill/rq_getRangeDailyPriceLongrun.py --start 2026-03-16 --end 2026-03-18

入库 date 格式：YYYY-MM-DD（如 2022-06-22）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__)

import pandas as pd
import rqdatac as rq

from rq_getRangeDailyPrice import (
    check_rq_quota_or_exit,
    _df_nan_to_none,
    _is_rq_column_multiindex_wide,
    _rq_code_to_display,
    fetch_range_base_info,
    to_price_lookup_df,
)
from trade_date_utils import parse_explicit_date_arg, parse_start_end_range
from usedbdef import get_client, insert_db_from_df

DATE_FMT_DB = "%Y-%m-%d"

DAILY_PRICE_FIELDS = [
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "volume",
    "total_turnover",
    "limit_up",
    "limit_down",
]

EXPECTED_STOCKS_PER_DAY = 5193
_STOCK_COUNT_TOLERANCE = 200

try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def log_rq_quota_status(label: str = "") -> None:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    remaining = q.get("remaining_days")
    lic = q.get("license_type")
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


def iter_year_segments(start_s: str, end_s: str):
    d0 = pd.Timestamp(start_s).date()
    d1 = pd.Timestamp(end_s).date()
    if d0 > d1:
        raise ValueError(f"start 不能晚于 end：{d0} > {d1}")
    for year in range(d0.year, d1.year + 1):
        seg_start = max(d0, date(year, 1, 1))
        seg_end = min(d1, date(year, 12, 31))
        if seg_start > seg_end:
            continue
        yield (
            seg_start.strftime(DATE_FMT_DB),
            seg_end.strftime(DATE_FMT_DB),
            year,
        )


def _get_daily_price_wide(
    order_book_ids: list[str],
    start_date: str,
    end_date: str,
    *,
    fields: list[str] | None = None,
) -> Any:
    fq = fields or list(DAILY_PRICE_FIELDS)
    return rq.get_price(
        order_book_ids,
        start_date=start_date,
        end_date=end_date,
        frequency="1d",
        fields=fq,
        adjust_type="none",
        expect_df=True,
    )


def _normalize_price_wide(df: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
        out = out.sort_index(axis=1)
    elif out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
    return out


def _norm_lookup_dates(df_keys: pd.DataFrame) -> pd.DataFrame:
    if df_keys.empty:
        return df_keys
    out = df_keys.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime(DATE_FMT_DB)
    return out


def run_pipeline_for_range(
    start_s: str,
    end_s: str,
    *,
    skip_price: bool,
    no_mongo: bool,
    mongo_alias: str,
    mongo_db: str,
    mongo_collection: str,
    segment_label: str,
) -> None:
    banner = segment_label or f"{start_s} ~ {end_s}"
    print(f"\n{'='*60}\n{banner}\n区间: {start_s} ~ {end_s}\n{'='*60}")

    check_rq_quota_or_exit(label=f"{segment_label or '本段'}开始前")

    df = fetch_range_base_info(start_s, end_s)
    if df.empty:
        print(f"  [{segment_label}] 无基础数据，跳过本段。")
        return

    print("\n完整基础表预览（前 5 行）:")
    print(df.head())
    df_keys = _norm_lookup_dates(to_price_lookup_df(df))
    print("\n供 get_price 使用：仅 date + code_rq（去重后，前 10 行）:")
    print(df_keys.head(10))
    print(f"date+code_rq 行数: {len(df_keys)}")

    if skip_price:
        print("\n已跳过日线下载（--skip-price）。")
        return

    check_rq_quota_or_exit(label=f"{segment_label or '本段'}拉日线前")

    print("\n=== 按日拉取日线宽表（rq.get_price 1d, adjust_type=none）===")
    by_day = fetch_daily_prices_by_lookup(df_keys)
    print(f"\n✅ 日线宽表按日字典共 {len(by_day)} 个交易日；键为日期字符串，值为宽表 DataFrame。")

    print("\n=== 核对每日约 5193 行：lookup 标的数 vs 长表行数 ===")
    compare_lookup_to_long(df_keys, by_day)

    if no_mongo:
        print("\n已跳过 MongoDB（--no-mongo）。")
        return

    save_rq_daily_prices_to_mongo(
        by_day,
        mongo_alias=mongo_alias,
        mongo_db=mongo_db,
        mongo_collection=mongo_collection,
    )


def fetch_daily_prices_by_lookup(
    df_keys: pd.DataFrame,
    *,
    fields: list[str] | None = None,
    max_ids_per_request: int = 2000,
) -> dict[str, pd.DataFrame]:
    fields = fields or DAILY_PRICE_FIELDS
    if df_keys.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}
    for trade_date, g in df_keys.groupby("date", sort=True):
        ids = g["code_rq"].tolist()
        if not ids:
            continue
        rq_day = pd.Timestamp(trade_date).strftime("%Y/%m/%d")
        parts: list[pd.DataFrame] = []
        for i in range(0, len(ids), max_ids_per_request):
            chunk = ids[i : i + max_ids_per_request]
            df_p = _get_daily_price_wide(chunk, rq_day, rq_day, fields=fields)
            if df_p is not None and not df_p.empty:
                parts.append(df_p)

        key = pd.Timestamp(trade_date).strftime(DATE_FMT_DB)
        if not parts:
            result[key] = pd.DataFrame()
        elif len(parts) == 1:
            result[key] = parts[0]
        elif _is_rq_column_multiindex_wide(parts[0]):
            merged = pd.concat(parts, axis=1)
            result[key] = _normalize_price_wide(merged, fields)
        else:
            merged_long = pd.concat(parts, axis=0, sort=False)
            if "order_book_id" in merged_long.columns:
                merged_long = merged_long.drop_duplicates(
                    subset=["order_book_id"], keep="last"
                )
            elif merged_long.index.name == "order_book_id":
                merged_long = merged_long[~merged_long.index.duplicated(keep="last")]
            result[key] = merged_long

        nrows = len(result[key])
        print(
            f"  日线宽表 {key}: 标的 {len(ids)} 只, 宽表行数 {nrows}, 列数 {result[key].shape[1]}"
        )
    return result


def wide_rq_daily_to_long_df(
    df_wide: pd.DataFrame,
    fields: list[str] | None = None,
    trade_date_hint: str | None = None,
) -> pd.DataFrame:
    fields = fields or DAILY_PRICE_FIELDS
    field_set = set(fields)
    if df_wide is None or df_wide.empty:
        return pd.DataFrame()

    df = df_wide.copy()

    if isinstance(df.columns, pd.MultiIndex) and df.columns.nlevels == 2:
        top = df.columns[0][0]
        if top in field_set:
            df = df.swaplevel(axis=1)
            df = df.sort_index(axis=1, level=0)
        stacked = df.stack(level=0)
        out = stacked.reset_index()
        if out.shape[1] < 3:
            return pd.DataFrame()
        c0, c1 = out.columns[0], out.columns[1]
        out = out.rename(columns={c0: "datetime", c1: "code_rq"})
        out["date"] = pd.Timestamp(trade_date_hint).strftime(DATE_FMT_DB) if trade_date_hint else pd.to_datetime(out["datetime"]).dt.strftime(DATE_FMT_DB)
        out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)
        out = out.drop(columns=["datetime"])
        price_cols = [c for c in fields if c in out.columns]
        return out[["date", "code", "code_rq"] + price_cols]

    out = out.reset_index()
    id_col = None
    if "order_book_id" in out.columns:
        id_col = "order_book_id"
    else:
        for c in out.columns:
            if c in field_set:
                continue
            sample = out[c].dropna().head(30)
            if sample.empty:
                continue
            s = sample.astype(str)
            if s.str.contains(r"\.XSHG|\.XSHE|\.XBSE", regex=True, na=False).any():
                id_col = c
                break
    if id_col is None:
        for c in out.columns:
            if c in field_set or not str(c).startswith("level_"):
                continue
            sample = out[c].dropna().head(30)
            if sample.empty:
                continue
            if (
                sample.astype(str)
                .str.contains(r"\.XSHG|\.XSHE|\.XBSE", regex=True, na=False)
                .any()
            ):
                id_col = c
                break
    if id_col is None:
        print(f"  ⚠️ 长表无法识别合约代码列，当前列: {list(out.columns)[:20]}...")
        return pd.DataFrame()

    out = out.rename(columns={id_col: "code_rq"})

    if trade_date_hint is not None:
        out["date"] = pd.Timestamp(trade_date_hint).strftime(DATE_FMT_DB)
    else:
        dt_col = None
        for c in ("datetime", "date", "trading_date"):
            if c in out.columns:
                dt_col = c
                break
        if dt_col is None:
            print("  ⚠️ 长表缺少交易日列且未传入 trade_date_hint")
            return pd.DataFrame()
        out["date"] = pd.to_datetime(out[dt_col], errors="coerce").dt.strftime(DATE_FMT_DB)
        if dt_col != "date":
            out = out.drop(columns=[dt_col], errors="ignore")

    for drop_c in ("datetime", "trading_date"):
        if drop_c in out.columns:
            out = out.drop(columns=[drop_c], errors="ignore")

    out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)
    price_cols = [c for c in fields if c in out.columns]
    return out[["date", "code", "code_rq"] + price_cols]


def _validate_daily_row_count(n: int, trade_label: str) -> None:
    if n == 0:
        print(f"  ⚠️ [{trade_label}] 长表行数为 0")
        return
    lo = EXPECTED_STOCKS_PER_DAY - _STOCK_COUNT_TOLERANCE
    hi = EXPECTED_STOCKS_PER_DAY + _STOCK_COUNT_TOLERANCE
    if not (lo <= n <= hi):
        print(
            f"  ⚠️ [{trade_label}] 长表行数 {n}，与预期约 {EXPECTED_STOCKS_PER_DAY} 差异较大（允许 ±{_STOCK_COUNT_TOLERANCE}）"
        )
    else:
        print(f"  ✓ [{trade_label}] 长表行数 {n}（约全市场 {EXPECTED_STOCKS_PER_DAY}）")


def compare_lookup_to_long(df_keys: pd.DataFrame, by_day: dict[str, pd.DataFrame]) -> None:
    if df_keys.empty or not by_day:
        return
    for trade_date, g in df_keys.groupby("date", sort=False):
        key = pd.Timestamp(trade_date).strftime(DATE_FMT_DB)
        if key not in by_day:
            print(f"  ⚠️ lookup 有日期 {key}，但无对应宽表")
            continue
        n_lk = len(g)
        long_df = wide_rq_daily_to_long_df(by_day[key], trade_date_hint=key)
        n_long = len(long_df)
        if n_lk != n_long:
            print(
                f"  ⚠️ {key}: 基础表当日标的 {n_lk} 与日线长表行数 {n_long} 不一致，请检查缺失标的"
            )
        else:
            print(f"  ✓ {key}: lookup={n_lk} 行 = 长表={n_long} 行")


def save_rq_daily_prices_to_mongo(
    by_day: dict[str, pd.DataFrame],
    *,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    mongo_collection: str = "rq_daily_price_none",
    fields: list[str] | None = None,
    delete_before_insert: bool = True,
) -> None:
    fields = fields or DAILY_PRICE_FIELDS
    if not by_day:
        print("无日线数据，跳过 MongoDB 写入。")
        return

    client = get_client(mongo_alias)
    table = client[mongo_db][mongo_collection]
    print(f"\n=== 写入 MongoDB {mongo_db}.{mongo_collection}（{mongo_alias}）===")

    for trade_key, df_wide in by_day.items():
        key = pd.Timestamp(trade_key).strftime(DATE_FMT_DB)
        long_df = wide_rq_daily_to_long_df(
            df_wide, fields=fields, trade_date_hint=key
        )
        if long_df.empty:
            print(f"  跳过空长表：{key}")
            continue

        _validate_daily_row_count(len(long_df), key)

        date_for_db = long_df["date"].iloc[0]
        long_df = _df_nan_to_none(long_df)

        if delete_before_insert:
            date_variants = [date_for_db, date_for_db.replace("-", "/")]
            dr = table.delete_many({"date": {"$in": date_variants}})
            print(f"  已删除当日旧记录：{dr.deleted_count} 条")

        insert_db_from_df(table, long_df.sort_values(by=["date", "code"]))
        print(f"  ✅ 已插入 {len(long_df)} 条（date={date_for_db}）")


def run_longrange_backfill(
    *,
    start_date: str,
    end_date: str,
    single_day: str | None = None,
    split_by_year: bool = True,
    skip_price: bool = False,
    no_mongo: bool = False,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    mongo_collection: str = "rq_daily_price_none",
) -> None:
    if single_day is not None and str(single_day).strip():
        start_s = end_s = pd.Timestamp(single_day).strftime(DATE_FMT_DB)
    else:
        start_s, end_s = parse_start_end_range(start_date, end_date, fmt=DATE_FMT_DB)

    print(f"总区间: {start_s} ~ {end_s}（含）")
    log_rq_quota_status("任务启动时")

    if single_day is not None and str(single_day).strip():
        run_pipeline_for_range(
            start_s,
            end_s,
            skip_price=skip_price,
            no_mongo=no_mongo,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            mongo_collection=mongo_collection,
            segment_label="单日",
        )
        log_rq_quota_status("单日任务结束后")
        return

    if not split_by_year:
        run_pipeline_for_range(
            start_s,
            end_s,
            skip_price=skip_price,
            no_mongo=no_mongo,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            mongo_collection=mongo_collection,
            segment_label="整段（未按年拆分）",
        )
        log_rq_quota_status("整段任务结束后")
        return

    segments = list(iter_year_segments(start_s, end_s))
    print(f"\n将按自然年分段执行，共 {len(segments)} 个年段（每年段内仍会逐交易日拉数）。")

    for seg_start, seg_end, year in segments:
        run_pipeline_for_range(
            seg_start,
            seg_end,
            skip_price=skip_price,
            no_mongo=no_mongo,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            mongo_collection=mongo_collection,
            segment_label=f"{year} 年",
        )
        log_rq_quota_status(f"{year} 年段结束后")

    print("\n✅ 全部年段执行完毕。")
    log_rq_quota_status("全部任务结束后")


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="历史补齐 rq_daily_price_none：按区间拉取全市场不复权日线并落库",
    )
    p.add_argument(
        "--start",
        default=None,
        help="区间起（含）：YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD",
    )
    p.add_argument(
        "--end",
        default=None,
        help="区间止（含），格式同 --start",
    )
    p.add_argument(
        "--date",
        default=None,
        help="单日补齐（设置后忽略 --start/--end）",
    )
    p.add_argument("--mongo-alias", default="wonderwz27018_rw", help="Mongo 连接别名")
    p.add_argument("--skip-price", action="store_true", help="只拉基础 lookup，不拉日线、不写库")
    p.add_argument("--no-mongo", action="store_true", help="拉日线但不写入 MongoDB")
    p.add_argument(
        "--no-split-year",
        action="store_true",
        help="不按年分段，整段一次跑完（多年区间流量风险大）",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    if args.date:
        single = parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
        start_s = end_s = single
    elif args.start and args.end:
        single = None
        start_s, end_s = parse_start_end_range(args.start, args.end, fmt=DATE_FMT_DB)
    else:
        raise SystemExit("请指定 --start 与 --end，或仅用 --date 单日补齐")

    run_longrange_backfill(
        start_date=start_s,
        end_date=end_s,
        single_day=single,
        split_by_year=not args.no_split_year,
        skip_price=args.skip_price,
        no_mongo=args.no_mongo,
        mongo_alias=args.mongo_alias,
        mongo_db="basic_rq",
        mongo_collection="rq_daily_price_none",
    )
