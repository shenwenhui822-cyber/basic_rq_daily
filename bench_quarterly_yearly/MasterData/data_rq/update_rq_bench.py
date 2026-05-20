"""
独立脚本：仅更新 basic_rq.rq_bench（对标 Wind w_bench）。

指数说明：881001.WI（Wind 万得全 A）米筐无对应合约，行情用国证 A 指 399317（RQ：399317.XSHE）。
落库仍用 code='881001.WI'、code_rq='399317.XSHE'，与 load_data 查询兼容。

依赖：rqdatac 已 init。

用法（项目根，PYTHONPATH=.）:
  python MasterData/data_rq/update_rq_bench.py
  python -c "from MasterData.data_rq.update_rq_bench import update_rq_bench; update_rq_bench('2025-12-01')"
"""

from __future__ import annotations

import os
import sys
from typing import Any

import numpy as np
import pandas as pd
import pymongo
import rqdatac as rq

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功 (update_rq_bench)")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def get_client(c_from: str = "local") -> pymongo.MongoClient:
    client_dict = {
        "local": {"host": "127.0.0.1", "port": 27017, "user": None, "pwd": None},
    }
    config = client_dict.get(c_from)
    if not config:
        raise ValueError(f"未知 mongo_alias: {c_from}")
    if config.get("user") and config.get("pwd"):
        uri = f"mongodb://{config['user']}:{config['pwd']}@{config['host']}:{config['port']}"
    else:
        uri = f"mongodb://{config['host']}:{config['port']}"
    return pymongo.MongoClient(uri)


def _norm_day(s: str) -> str:
    s = str(s).strip()
    if "/" in s:
        return pd.Timestamp(s.replace("/", "-")).strftime("%Y-%m-%d")
    return pd.Timestamp(s).strftime("%Y-%m-%d")


def _day_variants(s: str) -> list[str]:
    s = _norm_day(s)
    y, m, d = s.split("-")
    return list(dict.fromkeys([s, f"{y}/{m}/{d}"]))


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})


# Wind 基准代码 -> 米筐 order_book_id（881001.WI → 国证A指 399317，见阶段B 文档说明）
# 落库时每行含 rq_index_code（如 399317）与 rq_bench_substitute（881001 行为 True）
#
# 000300 / 000905 / 000852 均为中证指数官方代码，Wind 记为 .SH，米筐为 .XSHG；与「成分股含深市」无关。
# 勿改为 000905.SZ 等——会与 w_bench 中 code 及 CSI 合约不一致。
BENCH_WIND_TO_RQ: list[tuple[str, str]] = [
    ("000001.SH", "000001.XSHG"),
    ("399001.SZ", "399001.XSHE"),
    ("881001.WI", "399317.XSHE"),
    ("000300.SH", "000300.XSHG"),
    ("000905.SH", "000905.XSHG"),
    ("000852.SH", "000852.XSHG"),
]


