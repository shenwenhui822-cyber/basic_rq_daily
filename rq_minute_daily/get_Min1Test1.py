"""
1 分钟 K 线：``rq.get_price(..., frequency='1m')`` 封装与宽表规整。

使用前需已由调用方执行 ``rqdatac.init``。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import rqdatac as rq

# 与 update_rqMinPrice.MINUTE_PRICE_FIELDS 一致（全量）；拉取失败时按子集降级
CURRENT_MINUTE_FIELDS_FULL = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "total_turnover",
    "num_trades",
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
    单日 1m：先按全字段请求，失败则依次减少字段（如去掉 num_trades）。

    Returns:
        (规整后的宽表面板, 实际使用的 fields, None 占位与历史接口兼容)
    """
    if not order_book_ids:
        return pd.DataFrame(), list(CURRENT_MINUTE_FIELDS_FULL), None

    d = resolve_cn_trade_date(trade_date_str)
    candidates: list[list[str]] = [
        list(CURRENT_MINUTE_FIELDS_FULL),
        [f for f in CURRENT_MINUTE_FIELDS_FULL if f != "num_trades"],
        ["open", "high", "low", "close", "volume", "total_turnover"],
    ]

    last_err: Exception | None = None
    for fields in candidates:
        try:
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
                continue
            if isinstance(raw, pd.DataFrame) and raw.empty:
                last_err = RuntimeError("get_price 返回空表")
                continue
            norm = normalize_minute_wide(raw, fields)
            return norm, fields, None
        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise last_err
    raise RuntimeError("fetch_trade_day_1m_bars_with_fallback: 无可用的字段组合")
