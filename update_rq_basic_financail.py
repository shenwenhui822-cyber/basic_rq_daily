"""
每日更新 rq_basic_financial（依赖当天 rq_base_info 已先更新）。

执行顺序要求：
1) 先运行 update_rqbaseInfo.py，确保当天 rq_base_info 已入库；
2) 再运行本脚本，基于 rq_base_info 当天 code_rq 列表拉取财务字段并落库。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any
from pathlib import Path

import numpy as np
import pandas as pd
import pymongo
import rqdatac as rq


DATE_FMT_DB = "%Y/%m/%d"

FACTOR_MAP = {
    "mkt_cap_ard": "market_cap_3",
    "or_ttm": "revenue_ttm_0",
    "netprofit_ttm": "net_profit_parent_company_ttm_0",
    "operatecashflow_ttm": "cash_flow_from_operating_activities_ttm_0",
}


try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def get_client(c_from: str = "local") -> pymongo.MongoClient:
    client_dict = {
        "local": {"host": "127.0.0.1", "port": 27017, "user": None, "pwd": None},
    }
    config = client_dict.get(c_from)
    if not config:
        raise ValueError(f"传入的数据库目标服务器有误 {c_from}，请检查 {list(client_dict.keys())}")

    if config.get("user") and config.get("pwd"):
        client_uri = f"mongodb://{config['user']}:{config['pwd']}@{config['host']}:{config['port']}"
    else:
        client_uri = f"mongodb://{config['host']}:{config['port']}"
    print(f"正在连接到 {c_from} 数据库：{config['host']}:{config['port']}")
    return pymongo.MongoClient(client_uri)


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})


def _is_trade_day(today_str: str, trade_dates_path: str) -> bool:
    df = pd.read_csv(trade_dates_path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    today_date = datetime.strptime(today_str.replace("/", "-"), "%Y-%m-%d").date()
    return today_date in df["trade_date"].dt.date.values


def _load_today_base_info_codes(*, table: Any, today_str: str) -> list[str]:
    day_variants = [today_str]
    if "/" in today_str:
        day_variants.append(today_str.replace("/", "-"))
    elif "-" in today_str:
        day_variants.append(today_str.replace("-", "/"))

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


def _extract_factor_series(df: pd.DataFrame, factor_name: str) -> pd.Series:
    if df is None or df.empty or factor_name not in df.columns:
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
            print(f"⚠️ 因子 {factor_name} 拉取失败：{exc}")
            out[out_col] = np.nan
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
        print(f"⚠️ get_shares 拉取失败：{exc}")
        out["total_shares"] = np.nan
        out["free_float_shares"] = np.nan
    return out


def _fetch_basic_financial_for_today(
    codes: list[str],
    *,
    today_str: str,
    chunk_size: int = 1500,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        print(f"拉取财务分批：{i + 1} ~ {i + len(chunk)} / {len(codes)}")
        fac = _fetch_factors_for_codes(chunk, today_str)
        shares = _fetch_shares_for_codes(chunk, today_str)
        merged = fac.join(shares, how="outer")
        parts.append(merged)

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, axis=0)
    out = out.reset_index().rename(columns={"order_book_id": "code_rq"})
    out["date"] = pd.Timestamp(today_str).strftime(DATE_FMT_DB)
    out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)

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
    out = out[cols].drop_duplicates(subset=["date", "code_rq"], keep="last")
    out = _df_nan_to_none(out)
    return out.sort_values(by=["date", "code"]).reset_index(drop=True)


def update_rq_basic_financail(
    today_str: str,
    trade_dates_path: str,
    *,
    mongo_alias: str = "local",
    base_db: str = "basic_rq",
    base_collection: str = "rq_base_info",
    target_db: str = "basic_rq",
    target_collection: str = "rq_basic_financial",
) -> bool:
    print(f"\n=== 开始更新 rq_basic_financial，日期：{today_str} ===")

    if not _is_trade_day(today_str, trade_dates_path):
        print(f"❌ {today_str} 不是交易日，跳过更新")
        return False
    print(f"✅ {today_str} 是交易日")

    client = get_client(mongo_alias)
    base_table = client[base_db][base_collection]
    target_table = client[target_db][target_collection]

    codes = _load_today_base_info_codes(table=base_table, today_str=today_str)
    if not codes:
        print(
            "❌ 未在 rq_base_info 中找到当天数据。"
            "请先执行 update_rqbaseInfo.py，再执行本脚本。"
        )
        return False
    print(f"✅ 从 rq_base_info 获取到 {len(codes)} 只股票")

    df_fin = _fetch_basic_financial_for_today(codes, today_str=today_str)
    if df_fin.empty:
        print("❌ 当天财务数据为空，更新失败")
        return False

    day_variants = [today_str]
    if "/" in today_str:
        day_variants.append(today_str.replace("/", "-"))
    elif "-" in today_str:
        day_variants.append(today_str.replace("-", "/"))
    dr = target_table.delete_many({"date": {"$in": day_variants}})
    print(f"已删除当天旧记录：{dr.deleted_count} 条")

    docs = df_fin.to_dict("records")
    target_table.insert_many(docs, ordered=False)
    print(f"✅ 已写入 {len(docs)} 条到 {target_db}.{target_collection}")
    return True


def _cli_target_date_str() -> str:
    p = argparse.ArgumentParser(description="更新 rq_basic_financial")
    p.add_argument(
        "--date",
        "-d",
        default=None,
        help="目标日期，如 20260507、2026/05/07、2026-05-07；默认今天",
    )
    args = p.parse_args()
    if not args.date:
        return datetime.now().strftime("%Y/%m/%d")
    s = str(args.date).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}/{s[4:6]}/{s[6:8]}"
    return pd.Timestamp(s.replace("/", "-")).strftime("%Y/%m/%d")


if __name__ == "__main__":
    TRADE_DATES_PATH = str(Path(__file__).resolve().parent / "trade_dates_all.csv")
    TODAY_STR = _cli_target_date_str()

    result = update_rq_basic_financail(
        today_str=TODAY_STR,
        trade_dates_path=TRADE_DATES_PATH,
        mongo_alias="local",
        base_db="basic_rq",
        base_collection="rq_base_info",
        target_db="basic_rq",
        target_collection="rq_basic_financial",
    )

    if result:
        print("\n✅ rq_basic_financial 更新成功")
    else:
        print("\n❌ rq_basic_financial 更新失败或不是交易日")