def _rq_close_series(rq_id: str, end_day: str, lookback_days: int = 45) -> pd.Series | None:
    """拉取窗口内日收盘序列（当前 rqdatac 的 get_price 不支持 count=）。"""
    end_day = _norm_day(end_day)
    start = (pd.Timestamp(end_day) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    prev = rq.get_price(
        rq_id,
        start_date=start,
        end_date=end_day,
        frequency="1d",
        fields=["close"],
        expect_df=True,
    )
    if prev is None or (isinstance(prev, pd.DataFrame) and prev.empty):
        return None
    s = prev["close"].dropna()
    return s if len(s) else None


def _bench_row(wind_code: str, rq_id: str, day: str) -> dict[str, Any]:
    day = _norm_day(day)
    try:
        bar = rq.get_price(
            rq_id,
            start_date=day,
            end_date=day,
            frequency="1d",
            fields=["open", "high", "low", "close", "volume", "total_turnover"],
            expect_df=True,
        )
        if bar is None or (isinstance(bar, pd.DataFrame) and bar.empty):
            return {}
        if isinstance(bar, pd.DataFrame) and "order_book_id" in bar.columns:
            bar = bar[bar["order_book_id"] == rq_id]
        closes = _rq_close_series(rq_id, day)
        if closes is None or len(closes) < 2:
            pct = None
            pre_close = None
        else:
            c0 = float(closes.iloc[-2])
            c1 = float(closes.iloc[-1])
            pct = (c1 / c0 - 1) if c0 else None
            pre_close = c0
        row = bar.iloc[-1]
        vol = float(row["volume"]) if "volume" in row.index and pd.notna(row["volume"]) else 0.0
        amt = float(row["total_turnover"]) if "total_turnover" in row.index and pd.notna(row["total_turnover"]) else None
        close = float(row["close"])
        rq_index_code = str(rq_id).split(".")[0] if rq_id else ""
        rq_bench_substitute = wind_code == "881001.WI" and rq_id == "399317.XSHE"
        return {
            "date": day,
            "code": wind_code,
            "code_rq": rq_id,
            "rq_index_code": rq_index_code,
            "rq_bench_substitute": rq_bench_substitute,
            "pct_chg": format(pct, ".14f") if pct is not None else None,
            "volume": vol / 1_000_000,
            "amt": amt,
            "pre_close": pre_close,
            "close": close,
            "open": float(row["open"]) if "open" in row.index else None,
            "high": float(row["high"]) if "high" in row.index else None,
            "low": float(row["low"]) if "low" in row.index else None,
        }
    except Exception as e:
        print(f"⚠️ 基准 {wind_code} / {rq_id} 失败: {e}")
        return {}


def update_rq_bench(
    pre_trade_day: str,
    *,
    mongo_alias: str = "local",
    mongo_db: str = "basic_rq",
    target_coll: str = "rq_bench",
    min_date: str = "1990-01-01",
) -> bool:
    """
    min_date：不写入 **早于** 该日（pre_trade_day）的 bench；默认 1990 起即不跳过。
    若需恢复旧行为（仅 2023 起写 bench），可传 min_date='2023-01-01' 或环境变量 RQ_BENCH_MIN_DATE。
    """
    env_min = os.environ.get("RQ_BENCH_MIN_DATE", "").strip()
    if env_min:
        min_date = env_min[:10]
    pre_trade_day = _norm_day(pre_trade_day)
    if pre_trade_day <= _norm_day(min_date):
        print(f"⏭ 跳过 rq_bench（pre_trade_day <= {min_date}）")
        return True

    client = get_client(mongo_alias)
    table = client[mongo_db][target_coll]
    rows = []
    for wind_code, rq_id in BENCH_WIND_TO_RQ:
        r = _bench_row(wind_code, rq_id, pre_trade_day)
        if r:
            rows.append(_df_nan_to_none(pd.DataFrame([r])).to_dict("records")[0])

    if not rows:
        print("❌ 基准数据为空")
        return False

    for dv in _day_variants(pre_trade_day):
        table.delete_many({"date": dv})
    table.insert_many(rows, ordered=False)
    print(f"✅ {target_coll} 写入 {len(rows)} 条 (date={pre_trade_day})")
    return True


def create_indexes_rq_bench(
    mongo_alias: str = "local",
    mongo_db: str = "basic_rq",
) -> None:
    """为 rq_bench 建 (date, code) 唯一索引。"""
    c = get_client(mongo_alias)
    t = c[mongo_db]["rq_bench"]
    t.create_index([("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)], background=True, unique=True)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="更新 basic_rq.rq_bench")
    p.add_argument(
        "pre_trade_day",
        nargs="?",
        default=os.environ.get("RQ_BENCH_PRE_DAY", "2025-07-07"),
        help="数据交易日（与 Wind 语义一致：写入该日行情）",
    )
    p.add_argument("--mongo", default="local", help="Mongo 别名，默认 local")
    p.add_argument("--db", default="basic_rq", help="数据库名")
    p.add_argument("--index", action="store_true", help="仅建索引，不写数")
    p.add_argument(
        "--min-date",
        default=os.environ.get("RQ_BENCH_MIN_DATE", "1990-01-01"),
        help="早于此 pre_trade_day 不写 bench，默认 1990-01-01；仅写 2023 起可设 2023-01-01",
    )
    args = p.parse_args()

    if args.index:
        create_indexes_rq_bench(mongo_alias=args.mongo, mongo_db=args.db)
        print("✅ rq_bench 索引已处理")
    else:
        create_indexes_rq_bench(mongo_alias=args.mongo, mongo_db=args.db)
        update_rq_bench(
            args.pre_trade_day,
            mongo_alias=args.mongo,
            mongo_db=args.db,
            min_date=str(args.min_date)[:10],
        )
