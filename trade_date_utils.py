# -*- coding: utf-8 -*-
"""
交易日工具：从 MongoDB ``economic.trade_dates`` 读取。

日更脚本默认取 **T-1**（严格早于「今天」的最近一个交易日）。
默认连接别名 ``local``（见 ``mongo_connect.py``）；可用环境变量 ``MONGO_TRADE_ALIAS`` 覆盖。
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import pandas as pd

_DEFAULT_MONGO_ALIAS = os.environ.get("MONGO_TRADE_ALIAS", "local")


def norm_trade_date_str(s: str) -> str:
    """统一为 YYYY-MM-DD。"""
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime("%Y-%m-%d")


def _get_client(mongo_alias: str, client: Any | None):
    if client is not None:
        return client
    from mongo_connect import get_client

    return get_client(mongo_alias)


def is_trade_day(
    day: str,
    *,
    mongo_alias: str = _DEFAULT_MONGO_ALIAS,
    client: Any | None = None,
) -> bool:
    """判断 ``day`` 是否在 ``economic.trade_dates`` 中。"""
    d = norm_trade_date_str(day)
    c = _get_client(mongo_alias, client)
    col = c.economic.trade_dates
    for variant in (d, d.replace("-", "/")):
        if col.find_one({"trade_date": variant}, {"_id": 1}):
            return True
    return False


def previous_trade_date(
    *,
    mongo_alias: str = _DEFAULT_MONGO_ALIAS,
    client: Any | None = None,
    fmt: str = "%Y-%m-%d",
    as_of: date | None = None,
) -> str:
    """返回严格早于 ``as_of``（默认今天）的最近交易日字符串。"""
    ref = norm_trade_date_str((as_of or date.today()).isoformat())
    c = _get_client(mongo_alias, client)
    doc = c.economic.trade_dates.find_one(
        {"trade_date": {"$lt": ref}},
        {"_id": 0, "trade_date": 1},
        sort=[("trade_date", -1)],
    )
    if not doc:
        raise ValueError(f"economic.trade_dates 中无早于 {ref} 的交易日")
    return pd.Timestamp(str(doc["trade_date"]).replace("/", "-")).strftime(fmt)


def parse_explicit_date_arg(arg: str, *, fmt: str = "%Y-%m-%d") -> str:
    """解析 ``--date``：支持 YYYYMMDD、YYYY/MM/DD、YYYY-MM-DD。"""
    raw = str(arg).strip()
    if len(raw) == 8 and raw.isdigit():
        ts = pd.Timestamp(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    else:
        ts = pd.Timestamp(raw.replace("/", "-"))
    return ts.strftime(fmt)


def parse_start_end_range(start: str, end: str, *, fmt: str = "%Y-%m-%d") -> tuple[str, str]:
    """解析 ``--start`` / ``--end`` 并校验起止顺序。"""
    start_s = parse_explicit_date_arg(start, fmt=fmt)
    end_s = parse_explicit_date_arg(end, fmt=fmt)
    if start_s > end_s:
        raise ValueError(f"起始日 {start_s} 不能晚于结束日 {end_s}")
    return start_s, end_s


def mongo_trade_date_range(start: str, end: str) -> dict[str, str]:
    """Mongo ``economic.trade_dates`` 查询用 ``{'$gte': ..., '$lte': ...}``。"""
    start_s, end_s = parse_start_end_range(start, end)
    return {"$gte": start_s, "$lte": end_s}


def list_trade_dates(
    start: str,
    end: str,
    *,
    mongo_alias: str = _DEFAULT_MONGO_ALIAS,
    client: Any | None = None,
) -> list[str]:
    """返回 ``[start, end]`` 内全部交易日（升序，``YYYY-MM-DD``）。"""
    c = _get_client(mongo_alias, client)
    cursor = c.economic.trade_dates.find(
        {"trade_date": mongo_trade_date_range(start, end)},
        {"_id": 0, "trade_date": 1},
    ).sort("trade_date", 1)
    return [norm_trade_date_str(d["trade_date"]) for d in cursor]
