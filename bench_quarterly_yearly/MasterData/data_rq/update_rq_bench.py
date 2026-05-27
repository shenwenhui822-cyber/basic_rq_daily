"""
兼容入口：核心逻辑在 ``rq_history_backfill/backfill_rq_bench.py``。

日更请用: ``python rq_daily_update/update_rq_bench.py``
历史请用: ``python rq_history_backfill/backfill_rq_bench.py --start ... --end ...``
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_LIB = os.path.join(_ROOT, "lib")
for _p in (_ROOT, _LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rq_history_backfill.backfill_rq_bench import (  # noqa: E402
    BENCH_WIND_TO_RQ,
    create_indexes_rq_bench,
    update_rq_bench,
)

__all__ = ["BENCH_WIND_TO_RQ", "create_indexes_rq_bench", "update_rq_bench"]
