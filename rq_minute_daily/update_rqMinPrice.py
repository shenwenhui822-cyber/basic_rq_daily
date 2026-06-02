"""
每日更新 1 分钟行情（依赖当天 rq_base_info 已先更新）。

执行顺序要求：
1) 先运行 update_rqbaseInfo.py，确保当天 rq_base_info 已入库；
2) 再运行本脚本，基于 rq_base_info 当天 code_rq 列表拉取 1m 并落库。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pymongo
import rqdatac as rq

_PKG_DIR = Path(__file__).resolve().parent
_PKG_ROOT = _PKG_DIR.parent
for _p in (_PKG_ROOT, _PKG_DIR):
    _s = str(_p)
    if _s in sys.path:
        sys.path.remove(_s)
    sys.path.insert(0, _s)

from trade_date_utils import is_trade_day, parse_explicit_date_arg, previous_trade_date
from usedbdef import DEFAULT_MONGO_ALIAS, get_client
from minute_mongo import MINUTE_DB, minute_collection_for_date


MINUTE_PRICE_FIELDS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "total_turnover",
    "num_trades",
]

DATE_FMT_DB = "%Y-%m-%d"
TIME_FMT_DB = "%H:%M:%S"
INSERT_CHUNK_ROWS = 50000


try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})


def _load_today_base_info_codes(*, table: Any, today_str: str) -> list[str]:
    """从 rq_base_info 读取当天 code_rq 列表。"""
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


def _minute_panel_to_long(df_panel: pd.DataFrame) -> pd.DataFrame:
    """
    将 rq.get_price(1m) 返回面板转长表。
    输出列: date,time,code,code_rq,行情字段...
    """
    if df_panel is None or df_panel.empty:
        return pd.DataFrame()

    out = df_panel.copy()
    field_set = set(MINUTE_PRICE_FIELDS)

    # 形态 A：列 MultiIndex，列=(code_rq, field)，索引=datetime
    if isinstance(out.columns, pd.MultiIndex) and out.columns.nlevels == 2:
        top = out.columns[0][0]
        if top in field_set:
            out = out.swaplevel(axis=1).sort_index(axis=1, level=0)
        out = out.stack(level=0).reset_index()
        c0, c1 = out.columns[0], out.columns[1]
        out = out.rename(columns={c0: "datetime", c1: "code_rq"})
    else:
        # 形态 B：长表（常见为 index 多级：datetime + order_book_id）
        out = out.reset_index()
        if "order_book_id" in out.columns:
            out = out.rename(columns={"order_book_id": "code_rq"})
        elif "level_1" in out.columns:
            sample = out["level_1"].dropna().astype(str).head(20)
            if sample.str.contains(r"\.XSHG|\.XSHE|\.XBSE", regex=True, na=False).any():
                out = out.rename(columns={"level_1": "code_rq"})

        if "datetime" not in out.columns and "level_0" in out.columns:
            out = out.rename(columns={"level_0": "datetime"})

    if "code_rq" not in out.columns or "datetime" not in out.columns:
        print(f"⚠️ 分钟面板转长表失败，缺少关键列。当前列: {list(out.columns)[:20]}")
        return pd.DataFrame()

    dt = pd.to_datetime(out["datetime"], errors="coerce")
    out["date"] = dt.dt.strftime(DATE_FMT_DB)
    out["time"] = dt.dt.strftime(TIME_FMT_DB)
    out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)
    out = out.drop(columns=["datetime"], errors="ignore")

    keep_fields = [c for c in MINUTE_PRICE_FIELDS if c in out.columns]
    cols = ["date", "time", "code", "code_rq"] + keep_fields
    out = out[cols]
    out = out.dropna(subset=["date", "time", "code_rq"])
    out = out.drop_duplicates(subset=["date", "time", "code_rq"], keep="last")
    return out.sort_values(by=["date", "time", "code_rq"]).reset_index(drop=True)


def _fetch_today_minute_prices(
    codes: list[str],
    *,
    today_str: str,
    chunk_size: int = 600,
) -> pd.DataFrame:
    """分批拉取当天 1m，合并后返回长表。"""
    if not codes:
        return pd.DataFrame()

    parts: list[pd.DataFrame] = []
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        print(f"拉取 1m 分批：{i + 1} ~ {i + len(chunk)} / {len(codes)}")
        df_panel = rq.get_price(
            chunk,
            start_date=today_str,
            end_date=today_str,
            frequency="1m",
            fields=MINUTE_PRICE_FIELDS,
            expect_df=True,
            market="cn",
        )
        df_long = _minute_panel_to_long(df_panel)
        if not df_long.empty:
            parts.append(df_long)

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates(subset=["date", "time", "code_rq"], keep="last")
    out = _df_nan_to_none(out)
    return out.sort_values(by=["date", "time", "code_rq"]).reset_index(drop=True)


def _insert_df_in_chunks(
    table: Any,
    df: pd.DataFrame,
    *,
    chunk_rows: int = INSERT_CHUNK_ROWS,
) -> int:
    """
    分块写入，避免 insert_many 一次性占用过多内存导致 MemoryError。
    """
    if df is None or df.empty:
        return 0

    total = 0
    n = len(df)
    for i in range(0, n, chunk_rows):
        j = min(i + chunk_rows, n)
        chunk_df = df.iloc[i:j]
        docs = chunk_df.to_dict("records")
        if not docs:
            continue
        table.insert_many(docs, ordered=False)
        total += len(docs)
        print(f"分钟数据入库分块：{i + 1} ~ {j} / {n}")
    return total


def _wait_mongo_ready(
    client: pymongo.MongoClient,
    *,
    retries: int = 6,
    sleep_seconds: int = 5,
) -> bool:
    """
    检查 Mongo 可用性并重试，避免服务短暂抖动时直接失败。
    """
    for i in range(1, retries + 1):
        try:
            client.admin.command("ping")
            return True
        except Exception as e:
            print(f"Mongo ping 失败（第 {i}/{retries} 次）：{repr(e)}")
            if i < retries:
                time.sleep(sleep_seconds)
    return False


def update_rqMinPrice(
    today_str: str,
    *,
    mongo_alias: str = DEFAULT_MONGO_ALIAS,
    base_db: str = "basic_rq",
    base_collection: str = "rq_base_info",
    minute_db: str = MINUTE_DB,
    minute_collection: str | None = None,
) -> bool:
    """
    每日更新 1 分钟行情。
    注意：该函数要求当天 rq_base_info 已提前更新。
    minute_collection 未指定时，按 today_str 年份写入 rq_minute_none_YYYY。
    """
    if not minute_collection:
        minute_collection = minute_collection_for_date(today_str)

    print(f"\n=== 开始更新 1 分钟行情，日期：{today_str} ===")
    print(f"目标集合：{minute_db}.{minute_collection}")

    client = get_client(mongo_alias)
    if not is_trade_day(today_str, client=client):
        print(f"❌ {today_str} 不是交易日，跳过更新")
        return False
    print(f"✅ {today_str} 是交易日")
    if not _wait_mongo_ready(client):
        print(f"❌ MongoDB 不可用，请检查 ../mongo_connect.py 中别名 {mongo_alias!r} 及网络后重试")
        return False

    base_table = client[base_db][base_collection]
    minute_table = client[minute_db][minute_collection]

    from minute_mongo import ensure_minute_collection_indexes

    ensure_minute_collection_indexes(minute_table)

    try:
        codes = _load_today_base_info_codes(table=base_table, today_str=today_str)
    except Exception as e:
        print(f"❌ 读取 rq_base_info 失败：{repr(e)}")
        return False
    if not codes:
        print(
            "❌ 未在 rq_base_info 中找到当天数据。"
            "请先执行 update_rqbaseInfo.py，再执行本脚本。"
        )
        return False
    print(f"✅ 从 rq_base_info 获取到 {len(codes)} 只股票")

    df_min = _fetch_today_minute_prices(codes, today_str=today_str)
    if df_min.empty:
        print("❌ 当天 1 分钟数据为空，更新失败")
        return False

    date_for_delete = pd.Timestamp(today_str).strftime(DATE_FMT_DB)
    dr = minute_table.delete_many({"date": date_for_delete})
    print(f"已删除当天旧分钟数据：{dr.deleted_count} 条（date={date_for_delete}）")

    inserted = _insert_df_in_chunks(minute_table, df_min, chunk_rows=INSERT_CHUNK_ROWS)
    print(f"✅ 已写入 {inserted} 条到 {minute_db}.{minute_collection}")
    return True


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="更新 rq 分钟行情")
    p.add_argument("--date", "-d", default=None, help="目标交易日；默认 T-1")
    p.add_argument("--mongo-alias", default=DEFAULT_MONGO_ALIAS)
    p.add_argument(
        "--collection",
        default=None,
        help="指定集合名；默认按 --date 年份自动 rq_minute_none_YYYY",
    )
    cli = p.parse_args()
    today_str = (
        parse_explicit_date_arg(cli.date, fmt="%Y/%m/%d")
        if cli.date
        else previous_trade_date(mongo_alias=cli.mongo_alias, fmt="%Y/%m/%d")
    )

    result = update_rqMinPrice(
        today_str=today_str,
        mongo_alias=cli.mongo_alias,
        base_db="basic_rq",
        base_collection="rq_base_info",
        minute_db=MINUTE_DB,
        minute_collection=cli.collection,
    )

    if result:
        print("\n✅ rq 分钟行情更新成功")
    else:
        print("\n❌ rq 分钟行情更新失败或不是交易日")
