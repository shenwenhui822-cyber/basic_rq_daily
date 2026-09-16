# -*- coding: utf-8 -*-
"""
每日更新 rq_alpha101（WorldQuant_alpha001 … WorldQuant_alpha101）。

依赖：同日 ``rq_base_info`` 已入库（取其 code_rq 列表）。
落库：``basic_rq.rq_alpha101``，字段 date / code / code_rq + 101 个因子（float64/Double）。

流量：当日占用 ≥ bytes_limit 的 50% 即停止。

用法（仓库根目录）：
  python rq_daily_update/update_rq_alpha101.py
  python rq_daily_update/update_rq_alpha101.py --date 2026-09-15
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__)

import numpy as np
import pandas as pd
import rqdatac as rq

from trade_date_utils import is_trade_day, parse_explicit_date_arg, previous_trade_date
from usedbdef import get_client

DATE_FMT_DB = "%Y-%m-%d"
MONGO_ALIAS = "wonderwz27018_rw"
TARGET_DB = "basic_rq"
TARGET_COLLECTION = "rq_alpha101"
BASE_COLLECTION = "rq_base_info"

QUOTA_STOP_FRACTION = 0.5
CODE_CHUNK = 800
FACTOR_BATCH = 20
INSERT_BATCH = 2000
SLEEP_BETWEEN_CALLS = 0.15

ALPHA_FACTORS: list[str] = [f"WorldQuant_alpha{i:03d}" for i in range(1, 102)]

try:
    rq.init("15317321758", "WuZhi@2026")
    print("RQData 连接成功")
except Exception as e:
    print(f"RQData 连接失败：{e}")
    raise


def _norm_date(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime(DATE_FMT_DB)


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def _to_jsonable(val: Any) -> Any:
    if val is None:
        return None
    try:
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
    except TypeError:
        pass
    if hasattr(val, "item"):
        try:
            val = val.item()
        except Exception:
            pass
    if isinstance(val, (np.floating, float)):
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(val, (np.integer, int)):
        return int(val)
    if isinstance(val, (str, bool)):
        return val
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def log_rq_quota(label: str = "") -> dict[str, Any]:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    remaining = q.get("remaining_days")
    lic = q.get("license_type")
    prefix = f"[{label}] " if label else ""
    if limit <= 0:
        print(
            f"{prefix}流量: used={used}, limit=0(不限), "
            f"remaining_days={remaining}, license={lic}"
        )
    else:
        pct = 100.0 * used / limit
        print(
            f"{prefix}流量: used={used}/{limit} ({pct:.2f}%), "
            f"阈值={QUOTA_STOP_FRACTION * 100:.0f}%, "
            f"remaining_days={remaining}, license={lic}"
        )
    return q


def check_rq_quota_or_raise(*, label: str = "") -> None:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    if limit <= 0:
        return
    ratio = used / limit
    prefix = f"[{label}] " if label else ""
    if ratio >= QUOTA_STOP_FRACTION:
        raise RuntimeError(
            f"{prefix}当日流量占比 {ratio * 100:.2f}% ≥ "
            f"{QUOTA_STOP_FRACTION * 100:.0f}%，已停止（不超过一半）"
        )


def load_codes_from_base_info(client: Any, trade_date: str) -> list[str]:
    day = _norm_date(trade_date)
    cursor = client[TARGET_DB][BASE_COLLECTION].find(
        {"date": day},
        {"_id": 0, "code_rq": 1},
    )
    df = pd.DataFrame(list(cursor))
    if df.empty or "code_rq" not in df.columns:
        return []
    return df["code_rq"].dropna().astype(str).drop_duplicates().tolist()


def load_codes_from_rq(trade_date: str) -> list[str]:
    day_slash = pd.Timestamp(trade_date).strftime("%Y/%m/%d")
    df = rq.all_instruments(type="CS", date=day_slash, market="cn")
    if df is None or df.empty:
        return []
    return df["order_book_id"].dropna().astype(str).drop_duplicates().tolist()


def _extract_factor_frame(df: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=factors)

    out = df.copy()
    if isinstance(out.index, pd.MultiIndex):
        names = list(out.index.names)
        out = out.reset_index()
        id_col = "order_book_id" if "order_book_id" in out.columns else names[0]
        if "date" in out.columns:
            out = out.sort_values("date").groupby(id_col, as_index=False).tail(1)
        out = out.set_index(id_col)
    elif "order_book_id" in out.columns:
        out = out.set_index("order_book_id")

    keep = [c for c in factors if c in out.columns]
    return out[keep]


def fetch_alpha101_one_day(
    codes: list[str],
    trade_date: str,
    *,
    code_chunk: int = CODE_CHUNK,
    factor_batch: int = FACTOR_BATCH,
) -> pd.DataFrame:
    day = _norm_date(trade_date)
    parts: list[pd.DataFrame] = []
    n_codes = len(codes)
    n_code_batches = max(1, (n_codes + code_chunk - 1) // code_chunk)
    n_factor_batches = (len(ALPHA_FACTORS) + factor_batch - 1) // factor_batch

    print(
        f"拉取计划: {n_codes} 只 × {len(ALPHA_FACTORS)} 因子 | "
        f"股票批={code_chunk}({n_code_batches}批) "
        f"因子批={factor_batch}({n_factor_batches}批/股票批)"
    )

    for ci in range(0, n_codes, code_chunk):
        chunk = codes[ci : ci + code_chunk]
        batch_i = ci // code_chunk + 1
        check_rq_quota_or_raise(label=f"股票批 {batch_i}/{n_code_batches} 前")
        log_rq_quota(f"股票批 {batch_i}/{n_code_batches}")

        merged_frames: list[pd.DataFrame] = []
        for fi in range(0, len(ALPHA_FACTORS), factor_batch):
            factors = ALPHA_FACTORS[fi : fi + factor_batch]
            fbatch_i = fi // factor_batch + 1
            check_rq_quota_or_raise(
                label=f"股票批{batch_i} 因子批{fbatch_i}/{n_factor_batches}"
            )
            try:
                raw = rq.get_factor(
                    chunk, factors, day, day, expect_df=True, market="cn"
                )
                frame = _extract_factor_frame(raw, factors)
                for col in factors:
                    if col not in frame.columns:
                        frame[col] = np.nan
                merged_frames.append(frame[factors])
            except Exception as exc:
                print(f"  ⚠️ 股票批{batch_i} 因子批{fbatch_i} 失败: {exc}")
                merged_frames.append(
                    pd.DataFrame(np.nan, index=chunk, columns=factors)
                )
            time.sleep(SLEEP_BETWEEN_CALLS)

        merged = pd.concat(merged_frames, axis=1)
        merged = merged.reindex(chunk)
        merged.index.name = "order_book_id"
        parts.append(merged)
        print(
            f"  完成股票批 {batch_i}/{n_code_batches} "
            f"({ci + 1}~{ci + len(chunk)} / {n_codes})"
        )

    if not parts:
        return pd.DataFrame()

    out = pd.concat(parts, axis=0)
    out = out.reset_index().rename(columns={"order_book_id": "code_rq"})
    out["date"] = day
    out["code"] = out["code_rq"].astype(str).map(_rq_code_to_display)

    cols = ["date", "code", "code_rq"] + ALPHA_FACTORS
    for c in ALPHA_FACTORS:
        if c not in out.columns:
            out[c] = np.nan
    out = out[cols].drop_duplicates(subset=["date", "code_rq"], keep="last")
    return out.sort_values(["date", "code"]).reset_index(drop=True)


def df_to_docs(df: pd.DataFrame) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        d: dict[str, Any] = {
            "date": row.date,
            "code": row.code,
            "code_rq": row.code_rq,
        }
        for name in ALPHA_FACTORS:
            d[name] = _to_jsonable(getattr(row, name))
        docs.append(d)
    return docs


def ensure_indexes(table: Any) -> None:
    table.create_index(
        [("date", 1), ("code_rq", 1)],
        unique=True,
        background=True,
        name="uniq_date_code_rq",
    )
    table.create_index([("date", 1)], background=True, name="idx_date")


def write_mongo(
    client: Any,
    df: pd.DataFrame,
    trade_date: str,
    *,
    dry_run: bool = False,
    target_db: str = TARGET_DB,
    target_collection: str = TARGET_COLLECTION,
) -> int:
    day = _norm_date(trade_date)
    table = client[target_db][target_collection]
    docs = df_to_docs(df)
    if dry_run:
        print(f"[dry-run] 将写入 {len(docs)} 条到 {target_db}.{target_collection}")
        return 0

    ensure_indexes(table)
    deleted = table.delete_many({"date": day}).deleted_count
    print(f"已删除 {day} 旧记录: {deleted} 条")

    inserted = 0
    for i in range(0, len(docs), INSERT_BATCH):
        batch = docs[i : i + INSERT_BATCH]
        table.insert_many(batch, ordered=False)
        inserted += len(batch)
    print(f"已写入 {inserted} 条 → {target_db}.{target_collection}")
    return inserted


def update_rq_alpha101(
    today_str: str,
    *,
    mongo_alias: str = MONGO_ALIAS,
    base_db: str = TARGET_DB,
    base_collection: str = BASE_COLLECTION,
    target_db: str = TARGET_DB,
    target_collection: str = TARGET_COLLECTION,
    dry_run: bool = False,
    code_chunk: int = CODE_CHUNK,
    factor_batch: int = FACTOR_BATCH,
    allow_all_instruments_fallback: bool = False,
) -> bool:
    """单日更新；成功返回 True。"""
    day = _norm_date(today_str)
    print(f"\n=== 开始更新 rq_alpha101，日期：{day} ===")
    log_rq_quota("开始前")
    check_rq_quota_or_raise(label="开始前")

    client = get_client(mongo_alias)
    if not is_trade_day(day, client=client):
        print(f"❌ {day} 不是交易日，跳过更新")
        return False
    print(f"✅ {day} 是交易日")

    cursor = client[base_db][base_collection].find(
        {"date": day}, {"_id": 0, "code_rq": 1}
    )
    df_codes = pd.DataFrame(list(cursor))
    if not df_codes.empty and "code_rq" in df_codes.columns:
        codes = (
            df_codes["code_rq"].dropna().astype(str).drop_duplicates().tolist()
        )
    else:
        codes = []

    if codes:
        print(f"✅ 从 {base_collection} 获取到 {len(codes)} 只股票")
    elif allow_all_instruments_fallback:
        print(f"⚠️ {base_collection} 无 {day} 数据，回退 all_instruments")
        codes = load_codes_from_rq(day)
        print(f"all_instruments 取到 {len(codes)} 只")
    else:
        print(
            f"❌ 未在 {base_collection} 中找到当天数据。"
            "请先执行 update_rqbaseInfo.py，再执行本脚本。"
        )
        return False

    if not codes:
        print(f"❌ {day} 无可用股票列表")
        return False

    df = fetch_alpha101_one_day(
        codes, day, code_chunk=code_chunk, factor_batch=factor_batch
    )
    if df.empty:
        print("❌ 当天 Alpha101 数据为空，更新失败")
        return False

    write_mongo(
        client,
        df,
        day,
        dry_run=dry_run,
        target_db=target_db,
        target_collection=target_collection,
    )
    log_rq_quota("结束后")
    return True


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="更新 rq_alpha101（WorldQuant Alpha101）")
    p.add_argument(
        "--date",
        "-d",
        default=None,
        help="目标交易日；默认 T-1（上一交易日）",
    )
    p.add_argument(
        "--mongo-alias",
        default=MONGO_ALIAS,
        help=f"Mongo 别名，默认 {MONGO_ALIAS}",
    )
    p.add_argument("--dry-run", action="store_true", help="只拉取不写库")
    p.add_argument("--code-chunk", type=int, default=CODE_CHUNK)
    p.add_argument("--factor-batch", type=int, default=FACTOR_BATCH)
    return p.parse_args()


def main() -> int:
    args = _cli_args()
    today_str = (
        parse_explicit_date_arg(args.date, fmt=DATE_FMT_DB)
        if args.date
        else previous_trade_date(mongo_alias=args.mongo_alias, fmt=DATE_FMT_DB)
    )
    try:
        ok = update_rq_alpha101(
            today_str,
            mongo_alias=args.mongo_alias,
            dry_run=args.dry_run,
            code_chunk=args.code_chunk,
            factor_batch=args.factor_batch,
        )
    except RuntimeError as e:
        print(f"\n中止: {e}", file=sys.stderr)
        log_rq_quota("中止时")
        return 1

    if ok:
        print("\n✅ rq_alpha101 更新成功")
        return 0
    print("\n❌ rq_alpha101 更新失败或不是交易日")
    return 1


if __name__ == "__main__":
    sys.exit(main())
