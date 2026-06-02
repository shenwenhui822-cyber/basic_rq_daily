# -*- coding: utf-8 -*-
"""10:00 启动，在 10:00–14:40 内按自然月倒序批量补分钟线历史。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MINUTE_DIR = _ROOT / "rq_minute_daily"
for _p in (_ROOT, _ROOT / "lib", _MINUTE_DIR):
    _s = str(_p)
    if _s in sys.path:
        sys.path.remove(_s)
    sys.path.insert(0, _s)

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult

SCHEDULER_JOB_KEY = "rq_minute_backfill"


def run() -> JobResult:
    from backfill_rq_minute import DEFAULT_BACKFILL_START, run_minute_monthly_backfill

    mongo_alias = mongo_trade_alias()
    start = os.environ.get("RQ_MINUTE_BACKFILL_START", DEFAULT_BACKFILL_START).strip()
    force = os.environ.get("RQ_MINUTE_BACKFILL_FORCE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    result = run_minute_monthly_backfill(
        mongo_alias=mongo_alias,
        start=start or DEFAULT_BACKFILL_START,
        enforce_window=not force,
        skip_existing=True,
        now=datetime.now(),
    )

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=bool(result.get("ok")),
        skipped=bool(result.get("skipped")),
        message=str(result.get("message", "")),
        detail={k: v for k, v in result.items() if k not in ("ok", "skipped", "message")},
    )
