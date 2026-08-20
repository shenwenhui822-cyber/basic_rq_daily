"""
每日更新申万二级行业成分（rq_daily_indusSWL2）。

输出字段：
- date: YYYY-MM-DD
- indus_code: 二级行业代码（去掉 .INDX）
- name: 二级行业名称
- stocks: 成分股列表字符串
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

import pandas as pd
import rqdatac as rq

from usedbdef import get_client
from trade_date_utils import is_trade_day, parse_explicit_date_arg, previous_trade_date

DATE_FMT_DB = "%Y-%m-%d"


try:
    rq.init("15317321758", "WuZhi@2026")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def _insert_df(table: Any, df: pd.DataFrame) -> None:
    docs = df.to_dict("records")
    if not docs:
        return
    table.insert_many(docs, ordered=False)


def _normalize_mapping(industry_mapping: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(industry_mapping, pd.DataFrame) or industry_mapping.empty:
        return pd.DataFrame()

    df = industry_mapping.copy()
    if "second_index_code" in df.columns and "second_index_name" in df.columns:
        pass
    elif len(df.columns) >= 4:
        # 兼容旧格式按顺序重命名（至少需要到二级行业）
        cols = list(df.columns)
        rename_map = {
            cols[2]: "second_index_code",
            cols[3]: "second_index_name",
        }
        df = df.rename(columns=rename_map)
    else:
        return pd.DataFrame()

    return (
        df[["second_index_code", "second_index_name"]]
        .dropna()
        .drop_duplicates()
        .reset_index(drop=True)
        .rename(columns={"second_index_code": "level2_code", "second_index_name": "level2_name"})
    )


def _extract_stock_codes(stocks: Any) -> list[str]:
    codes: list[str] = []
    if not stocks:
        return codes

    if isinstance(stocks, list):
        for x in stocks:
            if isinstance(x, str):
                codes.append(x)
            elif isinstance(x, (list, tuple)) and len(x) > 0:
                codes.append(str(x[0]))
    elif isinstance(stocks, dict):
        codes.extend([str(k) for k in stocks.keys()])
    elif hasattr(stocks, "__iter__") and not isinstance(stocks, (str, bytes)):
        for x in stocks:
            if isinstance(x, str):
                codes.append(x)
            elif isinstance(x, (list, tuple)) and len(x) > 0:
                codes.append(str(x[0]))
    return codes


def _build_swl2_for_day(today_str: str) -> pd.DataFrame:
    rq_date = pd.Timestamp(today_str).strftime("%Y/%m/%d")
    db_date = pd.Timestamp(today_str).strftime("%Y-%m-%d")

    print("正在获取申万行业映射数据...")
    industry_mapping = rq.get_industry_mapping(source="sws", date=rq_date, market="cn")
    df_level2 = _normalize_mapping(industry_mapping)
    if df_level2.empty:
        return pd.DataFrame()

    print(f"共获取到 {len(df_level2)} 个二级行业，开始拉成分股...")
    all_rows: list[dict[str, str]] = []

    for _, row in df_level2.iterrows():
        level2_code = str(row["level2_code"])
        level2_name = str(row["level2_name"])
        try:
            stocks = rq.get_industry(level2_code, source="sws", date=rq_date, market="cn")
            codes = _extract_stock_codes(stocks)
            if not codes:
                continue
            all_rows.append(
                {
                    "date": db_date,
                    "indus_code": level2_code.replace(".INDX", ""),
                    "name": level2_name,
                    "stocks": str(codes),
                }
            )
        except Exception as e:
            print(f"⚠️ 拉取行业 {level2_code} 成分失败：{e}")
            continue

    if not all_rows:
        return pd.DataFrame()

    out = pd.DataFrame(all_rows)
    out = out.drop_duplicates(subset=["date", "indus_code"], keep="last")
    return out[["date", "indus_code", "name", "stocks"]].reset_index(drop=True)


def update_rq_SWL2(
    today_str: str,
    *,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    mongo_collection: str = "rq_daily_indusSWL2",
) -> bool:
    print(f"\n=== 开始更新 rq_daily_indusSWL2，日期：{today_str} ===")

    client = get_client(mongo_alias)
    if not is_trade_day(today_str, client=client):
        print(f"❌ {today_str} 不是交易日，跳过更新")
        return False
    print(f"✅ {today_str} 是交易日")
    table = client[mongo_db][mongo_collection]

    df_day = _build_swl2_for_day(today_str)
    if df_day.empty:
        print("❌ 当天申万二级行业数据为空，更新失败")
        return False

    day_dash = pd.Timestamp(today_str).strftime("%Y-%m-%d")
    day_slash = pd.Timestamp(today_str).strftime("%Y/%m/%d")
    dr = table.delete_many({"date": {"$in": [day_dash, day_slash]}})
    print(f"已删除当天旧记录：{dr.deleted_count} 条")

    _insert_df(table, df_day)
    print(f"✅ 已写入 {len(df_day)} 条到 {mongo_db}.{mongo_collection}")
    return True


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="更新 rq_daily_indusSWL2")
    p.add_argument("--date", "-d", default=None, help="目标交易日；默认 T-1")
    p.add_argument("--mongo-alias", default="wonderwz27018_rw")
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    today_str = (
        parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
        if args.date
        else previous_trade_date(mongo_alias=args.mongo_alias, fmt=DATE_FMT_DB)
    )

    result = update_rq_SWL2(
        today_str=today_str,
        mongo_alias=args.mongo_alias,
        mongo_db="basic_rq",
        mongo_collection="rq_daily_indusSWL2",
    )

    if result:
        print("\n✅ rq_daily_indusSWL2 更新成功")
    else:
        print("\n❌ rq_daily_indusSWL2 更新失败或不是交易日")
