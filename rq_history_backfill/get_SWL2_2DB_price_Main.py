# -*- coding: utf-8 -*-
"""
申万二级行业成分 + 行业指数日线价量（open/high/low/close/volume/total_turnover）落库。

在 get_SWL2_2DB_Main 流程基础上，对每个交易日的各 second_index_code（如 801206.INDX）
调用 rq.get_price，将六字段并入与 basic_rq 一致的 date / indus_code / name / stocks 行。
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
import traceback

import pandas as pd
import rqdatac as rq

from trade_date_utils import mongo_trade_date_range
from usedbdef import get_client, insert_db_from_df

_RQ_INITIALIZED = False


def _init_rq() -> None:
    global _RQ_INITIALIZED
    if _RQ_INITIALIZED:
        return
    try:
        rq.init("15317321758", "WuZhi@2026")
        print("RQData 连接成功")
        _RQ_INITIALIZED = True
    except Exception as e:
        print(f"RQData 连接失败: {e}")
        raise

PRICE_FIELDS = ["open", "high", "low", "close", "volume", "total_turnover"]


def _normalize_price_frame(px: pd.DataFrame | None) -> pd.DataFrame:
    """将 get_price 返回统一为含 order_book_id 与 PRICE_FIELDS 的 DataFrame。"""
    if px is None or len(px) == 0:
        return pd.DataFrame(columns=["order_book_id"] + PRICE_FIELDS)

    out = px.reset_index()
    if "order_book_id" not in out.columns:
        for alt in ("instrument", "code", "order_book_ids"):
            if alt in out.columns:
                out = out.rename(columns={alt: "order_book_id"})
                break
    if "order_book_id" not in out.columns:
        raise ValueError(f"无法识别合约列，当前列: {list(out.columns)}")

    for f in PRICE_FIELDS:
        if f not in out.columns:
            out[f] = None
    return out[["order_book_id"] + PRICE_FIELDS]


def fetch_level2_index_prices(order_book_ids: list[str], trade_date: str, chunk: int = 80) -> pd.DataFrame:
    """按交易日批量拉取行业指数日线价量。trade_date: YYYY-MM-DD。"""
    ids = [str(x).strip() for x in order_book_ids if str(x).strip()]
    if not ids:
        return pd.DataFrame(columns=["order_book_id"] + PRICE_FIELDS)

    parts: list[pd.DataFrame] = []
    for i in range(0, len(ids), chunk):
        batch = ids[i : i + chunk]
        try:
            px = rq.get_price(
                order_book_ids=batch,
                start_date=trade_date,
                end_date=trade_date,
                frequency="1d",
                fields=PRICE_FIELDS,
            )
            parts.append(_normalize_price_frame(px))
        except Exception as e:
            print(f"[WARN] get_price 批次 {i}-{i + len(batch)}__{e}")

    if not parts:
        return pd.DataFrame(columns=["order_book_id"] + PRICE_FIELDS)
    return pd.concat(parts, ignore_index=True)


def main(
    date_range,
    mongo_client_name: str = "wonderwz27018_rw",
    save_db_name: str = "basic_rq",
    save_table_name: str = "rq_daily_indusSWL2_price",
):
    """获取申万二级行业成分股 + 行业指数日线六个价量字段，写入 Mongo。

    :param date_range: 如 {'$gte': "2025-04-30", '$lte': "2025-04-30"}"""
    _init_rq()

    client = get_client(mongo_client_name)
    table = client[save_db_name][save_table_name]

    try:
        df_dates = pd.DataFrame(
            client.economic.trade_dates.find({"trade_date": date_range}, {"_id": 0})
        ).sort_values("trade_date")

        if not df_dates.empty:
            date_list = df_dates["trade_date"].to_list()
            print(f"数据下载区间: {date_list[0]} ~ {date_list[-1]}")
            print(f"总共有 {len(date_list)} 个交易日需要处理")
        else:
            print("未获取到日期数据")
            date_list = []
    except Exception as e:
        print(f"获取日期范围时出错: {e}")
        date_list = []

    success_count = 0
    total_count = len(date_list)
    batch_size = 10

    for inputdate in date_list:
        print(f"\\n=== 处理日期: {inputdate} ===")
        try:
            start_date = str(inputdate).replace("-", "/")
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
                        raise ValueError("DataFrame 列数不足，无法构建完整层级")
                    print("数据格式识别成功 - 完整层级结构")
                else:
                    raise ValueError(f"未知的数据类型，需要 DataFrame 格式: {type(industry_mapping)}")
            except Exception as e:
                print(f"数据转换失败: {e}")
                print(f"原始数据详情: {industry_mapping}")
                continue

            df_level2 = df[["second_index_code", "second_index_name"]].drop_duplicates().reset_index(drop=True)
            df_level2 = df_level2.rename(
                columns={"second_index_code": "level2_code", "second_index_name": "level2_name"}
            )
            print(f"共获取到 {len(df_level2)} 个二级行业")

            print("\\n开始获取各二级行业成分股...")
            all_stocks = []
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
                print("\\n[ERR] 未获取到任何成分股数据")
                continue

            df_stocks = pd.DataFrame(all_stocks)
            print("\\n正在转换为行业分组格式...")
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
            print(f"\\n正在拉取 {len(ob_ids)}__{PRICE_FIELDS} ...")
            price_df = fetch_level2_index_prices(ob_ids, str(inputdate))
            df_industry_stocks = df_industry_stocks.merge(
                price_df,
                left_on="level2_code",
                right_on="order_book_id",
                how="left",
            ).drop(columns=["order_book_id"], errors="ignore")

            out_cols = ["date", "indus_code", "name", "stocks"] + PRICE_FIELDS
            df_industry_stocks = df_industry_stocks[out_cols]

            print("\\n正在将数据插入到数据库...")
            insert_db_from_df(table, df_industry_stocks)
            print(f"[OK] 数据插入完成，共插入 {len(df_industry_stocks)} 条记录")
            success_count += 1

        except Exception as e:
            print(f"处理日期 {inputdate}__{e}")
            traceback.print_exc()
            continue

    print(f"\\n=== 处理完成 ===")
    print(f"总共有 {total_count} 个交易日")
    print(f"成功处理 {success_count} 个交易日")
    print(f"失败 {total_count - success_count} 个交易日")


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="历史补齐 rq_daily_indusSWL2_price：申万二级成分 + 行业指数日 K",
    )
    p.add_argument(
        "--start",
        default="2026-05-12",
        help="区间起（含）：YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD",
    )
    p.add_argument("--end", default="2026-05-12", help="区间止（含），格式同 --start")
    p.add_argument(
        "--mongo-alias",
        default="wonderwz27018_rw",
        help="Mongo 连接别名",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    main(
        date_range=mongo_trade_date_range(args.start, args.end),
        mongo_client_name=args.mongo_alias,
        save_db_name="basic_rq",
        save_table_name="rq_daily_indusSWL2_price",
    )
