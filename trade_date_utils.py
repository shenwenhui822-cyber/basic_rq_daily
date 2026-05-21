# -*- coding: utf-8 -*-
"""
交易日工具：基于与本目录脚本同级的 ``trade_dates_all.csv``。

日更脚本默认取 **T-1**（严格早于「今天」的最近一个交易日），避免当日米筐数据晚间才稳定。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

_DEFAULT_CSV = Path(__file__).resolve().parent / "trade_dates_all.csv"


def _load_trade_dates(trade_dates_path: str | Path) -> pd.Series:
    path = Path(trade_dates_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到交易日文件: {path}")
    df = pd.read_csv(path)
    if "trade_date" not in df.columns:
        raise ValueError(f"{path} 须含 trade_date 列")
    return pd.to_datetime(df["trade_date"], errors="coerce").dropna()


def previous_trade_date(
    trade_dates_path: str | Path | None = None,
    *,
    fmt: str = "%Y-%m-%d",
    as_of: date | None = None,
) -> str:
    """
    返回严格早于 ``as_of``（默认今天）的最近交易日字符串。

    :param fmt: 输出格式，常用 ``%Y-%m-%d`` 或 ``%Y/%m/%d``
    """
    ref = as_of or date.today()
    s = _load_trade_dates(trade_dates_path or _DEFAULT_CSV)
    past = s[s.dt.date < ref]
    if past.empty:
        raise ValueError(f"trade_dates 中无早于 {ref} 的交易日")
    return pd.Timestamp(past.max()).strftime(fmt)


def parse_explicit_date_arg(arg: str, *, fmt: str = "%Y-%m-%d") -> str:
    """解析 ``--date``：支持 YYYYMMDD、YYYY/MM/DD、YYYY-MM-DD。"""
    raw = str(arg).strip()
    if len(raw) == 8 and raw.isdigit():
        ts = pd.Timestamp(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    else:
        ts = pd.Timestamp(raw.replace("/", "-"))
    return ts.strftime(fmt)
