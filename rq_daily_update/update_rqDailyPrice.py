"""
每日更新 rq_daily_price_none（不复权日线，adjust_type=none）。

执行顺序要求：
1) 先运行 update_rqbaseInfo.py，确保目标交易日 rq_base_info 已入库；
2) 再运行本脚本，基于 rq_base_info 当日 code_rq 列表拉取日线并落库。

入库 date 格式：YYYY-MM-DD（如 2022-06-22）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__)

import argparse
from typing import Any

import numpy as np
import pandas as pd
import rqdatac as rq

from trade_date_utils import is_trade_day, parse_explicit_date_arg, previous_trade_date
from usedbdef import get_client

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

try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def _mongo_date_variants(d: str) -> list[str]:
    s = pd.Timestamp(str(d).strip().replace("/", "-")).strftime(DATE_FMT_DB)
    return list(dict.fromkeys([s, s.replace("-", "/")]))


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})


def _load_today_base_info_codes(*, table: Any, today_str: str) -> list[str]:
    day_variants = _mongo_date_variants(today_str)
    cursor = table.find(
        {"date": {"$in": day_variants}},
        {"_id": 0, "code_rq": 1},
    )
    df = pd.DataFrame(list(cursor))
    if df.empty or "code_rq" not in df.columns:
        return []
    return (
        df["code_rq"]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .tolist()
    )


def _daily_wide_to_long(df_wide: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df_wide is None or df_wide.empty:
        return pd.DataFrame()

    out = df_wide.copy()
    field_set = set(DAILY_PRICE_FIELDS)
    db_date = pd.Timestamp(trade_date).strftime(DATE_FMT_DB)

    if isinstance(out.columns, pd.MultiIndex) and out.columns.nlevels == 2:
        top = out.columns[0][0]
        if top in field_set:
            out = out.swaplevel(axis=1).sort_index(axis=1, level=0)
        stacked = out.stack(level=0).reset_index()
        c0, c1 = stacked.columns[0], stacked.columns[1]
        stacked = stacked.rename(columns={c0: "datetime", c1: "code_rq"})
        stacked["date"] = db_date
        stacked["code"] = stacked["code_rq"].astype(str).map(_rq_code_to_display)
        stacked = stacked.drop(columns=["datetime"])
        price_cols = [c for c in DAILY_PRICE_FIELDS if c in stacked.columns]
        return stacked[["date", "code", "code_rq"] + price_cols]

    out = out.reset_index()
    id_col = "order_book_id" if "order_book_id" in out.columns else None
    if id_col is None:
        for c in out.columns:
            if c in field_set:
                continue
            s = out[c].dropna().astype(str).head(30)
            if s.empty:
                continue
            if s.str.contains(r"\.XSHG|\.XSHE|\.XBSE", regex=True, na=False).any():
                id_col = c
                break
    if id_col is None:
        print(f"⚠️ 无法识别合约代码列，当前列示例：{list(out.columns)[:20]}")
        return pd.DataFrame()

    out = out.rename(columns={id_col: "code_rq"})
    out["date"] = db_date
    out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)
    price_cols = [c for c in DAILY_PRICE_FIELDS if c in out.columns]
    return out[["date", "code", "code_rq"] + price_cols]


def _fetch_today_prices(codes: list[str], today_str: str, chunk_size: int = 2000) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()

    rq_date = pd.Timestamp(today_str).strftime("%Y/%m/%d")
    parts: list[pd.DataFrame] = []
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        print(f"拉取日线分批：{i + 1} ~ {i + len(chunk)} / {len(codes)}")
        df_wide = rq.get_price(
            chunk,
            start_date=rq_date,
            end_date=rq_date,
            frequency="1d",
            fields=DAILY_PRICE_FIELDS,
            adjust_type="none",
            expect_df=True,
        )
        df_long = _daily_wide_to_long(df_wide, today_str)
        if not df_long.empty:
            parts.append(df_long)

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates(subset=["date", "code_rq"], keep="last")
    return _df_nan_to_none(out).sort_values(by=["date", "code"]).reset_index(drop=True)


def update_rqDailyPrice(
    today_str: str,
    *,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    base_collection: str = "rq_base_info",
    price_collection: str = "rq_daily_price_none",
) -> bool:
    """每日更新 rq_daily_price_none；要求目标交易日 rq_base_info 已存在。"""
    target = pd.Timestamp(today_str).strftime(DATE_FMT_DB)
    print(f"\n=== 开始更新 rq_daily_price_none，日期：{target} ===")

    client = get_client(mongo_alias)
    if not is_trade_day(target, client=client):
        print(f"❌ {target} 不是交易日，跳过更新")
        return False
    print(f"✅ {target} 是交易日")

    base_table = client[mongo_db][base_collection]
    price_table = client[mongo_db][price_collection]

    codes = _load_today_base_info_codes(table=base_table, today_str=target)
    if not codes:
        print(
            "❌ 未在 rq_base_info 中找到当天数据。"
            "请先执行 update_rqbaseInfo.py，再执行本脚本。"
        )
        return False

    print(f"✅ 从 rq_base_info 获取到 {len(codes)} 只股票")
    df_price = _fetch_today_prices(codes, today_str=target)
    if df_price.empty:
        print("❌ 当天日线数据为空，更新失败")
        return False

    dr = price_table.delete_many({"date": {"$in": _mongo_date_variants(target)}})
    print(f"已删除当天旧记录：{dr.deleted_count} 条")

    docs = df_price.to_dict("records")
    price_table.insert_many(docs, ordered=False)
    print(f"✅ 已写入 {len(docs)} 条到 {mongo_db}.{price_collection}")
    return True


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="更新 rq_daily_price_none（默认 T-1）")
    p.add_argument("--date", "-d", default=None, help="目标交易日；默认上一交易日")
    p.add_argument("--mongo-alias", default="wonderwz27018_rw")
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    today_str = (
        parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
        if args.date
        else previous_trade_date(mongo_alias=args.mongo_alias, fmt=DATE_FMT_DB)
    )

    ok = update_rqDailyPrice(
        today_str=today_str,
        mongo_alias=args.mongo_alias,
        mongo_db="basic_rq",
        base_collection="rq_base_info",
        price_collection="rq_daily_price_none",
    )
    if ok:
        print("\n✅ rq_daily_price_none 更新成功")
    else:
        print("\n❌ rq_daily_price_none 更新失败或不是交易日")
