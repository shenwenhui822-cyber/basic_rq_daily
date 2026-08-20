"""
历史区间补齐 / 单日写入 basic_rq.rq_bench（对标 Wind w_bench）。

881001.WI（万得全 A）米筐无合约，行情用 399317.XSHE；落库 code 仍为 881001.WI。
000688.SH 为上交所科创 50（米筐 000688.XSHG）；勿与深交所合约混淆。

历史用法（basic_rq_daily 根目录）::

    python rq_history_backfill/backfill_rq_bench.py --start 2020-01-02 --end 2026-01-09

    # 仅补科创50历史（不删当日其它指数）
    python rq_history_backfill/backfill_rq_bench.py --start 2019-07-22 --end 2026-07-16 --codes 000688.SH

日更由 ``rq_daily_update/update_rq_bench.py`` 调用本模块 ``update_rq_bench``。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pymongo
import rqdatac as rq

_PKG_ROOT = Path(__file__).resolve().parents[1]
for _p in (_PKG_ROOT, _PKG_ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from rq_paths import bootstrap

bootstrap(__file__)

from rq_bench_format import prepare_bench_row_for_mongo
from trade_date_utils import mongo_trade_date_range, parse_explicit_date_arg, parse_start_end_range
from usedbdef import get_client

DATE_FMT_DB = "%Y-%m-%d"

# Wind code -> RQ order_book_id
# 000688.SH = 科创50（上交所）；米筐为 000688.XSHG
BENCH_WIND_TO_RQ: list[tuple[str, str]] = [
    ("000001.SH", "000001.XSHG"),
    ("399001.SZ", "399001.XSHE"),
    ("881001.WI", "399317.XSHE"),
    ("000300.SH", "000300.XSHG"),
    ("000905.SH", "000905.XSHG"),
    ("000852.SH", "000852.XSHG"),
    ("000688.SH", "000688.XSHG"),  # 科创50
]

_BENCH_BY_WIND = {wind: rq_id for wind, rq_id in BENCH_WIND_TO_RQ}

_RQ_INITIALIZED = False


def init_rq() -> None:
    global _RQ_INITIALIZED
    if _RQ_INITIALIZED:
        return
    rq.init("15317321758", "WuZhi@2026")
    print("RQData 连接成功 (rq_bench)")
    _RQ_INITIALIZED = True


def norm_bench_day(s: str) -> str:
    s = str(s).strip()
    if "/" in s:
        return pd.Timestamp(s.replace("/", "-")).strftime(DATE_FMT_DB)
    return pd.Timestamp(s).strftime(DATE_FMT_DB)


def day_variants(s: str) -> list[str]:
    s = norm_bench_day(s)
    y, m, d = s.split("-")
    return list(dict.fromkeys([s, f"{y}/{m}/{d}"]))


def rq_close_series(rq_id: str, end_day: str, lookback_days: int = 45) -> pd.Series | None:
    end_day = norm_bench_day(end_day)
    start = (pd.Timestamp(end_day) - pd.Timedelta(days=lookback_days)).strftime(DATE_FMT_DB)
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


def bench_row(wind_code: str, rq_id: str, day: str) -> dict[str, Any]:
    day = norm_bench_day(day)
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
        closes = rq_close_series(rq_id, day)
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
        amt = (
            float(row["total_turnover"])
            if "total_turnover" in row.index and pd.notna(row["total_turnover"])
            else None
        )
        close = float(row["close"])
        rq_index_code = str(rq_id).split(".")[0] if rq_id else ""
        rq_bench_substitute = wind_code == "881001.WI" and rq_id == "399317.XSHE"
        return {
            "date": day,
            "code": wind_code,
            "code_rq": rq_id,
            "rq_index_code": rq_index_code,
            "rq_bench_substitute": rq_bench_substitute,
            "pct_chg": pct,
            "volume": vol / 1_000_000,
            "amt": amt,
            "pre_close": pre_close,
            "close": close,
            "open": float(row["open"]) if "open" in row.index else None,
            "high": float(row["high"]) if "high" in row.index else None,
            "low": float(row["low"]) if "low" in row.index else None,
        }
    except Exception as e:
        print(f"基准 {wind_code} / {rq_id} 失败: {e}")
        return {}


def create_indexes_rq_bench(
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    *,
    client: Any | None = None,
) -> None:
    c = client if client is not None else get_client(mongo_alias)
    t = c[mongo_db]["rq_bench"]
    t.create_index(
        [("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)],
        background=True,
        unique=True,
    )


def _resolve_bench_pairs(codes: list[str] | None) -> list[tuple[str, str]]:
    """codes 为 Wind code 列表；None 表示全量 BENCH_WIND_TO_RQ。"""
    if not codes:
        return list(BENCH_WIND_TO_RQ)
    pairs: list[tuple[str, str]] = []
    unknown: list[str] = []
    for c in codes:
        code = str(c).strip().upper()
        if not code:
            continue
        rq_id = _BENCH_BY_WIND.get(code)
        if rq_id is None:
            unknown.append(code)
        else:
            pairs.append((code, rq_id))
    if unknown:
        raise ValueError(
            f"未知 bench code: {unknown}；可选: {sorted(_BENCH_BY_WIND.keys())}"
        )
    if not pairs:
        raise ValueError("未解析到任何有效 bench code")
    return pairs


def update_rq_bench(
    pre_trade_day: str,
    *,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    target_coll: str = "rq_bench",
    min_date: str = "1990-01-01",
    client: Any | None = None,
    codes: list[str] | None = None,
) -> bool:
    """
    写入 pre_trade_day 的 rq_bench。

    - codes=None：删当日全部文档后写入全量指数（日更默认）
    - codes=[...]：只删/写指定 Wind code（历史单指数补齐，不碰其它指数）
    """
    init_rq()
    env_min = os.environ.get("RQ_BENCH_MIN_DATE", "").strip()
    if env_min:
        min_date = env_min[:10]
    pre_trade_day = norm_bench_day(pre_trade_day)
    if pre_trade_day <= norm_bench_day(min_date):
        print(f"跳过 rq_bench（pre_trade_day <= {min_date}）")
        return True

    pairs = _resolve_bench_pairs(codes)
    only_codes = codes is not None

    c = client if client is not None else get_client(mongo_alias)
    table = c[mongo_db][target_coll]
    rows = []
    for wind_code, rq_id in pairs:
        r = bench_row(wind_code, rq_id, pre_trade_day)
        if r:
            rows.append(prepare_bench_row_for_mongo(r))

    if not rows:
        print("基准数据为空")
        return False

    wind_codes = [w for w, _ in pairs]
    for dv in day_variants(pre_trade_day):
        if only_codes:
            table.delete_many({"date": dv, "code": {"$in": wind_codes}})
        else:
            table.delete_many({"date": dv})
    table.insert_many(rows, ordered=False)
    scope = ",".join(wind_codes) if only_codes else "全量"
    print(f"{target_coll} 写入 {len(rows)} 条 (date={pre_trade_day}, {scope})")
    return True


def backfill_rq_bench(
    date_range: dict,
    *,
    mongo_alias: str = "wonderwz27018_rw",
    mongo_db: str = "basic_rq",
    min_date: str = "1990-01-01",
    codes: list[str] | None = None,
) -> None:
    client = get_client(mongo_alias)
    df_dates = pd.DataFrame(
        client.economic.trade_dates.find({"trade_date": date_range}, {"_id": 0})
    ).sort_values("trade_date")

    if df_dates.empty:
        print("未获取到交易日列表")
        return

    date_list = [norm_bench_day(d) for d in df_dates["trade_date"].tolist()]
    scope = ",".join(codes) if codes else "全量"
    print(
        f"bench 补齐区间: {date_list[0]} ~ {date_list[-1]}，"
        f"共 {len(date_list)} 个交易日，codes={scope}"
    )

    create_indexes_rq_bench(mongo_alias=mongo_alias, mongo_db=mongo_db, client=client)

    ok_count = 0
    for i, day in enumerate(date_list, start=1):
        print(f"\n=== [{i}/{len(date_list)}] {day} ===")
        if update_rq_bench(
            day,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            min_date=min_date,
            client=client,
            codes=codes,
        ):
            ok_count += 1

    print(f"\n完成：成功 {ok_count}/{len(date_list)} 个交易日")


def _parse_codes_arg(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    out: list[str] = []
    for item in raw:
        for part in str(item).split(","):
            code = part.strip().upper()
            if code:
                out.append(code)
    return list(dict.fromkeys(out)) or None


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="历史补齐 rq_bench")
    p.add_argument("--start", required=False, help="区间起（含）")
    p.add_argument("--end", required=False, help="区间止（含）")
    p.add_argument("--date", default=None, help="单日（设置后忽略 --start/--end）")
    p.add_argument(
        "--codes",
        action="append",
        default=None,
        help="只补指定 Wind code（可重复或逗号分隔），如 000688.SH；省略则全量重写每日",
    )
    p.add_argument("--mongo-alias", default="wonderwz27018_rw")
    p.add_argument("--mongo-db", default="basic_rq")
    p.add_argument("--min-date", default="1990-01-01")
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    codes = _parse_codes_arg(args.codes)
    if args.date:
        single = parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
        dr = mongo_trade_date_range(single, single)
    else:
        if not args.start or not args.end:
            raise SystemExit("须指定 --date，或同时指定 --start 与 --end")
        start_s, end_s = parse_start_end_range(args.start, args.end, fmt=DATE_FMT_DB)
        dr = mongo_trade_date_range(start_s, end_s)

    backfill_rq_bench(
        dr,
        mongo_alias=args.mongo_alias,
        mongo_db=args.mongo_db,
        min_date=str(args.min_date)[:10],
        codes=codes,
    )
