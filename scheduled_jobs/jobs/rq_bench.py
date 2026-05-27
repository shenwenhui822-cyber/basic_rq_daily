# -*- coding: utf-8 -*-
"""调用 rq_daily_update/update_rq_bench（写入上一交易日 rq_bench）。"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult
from scheduled_jobs.notify.email import DATE_FMT_DB

SCHEDULER_JOB_KEY = "rq_bench"


def run() -> JobResult:
    from rq_paths import bootstrap

    bootstrap(str(_ROOT / "rq_daily_update" / "update_rq_bench.py"), backfill=True)
    from rq_history_backfill.backfill_rq_bench import create_indexes_rq_bench, update_rq_bench
    from trade_date_utils import is_trade_day, previous_trade_date
    from usedbdef import get_client

    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = date.today().isoformat()
    mongo_alias = mongo_trade_alias()
    mongo_db = "basic_rq"
    client = get_client(mongo_alias)

    if not is_trade_day(today, mongo_alias=mongo_alias, client=client):
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=True,
            skipped=True,
            message=f"今天 {today} 不是交易日，已跳过。",
            detail={"run_at": run_at, "today": today},
        )

    target = previous_trade_date(mongo_alias=mongo_alias, client=client, fmt=DATE_FMT_DB)
    create_indexes_rq_bench(mongo_alias=mongo_alias, mongo_db=mongo_db, client=client)
    ok = update_rq_bench(
        target,
        mongo_alias=mongo_alias,
        mongo_db=mongo_db,
        client=client,
    )

    if ok:
        msg = f"写入上一交易日 {target}（date 格式 {DATE_FMT_DB}）"
    else:
        msg = f"更新失败，目标交易日 {target}"

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=ok,
        skipped=False,
        message=msg,
        detail={
            "run_at": run_at,
            "today": today,
            "target_date": target,
            "collection": "basic_rq.rq_bench",
        },
    )
