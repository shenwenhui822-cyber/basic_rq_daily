"""
日线宽表：`rq.get_price` 封装；与 ``update_rqDailyPrice.DAILY_PRICE_FIELDS`` 对齐。

使用前需已由调用方执行 ``rqdatac.init``（与各 range 管线脚本一致）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import rqdatac as rq

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


def get_daily_price_wide(
    order_book_ids: list[str],
    start_date: str,
    end_date: str,
    *,
    fields: list[str] | None = None,
) -> Any:
    """单日或区间日线宽表（米筐原生形态：MultiIndex 或长表）。"""
    fq = fields or list(DAILY_PRICE_FIELDS)
    return rq.get_price(
        order_book_ids,
        start_date=start_date,
        end_date=end_date,
        frequency="1d",
        fields=fq,
        adjust_type="none",
        expect_df=True,
    )


def normalize_price_wide(df: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    """
    多块 MultiIndex 宽表 ``pd.concat(..., axis=1)`` 后整理列：
    去掉重复列名（保留最后一次），并按列索引排序以利下游核对。
    """
    _ = fields  # 与旧接口对齐；字段集合已由 get_price 约束
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
        out = out.sort_index(axis=1)
    elif out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
    return out
