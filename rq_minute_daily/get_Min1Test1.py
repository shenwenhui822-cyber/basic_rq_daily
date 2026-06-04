"""
1 分钟 K 线：``rq.get_price(..., frequency='1m')`` 封装与宽表规整。

使用前需已由调用方执行 ``rqdatac.init``。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import rqdatac as rq

# 与 update_rqMinPrice.MINUTE_PRICE_FIELDS 一致
CURRENT_MINUTE_FIELDS_FULL = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "total_turnover",
]


def resolve_cn_trade_date(trade_date_str: str) -> str:
    """统一为 ``YYYY-MM-DD``，兼容 ``YYYY/MM/DD``。"""
    return pd.Timestamp(str(trade_date_str).strip().replace("/", "-")).strftime("%Y-%m-%d")


def normalize_minute_wide(df: pd.DataFrame | None, fields: list[str]) -> pd.DataFrame:
    """
    将米筐 1m 宽表整理为便于横向 concat 的形态。
    MultiIndex 列时：保证一级为合约、二级为字段（与 stack 前 update_rqMinPrice 一致）。
    """
    if df is None:
        return pd.DataFrame()
    if df.empty:
        return df.copy()

    field_set = set(fields)
    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex) and out.columns.nlevels == 2:
        top = out.columns[0][0]
        if top in field_set:
            out = out.swaplevel(axis=1).sort_index(axis=1, level=0)
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
        out = out.sort_index(axis=1)
        return out

    # 长表或单级列：尽量不破坏，仅去重列
    if out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
    return out


def fetch_trade_day_1m_bars_with_fallback(
    order_book_ids: list[str],
    trade_date_str: str,
) -> tuple[pd.DataFrame, list[str], None]:
    """
    单日 1m：拉取 OHLCV + total_turnover（不含 num_trades）。

    Returns:
        (规整后的宽表面板, 实际使用的 fields, None 占位与历史接口兼容)
    """
    if not order_book_ids:
        return pd.DataFrame(), list(CURRENT_MINUTE_FIELDS_FULL), None

    d = resolve_cn_trade_date(trade_date_str)
    fields = list(CURRENT_MINUTE_FIELDS_FULL)
    raw: Any = rq.get_price(
        order_book_ids,
        start_date=d,
        end_date=d,
        frequency="1m",
        fields=fields,
        expect_df=True,
        market="cn",
    )
    if raw is None:
        raise RuntimeError("get_price 返回 None")
    if isinstance(raw, pd.DataFrame) and raw.empty:
        raise RuntimeError("get_price 返回空表")
    norm = normalize_minute_wide(raw, fields)
    return norm, fields, None
