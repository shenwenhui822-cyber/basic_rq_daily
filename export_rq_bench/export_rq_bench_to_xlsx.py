# -*- coding: utf-8 -*-
"""
从 MongoDB basic_rq.rq_bench 按指数导出 xlsx（每个指数一个文件）。

修改下方配置后在本目录执行:
    python export_rq_bench_to_xlsx.py

依赖: pip install pandas pymongo openpyxl
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pymongo

# ---------------------------------------------------------------------------
# 手动修改
# ---------------------------------------------------------------------------
mongodb_url = "mongodb://reader:readonly_wonderwz@192.168.110.199:27018"

START_DATE = "2026-01-02"   # 区间起（含）
END_DATE = "2026-05-20"     # 区间止（含）

# 要导出的指数（Wind 代码）；删掉或注释即不导出
INDICES = [
    "000001.SH",   # 上证综指
    "399001.SZ",   # 深证成指
    "881001.WI",   # 万得全A
    "000300.SH",   # 沪深300
    "000905.SH",   # 中证500
    "000852.SH",   # 中证1000
    "000688.SH",   # 科创50
]


# INDICES = [
#     "000001.SH",   # 上证综指
#     "399001.SZ",   # 深证成指
#     "881001.WI",   # 万得全A
#     "000300.SH",   # 沪深300
#     "000905.SH",   # 中证500
#     "000852.SH",   # 中证1000
# ]
OUTPUT_DIR = "output"      
MONGO_DB = "basic_rq"
COLLECTION = "rq_bench"
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent


def _norm_date(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime("%Y-%m-%d")


def _compact_date(s: str) -> str:
    return _norm_date(s).replace("-", "")


def _safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", str(s).strip())


def _xlsx_path(out_dir: Path, code: str, start: str, end: str) -> Path:
    s, e = _compact_date(start), _compact_date(end)
    name = f"{_safe_name(code)}_{s}.xlsx" if s == e else f"{_safe_name(code)}_{s}_{e}.xlsx"
    return out_dir / name


def _fetch(client: pymongo.MongoClient, code: str, start: str, end: str) -> pd.DataFrame:
    start, end = _norm_date(start), _norm_date(end)
    coll = client[MONGO_DB][COLLECTION]
    rows = list(coll.find({"code": code}, {"_id": 0}).sort("date", 1))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["_d"] = df["date"].astype(str).map(_norm_date)
    return df[(df["_d"] >= start) & (df["_d"] <= end)].drop(columns=["_d"]).reset_index(drop=True)


def main() -> None:
    if not INDICES:
        print("INDICES 为空，请在脚本顶部配置要导出的指数")
        return

    start, end = _norm_date(START_DATE), _norm_date(END_DATE)
    if start > end:
        raise ValueError(f"START_DATE ({start}) 不能晚于 END_DATE ({end})")

    out_dir = (_SCRIPT_DIR / OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Mongo: {MONGO_DB}.{COLLECTION}")
    print(f"区间: {start} ~ {end}")
    print(f"指数: {', '.join(INDICES)}")
    print(f"输出: {out_dir}\n")

    client = pymongo.MongoClient(mongodb_url)
    ok = 0
    for code in INDICES:
        df = _fetch(client, code, start, end)
        if df.empty:
            print(f"  [skip] {code}: 无数据")
            continue
        path = _xlsx_path(out_dir, code, start, end)
        df.to_excel(path, index=False, engine="openpyxl")
        print(f"  [ok] {code}: {len(df)} 行 -> {path.name}")
        ok += 1

    print(f"\n完成: {ok}/{len(INDICES)} 个文件")


if __name__ == "__main__":
    main()
