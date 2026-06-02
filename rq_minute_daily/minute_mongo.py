"""分钟线 Mongo 集合命名与索引。"""
from __future__ import annotations

from typing import Any

import pandas as pd
import pymongo

MINUTE_DB = "rq_minute"
MINUTE_COLLECTION_PREFIX = "rq_minute_none_"

# 判定某日已落库的最小文档数（全市场 1m 约 120 万行/日，阈值留余量）
MINUTE_DAY_MIN_ROWS = 1000


def minute_collection_for_date(trade_date_str: str) -> str:
    """按交易日年份返回集合名，如 ``rq_minute_none_2025``。"""
    normalized = str(trade_date_str).strip().replace("/", "-")
    year = pd.Timestamp(normalized).year
    return f"{MINUTE_COLLECTION_PREFIX}{year}"


def ensure_minute_collection_indexes(table: Any) -> None:
    """新建或写入前确保 ``rq_minute_none_YYYY`` 索引。"""
    table.create_index(
        [
            ("date", pymongo.ASCENDING),
            ("time", pymongo.ASCENDING),
            ("code_rq", pymongo.ASCENDING),
        ],
        unique=True,
        background=True,
        name="uniq_date_time_code_rq",
    )
    table.create_index(
        [("date", pymongo.ASCENDING)],
        background=True,
        name="idx_date",
    )
    table.create_index(
        [("code_rq", pymongo.ASCENDING), ("date", pymongo.ASCENDING)],
        background=True,
        name="idx_code_rq_date",
    )


def _norm_minute_day(s: str) -> str:
    return pd.Timestamp(str(s).strip().replace("/", "-")).strftime("%Y-%m-%d")


def list_minute_collections(client: Any, *, minute_db: str = MINUTE_DB) -> list[str]:
    db = client[minute_db]
    return sorted(
        n for n in db.list_collection_names() if n.startswith(MINUTE_COLLECTION_PREFIX)
    )


def find_latest_minute_trade_date(
    client: Any,
    *,
    minute_db: str = MINUTE_DB,
) -> str | None:
    """跨所有 ``rq_minute_none_*`` 集合取最大 ``date``。"""
    latest: str | None = None
    for coll_name in list_minute_collections(client, minute_db=minute_db):
        doc = client[minute_db][coll_name].find_one(
            {},
            sort=[("date", pymongo.DESCENDING)],
            projection={"_id": 0, "date": 1},
        )
        if not doc or not doc.get("date"):
            continue
        d = _norm_minute_day(doc["date"])
        if latest is None or d > latest:
            latest = d
    return latest


def minute_day_has_data(
    client: Any,
    trade_date: str,
    *,
    minute_db: str = MINUTE_DB,
    min_rows: int = MINUTE_DAY_MIN_ROWS,
) -> bool:
    day = _norm_minute_day(trade_date)
    coll = minute_collection_for_date(day)
    if coll not in client[minute_db].list_collection_names():
        return False
    n = client[minute_db][coll].count_documents({"date": day})
    return n >= min_rows
