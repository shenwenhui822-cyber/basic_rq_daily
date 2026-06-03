# -*- coding: utf-8 -*-
"""rq_bench 入库格式：价量两位小数；pct_chg（涨跌幅比例）四位小数，均四舍五入。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import numpy as np
import pandas as pd

DATE_FMT_DB = "%Y-%m-%d"

BENCH_PRICE_VOL_COLS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amt",
)

PCT_CHG_COL = "pct_chg"


def _round_half_up(value, places: int) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return np.nan
    except (TypeError, ValueError):
        pass
    quant = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def _round_half_up_2(value) -> float | None:
    return _round_half_up(value, 2)


def _round_half_up_4(value) -> float | None:
    return _round_half_up(value, 4)


def norm_bench_date_str(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime(DATE_FMT_DB)


def round_bench_numeric_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out.columns:
        out["date"] = out["date"].map(norm_bench_date_str)
    for col in BENCH_PRICE_VOL_COLS:
        if col not in out.columns:
            continue
        out[col] = out[col].map(_round_half_up_2)
    if PCT_CHG_COL in out.columns:
        out[PCT_CHG_COL] = out[PCT_CHG_COL].map(_round_half_up_4)
    return out


def prepare_bench_row_for_mongo(row: dict[str, Any]) -> dict[str, Any]:
    """单条 rq_bench 文档：价量两位、pct_chg 四位，NaN 转 None。"""
    df = round_bench_numeric_cols(pd.DataFrame([row]))
    return df.replace({np.nan: None}).to_dict("records")[0]
