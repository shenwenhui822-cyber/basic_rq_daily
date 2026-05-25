# -*- coding: utf-8 -*-
"""rq_basic_financial 数值字段入库格式（两位小数、四舍五入）。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd

FINANCIAL_NUM_COLS: tuple[str, ...] = (
    "mkt_cap_ard",
    "total_shares",
    "free_float_shares",
    "or_ttm",
    "gr_ttm",
    "netprofit_ttm",
    "operatecashflow_ttm",
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


def round_financial_numeric_cols(df: pd.DataFrame) -> pd.DataFrame:
    """财务数值列保留两位小数（四舍五入）。"""
    out = df.copy()
    for col in FINANCIAL_NUM_COLS:
        if col not in out.columns:
            continue
        out[col] = out[col].map(_round_half_up_2)
    return out


def prepare_financial_df_for_mongo(df: pd.DataFrame) -> pd.DataFrame:
    """两位小数四舍五入后，NaN 转为 None 供 Mongo 写入。"""
    return round_financial_numeric_cols(df).replace({np.nan: None})
