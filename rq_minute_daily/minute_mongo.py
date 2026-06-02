"""分钟线 Mongo 集合命名（按交易日年份分表）。"""
from __future__ import annotations

import pandas as pd

MINUTE_DB = "rq_minute"
MINUTE_COLLECTION_PREFIX = "rq_minute_none_"


def minute_collection_for_date(trade_date_str: str) -> str:
    """按交易日年份返回集合名，如 ``rq_minute_none_2025``。"""
    normalized = str(trade_date_str).strip().replace("/", "-")
    year = pd.Timestamp(normalized).year
    return f"{MINUTE_COLLECTION_PREFIX}{year}"
