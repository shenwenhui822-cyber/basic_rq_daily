"""
区间 1 分钟线落库：流程对齐 rq_getRangeDailyPrice（基础表 lookup → 按日分批拉数），
拉数使用 get_Min1Test1 的 get_price 1m 与字段降级策略。

⚠️ 分钟线数据量远大于日线，务必关注 RQ 配额；本脚本默认短区间 + 分批 + 多处配额检查。
落库：MongoDB ``rq_minute.rq_minute_none_YYYY``（按交易日年份自动分表）；每条文档含字符串 ``date``（``YYYY-MM-DD``）与 ``time``（``HH:MM:SS``）。
"""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from typing import Any


def _configure_windows_stdio() -> None:
    """
    Windows 下控制台乱码多半是「终端编码」与「Python 写出编码」不一致。

    - VSCode 集成终端默认多为 **UTF-8**：本脚本默认把 stdout/stderr 设为 **utf-8**（与之一致）。
    - 若你仍在 **GBK（cmd 活动代码页 936）** 终端里跑，请先执行::

        set RQ_MIN_CONSOLE_ENCODING=gbk

      再运行本脚本，会按 GBK 写出。

    在导入会立即 print 的模块（如 rq_getRangeDailyPrice）之前调用。
    """
    if sys.platform != "win32":
        return
    raw = os.environ.get("RQ_MIN_CONSOLE_ENCODING", "").strip().lower()
    if raw in ("gbk", "cp936", "gb2312", "936"):
        enc = "gbk"
    elif raw in ("utf-8", "utf8", "65001"):
        enc = "utf-8"
    else:
        enc = "utf-8"

    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding=enc, errors="replace")
            elif hasattr(stream, "buffer"):
                name = "stdout" if stream is sys.stdout else "stderr"
                w = io.TextIOWrapper(
                    stream.buffer,
                    encoding=enc,
                    errors="replace",
                    line_buffering=True,
                )
                setattr(sys, name, w)
    except Exception:
        pass


_configure_windows_stdio()

from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_root_dir = _script_dir.parent
for _p in (_root_dir, _script_dir):
    _s = str(_p)
    if _s in sys.path:
        sys.path.remove(_s)
    sys.path.insert(0, _s)

import argparse
import time

import numpy as np
import pandas as pd
import rqdatac as rq

from get_dayDataTest1 import DAILY_PRICE_FIELDS
from get_Min1Test1 import (
    CURRENT_MINUTE_FIELDS_FULL,
    fetch_trade_day_1m_bars_with_fallback,
    normalize_minute_wide,
    resolve_cn_trade_date,
)
from rq_getRangeDailyPrice import (
    check_rq_quota_or_exit,
    fetch_range_base_info,
    to_price_lookup_df,
    _df_nan_to_none,
    _is_rq_column_multiindex_wide,
    _rq_code_to_display,
)
from usedbdef import DEFAULT_MONGO_ALIAS, get_client, insert_db_from_df
from minute_mongo import MINUTE_DB, minute_collection_for_date

# 分钟线单次请求标的数（小于日线 2000，降低单次 payload）
MAX_IDS_PER_MINUTE_REQUEST = 600

QUOTA_STOP_FRACTION = 0.6

MONGO_DB = MINUTE_DB

# 入库日期/时间字符串格式（与业务示例一致：date="2025-04-28", time="10:37:00"）
DATE_FMT_MONGO_DAY = "%Y-%m-%d"
TIME_FMT_MONGO = "%H:%M:%S"

# 导入 rq_getRangeDailyPrice 时已 rq.init


def log_rq_quota_status(label: str = "") -> None:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    prefix = f"[{label}] " if label else ""
    if limit <= 0:
        print(f"{prefix}流量探查: bytes_used={used}, bytes_limit=0(不限)")
        return
    print(
        f"{prefix}流量探查: bytes_used={used}/{limit} ({100.0 * used / limit:.2f}%)"
    )


