"""
每日更新申万二级行业成分 + 行业指数日线价量（rq_daily_indusSWL2_price）。

与 update_rq_SWL2.py（rq_daily_indusSWL2，仅成分）完全分表：
- 本脚本只读写 basic_rq.rq_daily_indusSWL2_price
- 不删除、不写入 rq_daily_indusSWL2

字段：date, indus_code, name, stocks, open, high, low, close, volume, total_turnover
"""

from __future__ import annotations

import argparse
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import rqdatac as rq

from get_SWL2_2DB_price_Main import PRICE_FIELDS, fetch_level2_index_prices
from usedbdef import get_client, insert_db_from_df
from trade_date_utils import parse_explicit_date_arg, previous_trade_date

try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def _is_trade_day(today_str: str, trade_dates_path: str) -> bool:
    df = pd.read_csv(trade_dates_path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    today_date = datetime.strptime(today_str.replace("/", "-"), "%Y-%m-%d").date()
    return today_date in df["trade_date"].dt.date.values


def _build_swl2_price_for_day(today_str: str, batch_size: int = 10) -> pd.DataFrame:
    """单日：申万二级成分 + get_price 六字段；与 get_SWL2_2DB_price_Main 同一套逻辑。"""
    inputdate = pd.Timestamp(today_str).strftime("%Y-%m-%d")
    start_date = pd.Timestamp(today_str).strftime("%Y/%m/%d")

    print("正在获取申万行业映射数据...")
    industry_mapping = rq.get_industry_mapping(source="sws", date=start_date, market="cn")

    try:
        if isinstance(industry_mapping, pd.DataFrame):
            df = industry_mapping.copy()
            if (
                "index_code" in df.columns
                and "index_name" in df.columns
                and "second_index_code" in df.columns
                and "second_index_name" in df.columns
            ):
                pass
            elif len(df.columns) >= 6:
                df.columns = [
                    "index_code",
                    "index_name",
                    "second_index_code",
                    "second_index_name",
                    "third_index_code",
                    "third_index_name",
                ]
            else:
                raise ValueError("DataFrame列数不足，无法构建完整层级")
        else:
            raise ValueError(f"未知的数据类型，需要DataFrame格式: {type(industry_mapping)}")
    except Exception as e:
        print(f"数据转换失败: {e}")
        print(f"原始数据详情: {industry_mapping}")
        return pd.DataFrame()

    df_level2 = df[["second_index_code", "second_index_name"]].drop_duplicates().reset_index(drop=True)
    df_level2 = df_level2.rename(
        columns={"second_index_code": "level2_code", "second_index_name": "level2_name"}
    )
    print(f"共获取到 {len(df_level2)} 个二级行业")

    print("开始获取各二级行业成分股...")
    all_stocks: list[dict[str, str]] = []
    total_industries = len(df_level2)

    for batch_start in range(0, total_industries, batch_size):
        batch_end = min(batch_start + batch_size, total_industries)
        batch_industries = df_level2.iloc[batch_start:batch_end]
        print(f"处理成分股批次: {batch_start + 1}-{batch_end}/{total_industries}")

        for _, row in batch_industries.iterrows():
            industry_code = row["level2_code"]
            industry_name = row["level2_name"]
            try:
                stocks = rq.get_industry(industry_code, source="sws", date=start_date, market="cn")
                if not stocks:
                    continue
                if isinstance(stocks, list):
                    for stock in stocks:
                        all_stocks.append(
                            {
                                "level2_code": industry_code,
                                "level2_name": industry_name,
                                "stock_code": stock,
                            }
                        )
                elif isinstance(stocks, dict):
                    for stock_code in stocks.keys():
                        all_stocks.append(
                            {
                                "level2_code": industry_code,
                                "level2_name": industry_name,
                                "stock_code": stock_code,
                            }
                        )
                elif hasattr(stocks, "__iter__") and not isinstance(stocks, (str, bytes)):
                    for item in stocks:
                        if isinstance(item, (list, tuple)) and len(item) > 0:
                            all_stocks.append(
                                {
                                    "level2_code": industry_code,
                                    "level2_name": industry_name,
                                    "stock_code": item[0],
                                }
                            )
                        elif isinstance(item, str):
                            all_stocks.append(
                                {
                                    "level2_code": industry_code,
                                    "level2_name": industry_name,
                                    "stock_code": item,
                                }
                            )
            except Exception:
                continue

    if not all_stocks:
        print("❌ 未获取到任何成分股数据")
        return pd.DataFrame()

    df_stocks = pd.DataFrame(all_stocks)
    print("正在转换为行业分组格式...")
    df_industry_stocks = (
        df_stocks.groupby(["level2_code", "level2_name"])["stock_code"]
        .apply(lambda x: str(list(x)))
        .reset_index()
    )
    df_industry_stocks["date"] = inputdate
    df_industry_stocks["indus_code"] = df_industry_stocks["level2_code"].astype(str).str.replace(
        ".INDX", "", regex=False
    )
    df_industry_stocks = df_industry_stocks.rename(
        columns={"level2_name": "name", "stock_code": "stocks"}
    )

    ob_ids = df_industry_stocks["level2_code"].astype(str).unique().tolist()
    print(f"正在拉取 {len(ob_ids)} 个行业指数日线价量: {PRICE_FIELDS} ...")
    price_df = fetch_level2_index_prices(ob_ids, str(inputdate))
    df_industry_stocks = df_industry_stocks.merge(
        price_df,
        left_on="level2_code",
        right_on="order_book_id",
        how="left",
    ).drop(columns=["order_book_id"], errors="ignore")

    out_cols = ["date", "indus_code", "name", "stocks"] + PRICE_FIELDS
    return df_industry_stocks[out_cols].reset_index(drop=True)


def update_rq_SWL2_price(
    today_str: str,
    trade_dates_path: str,
    *,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    mongo_collection: str = "rq_daily_indusSWL2_price",
) -> bool:
    """
    仅更新「价量表」rq_daily_indusSWL2_price；不动 rq_daily_indusSWL2。
    """
    print(f"\n=== 开始更新 {mongo_collection}（价量表），日期：{today_str} ===")

    if not _is_trade_day(today_str, trade_dates_path):
        print(f"❌ {today_str} 不是交易日，跳过更新")
        return False
    print(f"✅ {today_str} 是交易日")

    try:
        df_day = _build_swl2_price_for_day(today_str)
    except Exception as e:
        print(f"构建数据失败: {e}")
        traceback.print_exc()
        return False

    if df_day.empty:
        print("❌ 当天申万二级价量数据为空，更新失败")
        return False

    client = get_client(mongo_alias)
    table: Any = client[mongo_db][mongo_collection]

    day_dash = pd.Timestamp(today_str).strftime("%Y-%m-%d")
    day_slash = pd.Timestamp(today_str).strftime("%Y/%m/%d")
    dr = table.delete_many({"date": {"$in": [day_dash, day_slash]}})
    print(f"已从 {mongo_db}.{mongo_collection} 删除当天旧记录：{dr.deleted_count} 条")

    insert_db_from_df(table, df_day)
    print(f"✅ 已写入 {len(df_day)} 条到 {mongo_db}.{mongo_collection}")
    return True


def _cli_target_date_str(trade_dates_path: str) -> str:
    """--date 显式指定；省略则为 T-1（上一交易日）。"""
    p = argparse.ArgumentParser(description="更新 rq_daily_indusSWL2_price")
    p.add_argument(
        "--date",
        "-d",
        default=None,
        help="目标交易日；默认 T-1（上一交易日）",
    )
    args = p.parse_args()
    if not args.date:
        return previous_trade_date(trade_dates_path, fmt="%Y/%m/%d")
    return parse_explicit_date_arg(args.date, fmt="%Y/%m/%d")


if __name__ == "__main__":
    TRADE_DATES_PATH = str(Path(__file__).resolve().parent / "trade_dates_all.csv")
    TODAY_STR = _cli_target_date_str(TRADE_DATES_PATH)

    ok = update_rq_SWL2_price(
        today_str=TODAY_STR,
        trade_dates_path=TRADE_DATES_PATH,
        mongo_alias="wonderwz27018_rw",
        mongo_db="basic_rq",
        mongo_collection="rq_daily_indusSWL2_price",
    )

    if ok:
        print("\n✅ rq_daily_indusSWL2_price 更新成功")
    else:
        print("\n❌ rq_daily_indusSWL2_price 更新失败或不是交易日")
