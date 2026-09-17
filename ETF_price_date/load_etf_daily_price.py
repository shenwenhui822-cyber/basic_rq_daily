# -*- coding: utf-8 -*-
"""
批量拉取 ETF 日线（不复权）写入 ``basic_rq.rq_daily_price_none``。

目标：wonderwz27018_rw @ 192.168.110.199:27018
默认日期：上一交易日（T-1）= ``previous_trade_date``
  - 严格早于「今天」的最近交易日
  - 周六/周日运行时目标为周五，不会写成下周一
  - 定时任务 ``rq_etf_daily_price`` 在非交易日也会执行

文档格式与股票日线一致：
  date / code / code_rq / open / high / low / close / prev_close /
  volume / total_turnover / limit_up / limit_down

用法（仓库根目录）：
  python ETF_price_date/load_etf_daily_price.py
  python ETF_price_date/load_etf_daily_price.py --date 2026-09-16
  python ETF_price_date/load_etf_daily_price.py --start 2026-09-01 --end 2026-09-16
  python ETF_price_date/load_etf_daily_price.py --date 2026-09-16 --code 159830
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__)

import pandas as pd
import rqdatac as rq

from rq_daily_price_format import prepare_daily_price_df_for_mongo
from trade_date_utils import (
    is_trade_day,
    list_trade_dates,
    parse_explicit_date_arg,
    parse_start_end_range,
    previous_trade_date,
)
from usedbdef import get_client

DATE_FMT_DB = "%Y-%m-%d"
MONGO_ALIAS = "wonderwz27018_rw"
MONGO_DB = "basic_rq"
PRICE_COLLECTION = "rq_daily_price_none"

# 默认批量清单
DEFAULT_ETF_CODES: tuple[str, ...] = (
    "159830",
    "159845",
    "159919",
    "159922",
    "159925",
    "159934",
    "159937",
    "510050",
    "510100",
    "510300",
    "510310",
    "510330",
    "510350",
    "510360",
    "510500",
    "510510",
    "510580",
    "511010",
    "511090",
    "511130",
    "511260",
    "512100",
    "512500",
    "515330",
    "515380",
    "515390",
    "515660",
    "518600",
    "518660",
    "588080",
)

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
    rq.init("15317321758", "WuZhi@2026")
    print("RQData 连接成功")
except Exception as e:
    print(f"RQData 连接失败：{e}")
    raise


def _norm_date(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime(DATE_FMT_DB)


def etf_code_to_code_rq(code: str) -> str:
    """展示/数字码 → code_rq（不查米筐）：15/16→XSHE，其余→XSHG。"""
    raw = str(code).strip().upper()
    if raw.endswith(".XSHE") or raw.endswith(".XSHG"):
        return raw
    digits = raw
    if digits.startswith("SZ") or digits.startswith("SH"):
        digits = digits[2:]
    digits = "".join(ch for ch in digits if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"无法解析 ETF 代码: {code!r}")
    if digits.startswith(("15", "16")):
        return f"{digits}.XSHE"
    return f"{digits}.XSHG"


def etf_day_row_count(
    trade_date: str,
    etf_codes: list[str] | None = None,
    *,
    mongo_alias: str = MONGO_ALIAS,
) -> tuple[int, int, list[str]]:
    """
    返回 (已有条数, 期望条数, code_rqs)。
    用于判断目标日是否已完整落库，避免同一交易日重复拉取。
    """
    codes = list(etf_codes or DEFAULT_ETF_CODES)
    code_rqs = [etf_code_to_code_rq(c) for c in codes]
    day = _norm_date(trade_date)
    variants = list({day, day.replace("-", "/")})
    client = get_client(mongo_alias)
    n = client[MONGO_DB][PRICE_COLLECTION].count_documents(
        {"date": {"$in": variants}, "code_rq": {"$in": code_rqs}}
    )
    return n, len(code_rqs), code_rqs


def etf_day_already_complete(
    trade_date: str,
    etf_codes: list[str] | None = None,
    *,
    mongo_alias: str = MONGO_ALIAS,
) -> bool:
    n, expect, _ = etf_day_row_count(
        trade_date, etf_codes, mongo_alias=mongo_alias
    )
    return n >= expect


def resolve_etf_order_book_id(code: str) -> str:
    """将 159830 / SZ159830 / 159830.XSHE 规范为米筐 order_book_id。"""
    raw = str(code).strip().upper()
    if raw.endswith(".XSHE") or raw.endswith(".XSHG"):
        return raw
    digits = raw
    if digits.startswith("SZ") or digits.startswith("SH"):
        digits = digits[2:]
    digits = "".join(ch for ch in digits if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"无法解析 ETF 代码: {code!r}")

    # 15/16 开头多为深市 ETF；51 开头多为沪市
    candidates = (
        [f"{digits}.XSHE", f"{digits}.XSHG"]
        if digits.startswith(("15", "16"))
        else [f"{digits}.XSHG", f"{digits}.XSHE"]
    )
    for oid in candidates:
        try:
            info = rq.instruments(oid)
        except Exception:
            info = None
        if info is not None and getattr(info, "order_book_id", None):
            return str(info.order_book_id)
    raise ValueError(f"米筐未找到 ETF 合约: {code!r}（尝试 {candidates}）")


def resolve_etf_codes(codes: list[str]) -> tuple[list[str], list[str]]:
    """返回 (order_book_ids, 失败代码说明)。"""
    oids: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for code in codes:
        try:
            oid = resolve_etf_order_book_id(code)
        except ValueError as e:
            errors.append(str(e))
            continue
        if oid not in seen:
            seen.add(oid)
            oids.append(oid)
            print(f"  {code} → {oid}")
    return oids, errors


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def fetch_etf_daily_batch(
    order_book_ids: list[str],
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """拉取单日或区间 ETF 日线，返回长表（含多日）。"""
    if not order_book_ids:
        return pd.DataFrame()

    start = _norm_date(start_date)
    end = _norm_date(end_date or start_date)
    rq_start = pd.Timestamp(start).strftime("%Y/%m/%d")
    rq_end = pd.Timestamp(end).strftime("%Y/%m/%d")
    df = rq.get_price(
        order_book_ids,
        start_date=rq_start,
        end_date=rq_end,
        frequency="1d",
        fields=DAILY_PRICE_FIELDS,
        adjust_type="none",
        expect_df=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    field_set = set(DAILY_PRICE_FIELDS)

    if isinstance(out.columns, pd.MultiIndex) and out.columns.nlevels == 2:
        top = out.columns[0][0]
        if top in field_set:
            out = out.swaplevel(axis=1).sort_index(axis=1, level=0)
        stacked = out.stack(level=0, future_stack=True).reset_index()
        # 期望含 date + order_book_id（或等价列）
        colmap: dict[str, str] = {}
        for c in stacked.columns:
            cl = str(c).lower()
            if cl in ("order_book_id", "code_rq") and "code_rq" not in colmap.values():
                colmap[c] = "code_rq"
            elif cl in ("date", "datetime", "time") and "date" not in colmap.values():
                colmap[c] = "date"
        stacked = stacked.rename(columns=colmap)
        if "code_rq" not in stacked.columns and "order_book_id" in stacked.columns:
            stacked = stacked.rename(columns={"order_book_id": "code_rq"})
        if "date" not in stacked.columns:
            print(f"无法识别日期列，当前列: {list(stacked.columns)[:20]}")
            return pd.DataFrame()
        stacked["date"] = stacked["date"].map(_norm_date)
        stacked["code"] = stacked["code_rq"].astype(str).map(_rq_code_to_display)
        price_cols = [c for c in DAILY_PRICE_FIELDS if c in stacked.columns]
        return prepare_daily_price_df_for_mongo(
            stacked[["date", "code", "code_rq"] + price_cols]
        )

    out = out.reset_index()
    if "order_book_id" in out.columns:
        out = out.rename(columns={"order_book_id": "code_rq"})
    elif "code_rq" not in out.columns and len(order_book_ids) == 1:
        out["code_rq"] = order_book_ids[0]

    date_col = None
    for c in ("date", "datetime", "time"):
        if c in out.columns:
            date_col = c
            break
    if date_col is None:
        print(f"无法识别日期列，当前列: {list(out.columns)[:20]}")
        return pd.DataFrame()
    if "code_rq" not in out.columns:
        print(f"无法识别合约列，当前列: {list(out.columns)[:20]}")
        return pd.DataFrame()

    out["date"] = out[date_col].map(_norm_date)
    out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)
    for c in DAILY_PRICE_FIELDS:
        if c not in out.columns:
            out[c] = None
    cols = ["date", "code", "code_rq"] + DAILY_PRICE_FIELDS
    out = out[cols].drop_duplicates(subset=["date", "code_rq"], keep="last")
    return prepare_daily_price_df_for_mongo(out)


def write_etf_batch(
    df: pd.DataFrame,
    *,
    mongo_alias: str = MONGO_ALIAS,
    dry_run: bool = False,
) -> int:
    """按 date 分组写入；只覆盖清单内 ETF，不影响同日其它股票。"""
    if df.empty:
        return 0

    if dry_run:
        print(f"[dry-run] 将写入 {len(df)} 条，样例: {df.iloc[0].to_dict()}")
        return 0

    client = get_client(mongo_alias)
    table = client[MONGO_DB][PRICE_COLLECTION]
    inserted = 0
    for day, g in df.groupby("date", sort=True):
        day_s = str(day)
        code_rqs = g["code_rq"].astype(str).unique().tolist()
        docs = g.to_dict("records")
        deleted = table.delete_many(
            {"date": day_s, "code_rq": {"$in": code_rqs}}
        ).deleted_count
        table.insert_many(docs, ordered=False)
        inserted += len(docs)
        print(
            f"  {day_s}: 删旧 {deleted}，写入 {len(docs)} "
            f"（codes={len(code_rqs)}）"
        )
    print(f"合计写入 {inserted} 条 → {MONGO_DB}.{PRICE_COLLECTION}")
    return inserted


def load_etf_daily_prices(
    *,
    trade_dates: list[str],
    etf_codes: list[str],
    mongo_alias: str = MONGO_ALIAS,
    dry_run: bool = False,
) -> dict:
    if not trade_dates:
        return {"ok": False, "inserted": 0, "errors": ["交易日列表为空"]}

    dates = [_norm_date(d) for d in trade_dates]
    start, end = dates[0], dates[-1]
    client = get_client(mongo_alias)
    valid_days = [d for d in dates if is_trade_day(d, client=client)]
    skipped = [d for d in dates if d not in valid_days]
    if not valid_days:
        return {
            "ok": False,
            "inserted": 0,
            "errors": [f"区间内无交易日: {start}~{end}"],
        }

    print(
        f"\n=== 批量拉取 ETF 日线 | {valid_days[0]} ~ {valid_days[-1]} "
        f"| {len(valid_days)} 个交易日 | {len(etf_codes)} 只 ==="
    )
    if skipped:
        print(f"跳过非交易日: {skipped}")

    oids, resolve_errors = resolve_etf_codes(etf_codes)
    if not oids:
        return {"ok": False, "inserted": 0, "errors": resolve_errors}

    df = fetch_etf_daily_batch(oids, valid_days[0], valid_days[-1])
    if df.empty:
        err = f"无行情数据 @ {valid_days[0]}~{valid_days[-1]}"
        print(err)
        return {"ok": False, "inserted": 0, "errors": resolve_errors + [err]}

    # 仅保留目标交易日
    df = df[df["date"].isin(valid_days)].copy()
    if df.empty:
        err = "过滤交易日后无数据"
        print(err)
        return {"ok": False, "inserted": 0, "errors": resolve_errors + [err]}

    print(f"拉取到 {len(df)} 条，样例: {df.iloc[0].to_dict()}")
    n = write_etf_batch(df, mongo_alias=mongo_alias, dry_run=dry_run)

    errors = list(resolve_errors)
    # 按日检查缺票
    for day in valid_days:
        got = set(df.loc[df["date"] == day, "code_rq"].astype(str))
        missing = [oid for oid in oids if oid not in got]
        if missing:
            errors.append(f"{day} 无行情: {missing}")

    return {
        "ok": n > 0 and not errors,
        "start": valid_days[0],
        "end": valid_days[-1],
        "n_days": len(valid_days),
        "inserted": n,
        "errors": errors,
    }


def _parse_codes(raw: list[str] | None) -> list[str]:
    if not raw:
        return list(DEFAULT_ETF_CODES)
    out: list[str] = []
    for item in raw:
        for part in str(item).split(","):
            code = part.strip()
            if code:
                out.append(code)
    return list(dict.fromkeys(out)) or list(DEFAULT_ETF_CODES)


def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="批量拉取 ETF 日线写入 rq_daily_price_none（支持单日/区间）"
    )
    p.add_argument(
        "--date",
        "-d",
        default=None,
        help="单个交易日 YYYY-MM-DD；与 --start/--end 互斥",
    )
    p.add_argument("--start", default=None, help="区间起（含），与 --end 合用")
    p.add_argument("--end", default=None, help="区间止（含），与 --start 合用")
    p.add_argument(
        "--code",
        action="append",
        dest="codes",
        help="ETF 代码，可重复或逗号分隔；省略则用内置清单",
    )
    p.add_argument("--mongo-alias", default=MONGO_ALIAS)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _cli()
    mongo_alias = args.mongo_alias
    codes = _parse_codes(args.codes)

    try:
        if args.start or args.end:
            if not args.start or not args.end:
                raise ValueError("区间须同时指定 --start 与 --end")
            if args.date:
                raise ValueError("--date 与 --start/--end 不能同时使用")
            start_s, end_s = parse_start_end_range(args.start, args.end, fmt=DATE_FMT_DB)
            dates = list_trade_dates(start_s, end_s, mongo_alias=mongo_alias)
            if not dates:
                raise ValueError(f"区间 {start_s}~{end_s} 无交易日")
            print(f"目标区间: {start_s} ~ {end_s}（{len(dates)} 个交易日）| ETF 数: {len(codes)}")
        elif args.date:
            day = parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
            dates = [day]
            print(f"目标日期: {day} | ETF 数: {len(codes)}")
        else:
            day = previous_trade_date(mongo_alias=mongo_alias, fmt=DATE_FMT_DB)
            dates = [day]
            print(f"目标日期: {day}（T-1）| ETF 数: {len(codes)}")
    except ValueError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2

    result = load_etf_daily_prices(
        trade_dates=dates,
        etf_codes=codes,
        mongo_alias=mongo_alias,
        dry_run=args.dry_run,
    )
    if result.get("errors"):
        print("异常:")
        for e in result["errors"]:
            print(f"  - {e}")
    print(
        f"完成: inserted={result.get('inserted')} "
        f"days={result.get('n_days')} ok={result.get('ok')}"
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