def _merge_minute_parts(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame()
    if len(parts) == 1:
        return parts[0]
    if _is_rq_column_multiindex_wide(parts[0]):
        merged = pd.concat(parts, axis=1)
        return normalize_minute_wide(merged, CURRENT_MINUTE_FIELDS_FULL)
    merged_long = pd.concat(parts, axis=0, sort=False)
    if isinstance(merged_long.index, pd.MultiIndex):
        merged_long = merged_long[
            ~merged_long.index.duplicated(keep="last")
        ]
    elif "order_book_id" in merged_long.columns and "datetime" in merged_long.columns:
        merged_long = merged_long.drop_duplicates(
            subset=["order_book_id", "datetime"], keep="last"
        )
    return merged_long


def fetch_one_trade_day_1m_all(
    order_book_ids: list[str],
    trade_date_str: str,
    *,
    chunk_size: int = MAX_IDS_PER_MINUTE_REQUEST,
) -> tuple[pd.DataFrame, list[str] | None]:
    """同一交易日全市场 1m：分批 get_price，合并为一张面板。"""
    ids = list(dict.fromkeys(order_book_ids))
    if not ids:
        return pd.DataFrame(), None

    parts: list[pd.DataFrame] = []
    used_fields: list[str] | None = None
    d_resolved = resolve_cn_trade_date(trade_date_str)

    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        bn = i // chunk_size + 1
        check_rq_quota_or_exit(
            label=f"{trade_date_str} 1m 第{bn}批({len(chunk)}只)",
            fraction=QUOTA_STOP_FRACTION,
        )

        if i == 0:
            df, used_fields, _ = fetch_trade_day_1m_bars_with_fallback(chunk, d_resolved)
        else:
            if used_fields is None:
                df = rq.get_price(
                    chunk,
                    start_date=d_resolved,
                    end_date=d_resolved,
                    frequency="1m",
                    expect_df=True,
                    market="cn",
                )
            else:
                df = rq.get_price(
                    chunk,
                    start_date=d_resolved,
                    end_date=d_resolved,
                    frequency="1m",
                    fields=used_fields,
                    expect_df=True,
                    market="cn",
                )
            df = normalize_minute_wide(
                df, used_fields or CURRENT_MINUTE_FIELDS_FULL
            )

        if df is not None and not df.empty:
            parts.append(df)
        time.sleep(0.08)

    merged = _merge_minute_parts(parts)
    return merged, used_fields if used_fields is not None else None


def minute_panel_to_mongo_df(df: pd.DataFrame) -> pd.DataFrame:
    """面板 → 入库长表：date（YYYY-MM-DD）、time（HH:MM:SS）、code、code_rq、行情列。"""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.reset_index()
    # 列名兼容
    if "order_book_id" not in out.columns:
        for a, b in [("level_0", "order_book_id"), ("level_1", "datetime")]:
            if a in out.columns and b not in out.columns:
                sample = out[a].astype(str).head(5)
                if a == "level_0" and sample.str.contains(r"\.XSHG|\.XSHE", regex=True).any():
                    out = out.rename(columns={a: "order_book_id"})
                if a == "level_1" and not sample.str.contains(r"\.XSHG|\.XSHE", regex=True).all():
                    out = out.rename(columns={a: "datetime"})

    if "datetime" not in out.columns:
        print(f"  [!] 无法解析 datetime 列，现有列: {list(out.columns)[:15]}")
        return pd.DataFrame()

    out["code_rq"] = out["order_book_id"].astype(str)
    out["code"] = out["code_rq"].map(_rq_code_to_display)
    _dt = pd.to_datetime(out["datetime"])
    out["date"] = _dt.dt.strftime(DATE_FMT_MONGO_DAY)
    out["time"] = _dt.dt.strftime(TIME_FMT_MONGO)

    drop_cols = [c for c in ("datetime", "order_book_id") if c in out.columns]
    out = out.drop(columns=drop_cols, errors="ignore")

    extra = [c for c in DAILY_PRICE_FIELDS + ["iopv"] if c in out.columns]
    cols = ["date", "time", "code", "code_rq"] + extra
    cols = [c for c in cols if c in out.columns]
    return out[cols].sort_values(by=["date", "time", "code_rq"])


def is_rq_quota_exceeded(fraction: float = QUOTA_STOP_FRACTION) -> bool:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    if limit <= 0:
        return False
    return used >= fraction * limit


def fetch_range_base_info_from_mongo(
    client,
    start_date: str,
    end_date: str,
    *,
    base_db: str = "basic_rq",
    base_collection: str = "rq_base_info",
) -> pd.DataFrame:
    """从 ``basic_rq.rq_base_info`` 读取区间 lookup（basic_rq 已就绪时用）。"""
    start_s = resolve_cn_trade_date(start_date)
    end_s = resolve_cn_trade_date(end_date)
    table = client[base_db][base_collection]
    cursor = table.find(
        {"date": {"$gte": start_s, "$lte": end_s}},
        {"_id": 0, "date": 1, "code": 1, "code_rq": 1},
    )
    df = pd.DataFrame(list(cursor))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime(DATE_FMT_MONGO_DAY)
    cols = [c for c in ("date", "code", "code_rq") if c in df.columns]
    return df[cols].dropna(subset=["date", "code_rq"]).drop_duplicates()


def run_minute_range_to_mongo(
    start_date: str,
    end_date: str,
    *,
    mongo_alias: str = DEFAULT_MONGO_ALIAS,
    chunk_size: int = MAX_IDS_PER_MINUTE_REQUEST,
    no_mongo: bool = False,
    skip_existing: bool = False,
    reverse_dates: bool = False,
    deadline: datetime | None = None,
    use_mongo_base_info: bool = True,
    month_label: str = "",
) -> dict[str, Any]:
    """
    区间 1m 落库（供定时补数 / CLI 调用）。

    reverse_dates=True 时同一自然月内按交易日倒序（离 anchor 近的日期优先）。

    Returns:
        ok_days, total_days, skipped_days, stopped_by_deadline, stopped_by_quota
    """
    from minute_mongo import minute_day_has_data

    start_s = resolve_cn_trade_date(start_date)
    end_s = resolve_cn_trade_date(end_date)
    prefix = f"{month_label} " if month_label else ""
    print(f"\n=== {prefix}分钟区间 {start_s} ~ {end_s} ===")

    if is_rq_quota_exceeded():
        print(f"{prefix}RQ 配额已达阈值，中止本段")
        return {
            "ok_days": 0,
            "total_days": 0,
            "skipped_days": 0,
            "stopped_by_deadline": False,
            "stopped_by_quota": True,
        }

    client = get_client(mongo_alias)
    if use_mongo_base_info:
        df_base = fetch_range_base_info_from_mongo(client, start_s, end_s)
        if df_base.empty:
            print(f"{prefix}Mongo rq_base_info 无数据，回退 RQ 拉基础表")
            check_rq_quota_or_exit(label=f"{prefix}拉基础表前")
            df_base = fetch_range_base_info(start_s, end_s)
    else:
        check_rq_quota_or_exit(label=f"{prefix}拉基础表前")
        df_base = fetch_range_base_info(start_s, end_s)

    if df_base.empty:
        print(f"{prefix}无基础 lookup，跳过")
        return {
            "ok_days": 0,
            "total_days": 0,
            "skipped_days": 0,
            "stopped_by_deadline": False,
            "stopped_by_quota": False,
        }

    df_keys = to_price_lookup_df(df_base)
    print(f"{prefix}lookup 行数: {len(df_keys)}")

    ok_days = 0
    total_days = 0
    skipped_days = 0
    grouped = list(df_keys.groupby("date", sort=True))
    if reverse_dates:
        grouped.reverse()

    for trade_date, g in grouped:
        from trade_date_utils import now_shanghai

        if deadline is not None and now_shanghai() >= deadline:
            print(
                f"{prefix}已达截止时间 {deadline.strftime('%H:%M')} (Asia/Shanghai)，暂停"
            )
            return {
                "ok_days": ok_days,
                "total_days": total_days,
                "skipped_days": skipped_days,
                "stopped_by_deadline": True,
                "stopped_by_quota": False,
            }

        key = resolve_cn_trade_date(str(trade_date))
        total_days += 1

        if skip_existing and minute_day_has_data(client, key, minute_db=MONGO_DB):
            print(f"  跳过已有 {key}")
            skipped_days += 1
            continue

        ids = g["code_rq"].tolist()
        print(f"\n>>> {prefix}交易日 {key}，标的 {len(ids)} 只，拉 1m…")

        if is_rq_quota_exceeded():
            print(f"{prefix}RQ 配额已达阈值，中止")
            return {
                "ok_days": ok_days,
                "total_days": total_days,
                "skipped_days": skipped_days,
                "stopped_by_deadline": False,
                "stopped_by_quota": True,
            }

        merged, _fields = fetch_one_trade_day_1m_all(ids, key, chunk_size=chunk_size)
        mongo_df = minute_panel_to_mongo_df(merged)
        if mongo_df.empty:
            print(f"  [!] {key} 转长表为空，跳过入库")
            log_rq_quota_status(f"{key} 结束后")
            continue

        date_db = mongo_df["date"].iloc[0]
        log_rq_quota_status(f"{key} 结束后")

        if no_mongo:
            ok_days += 1
            continue

        save_minute_day_to_mongo(
            mongo_df,
            mongo_alias=mongo_alias,
            date_for_delete=date_db,
        )
        ok_days += 1

    return {
        "ok_days": ok_days,
        "total_days": total_days,
        "skipped_days": skipped_days,
        "stopped_by_deadline": False,
        "stopped_by_quota": False,
    }


def save_minute_day_to_mongo(
    mongo_df: pd.DataFrame,
    *,
    mongo_alias: str = DEFAULT_MONGO_ALIAS,
    date_for_delete: str,
    delete_before_insert: bool = True,
) -> None:
    if mongo_df.empty:
        print(f"  跳过空分钟数据 date={date_for_delete}")
        return

    client = get_client(mongo_alias)
    mongo_collection = minute_collection_for_date(date_for_delete)
    table = client[MONGO_DB][mongo_collection]

    from minute_mongo import ensure_minute_collection_indexes

    ensure_minute_collection_indexes(table)
    print(f"\n=== 写入 MongoDB {MONGO_DB}.{mongo_collection} ({mongo_alias}) ===")

    mongo_df = _df_nan_to_none(mongo_df)
    if delete_before_insert:
        variants = [date_for_delete]
        if "/" in date_for_delete:
            variants.append(date_for_delete.replace("/", "-"))
        elif "-" in date_for_delete and len(date_for_delete) >= 10:
            try:
                ts = pd.Timestamp(date_for_delete)
                variants.append(ts.strftime("%Y/%m/%d"))
            except (ValueError, TypeError):
                pass
        dr = table.delete_many({"date": {"$in": list(dict.fromkeys(variants))}})
        print(f"  已删除 date 键匹配的旧文档: {dr.deleted_count} 条")

    insert_db_from_df(table, mongo_df)
    print(f"  [OK] 已插入 {len(mongo_df)} 条分钟线（date={date_for_delete}）")


def main(
    *,
    start_date: str,
    end_date: str,
    single_day: str | None = None,
) -> None:
    parser = argparse.ArgumentParser(description="区间 1 分钟线 → rq_minute.rq_minute_none_YYYY")
    parser.add_argument("--no-mongo", action="store_true", help="不落库")
    parser.add_argument(
        "--mongo-alias",
        default=DEFAULT_MONGO_ALIAS,
        help=f"Mongo 别名（见上级 mongo_connect.py），默认 {DEFAULT_MONGO_ALIAS}",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=MAX_IDS_PER_MINUTE_REQUEST,
        help=f"每批标的数量，默认 {MAX_IDS_PER_MINUTE_REQUEST}",
    )
    args = parser.parse_args()

    if single_day and str(single_day).strip():
        start_s = end_s = str(single_day).strip()
    else:
        start_s, end_s = str(start_date).strip(), str(end_date).strip()
        if not start_s or not end_s:
            raise ValueError("请填写 START_DATE / END_DATE 或 SINGLE_DAY")

    print(f"总区间: {start_s} ~ {end_s}")
    log_rq_quota_status("任务开始")

    stats = run_minute_range_to_mongo(
        start_s,
        end_s,
        mongo_alias=args.mongo_alias,
        chunk_size=args.chunk_size,
        no_mongo=args.no_mongo,
        use_mongo_base_info=False,
    )
    print(
        f"\n[OK] 分钟区间任务结束：写入 {stats['ok_days']}/{stats['total_days']} 日"
        f"（跳过已有 {stats['skipped_days']} 日）"
    )
    log_rq_quota_status("全部结束")


if __name__ == "__main__":
    # ---------------------------------------------------------------------------
    # 【运行前请核对】年份、区间、Mongo 是否与你当前任务一致（改完再跑）。
    # 与已落库日重叠时会先按 date delete 再 insert，可重复执行。
    # ---------------------------------------------------------------------------
    START_DATE = "2026-05-12"
    END_DATE = "2026-05-12"
    SINGLE_DAY = None
    main(
        start_date=START_DATE,
        end_date=END_DATE,
        single_day=SINGLE_DAY,
    )
