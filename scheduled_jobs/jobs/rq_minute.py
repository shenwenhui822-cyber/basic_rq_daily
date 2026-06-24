# -*- coding: utf-8 -*-
"""每日 9:35 拉取上一交易日 1 分钟线（依赖 rq_base_info）。"""
from __future__ import annotations

import sys
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
from scheduled_jobs.notify.email import DATE_FMT_DB

SCHEDULER_JOB_KEY = "rq_minute"


def run() -> JobResult:
    from minute_mongo import MINUTE_DB, minute_collection_for_date
    from trade_date_utils import is_trade_day, now_shanghai, previous_trade_date, today_shanghai
    from update_rqMinPrice import update_rqMinPrice
    from usedbdef import get_client

    run_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S")
    today = today_shanghai().isoformat()
    mongo_alias = mongo_trade_alias()
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
    collection = minute_collection_for_date(target)
    ok = update_rqMinPrice(
        today_str=target,
        mongo_alias=mongo_alias,
        minute_db=MINUTE_DB,
        minute_collection=collection,
    )

    if ok:
        msg = f"写入上一交易日 {target} → {MINUTE_DB}.{collection}"
    else:
        msg = f"更新失败，目标交易日 {target}（需先完成 T-1 rq_base_info）"

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=ok,
        skipped=False,
        message=msg,
        detail={
            "run_at": run_at,
            "today": today,
            "target_date": target,
            "collection": f"{MINUTE_DB}.{collection}",
        },
    )
