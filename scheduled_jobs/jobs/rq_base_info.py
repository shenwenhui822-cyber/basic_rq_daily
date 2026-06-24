# -*- coding: utf-8 -*-
"""调用 rq_daily_update/update_rqbaseInfo（写入上一交易日，date: YYYY-MM-DD）。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scheduled_jobs.jobs.base import JobResult
from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.notify.email import DATE_FMT_DB
from trade_date_utils import is_trade_day, now_shanghai, previous_trade_date, today_shanghai

SCHEDULER_JOB_KEY = "rq_base_info"


def run() -> JobResult:
    from rq_paths import bootstrap

    bootstrap(str(_ROOT / "rq_daily_update" / "update_rqbaseInfo.py"))
    from rq_daily_update.update_rqbaseInfo import update_rqbaseInfo

    run_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S")
    today = today_shanghai().isoformat()

    mongo_alias = mongo_trade_alias()
    if not is_trade_day(today, mongo_alias=mongo_alias):
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=True,
            skipped=True,
            message=f"今天 {today} 不是交易日，已跳过。",
            detail={"run_at": run_at, "today": today},
        )

    target = previous_trade_date(mongo_alias=mongo_alias, fmt=DATE_FMT_DB)
    ok = update_rqbaseInfo(target, mongo_alias=mongo_alias)

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
            "collection": "basic_rq.rq_base_info",
        },
    )
