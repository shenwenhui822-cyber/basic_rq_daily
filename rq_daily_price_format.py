# -*- coding: utf-8 -*-
"""rq_daily_price_none 入库格式：date 为 YYYY-MM-DD，价量数值两位小数（四舍五入）。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd

DATE_FMT_DB = "%Y-%m-%d"

DAILY_PRICE_NUM_COLS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "volume",
    "total_turnover",
    "limit_up",
    "limit_down",
)


def _round_half_up_2(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return np.nan
    except (TypeError, ValueError):
        pass
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def norm_price_date_str(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime(DATE_FMT_DB)


def round_daily_price_numeric_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out.columns:
        out["date"] = out["date"].map(norm_price_date_str)
    for col in DAILY_PRICE_NUM_COLS:
        if col not in out.columns:
            continue
        out[col] = out[col].map(_round_half_up_2)
    return out


def prepare_daily_price_df_for_mongo(df: pd.DataFrame) -> pd.DataFrame:
    return round_daily_price_numeric_cols(df).replace({np.nan: None})
