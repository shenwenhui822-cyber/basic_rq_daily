# -*- coding: utf-8 -*-
"""交易日 08:30 将 basic_rq 9 表 T-1 数据同步到远端 MongoDB。"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult

SCHEDULER_JOB_KEY = "rq_sync_basic_rq"


def _sync_target_alias() -> str:
    return (
        os.environ.get("BASIC_RQ_SYNC_TARGET_ALIAS", "114.80.62.203_rw").strip()
        or "114.80.62.203_rw"
    )


def _sync_source_alias() -> str:
    return (
        os.environ.get("BASIC_RQ_SYNC_SOURCE_ALIAS", mongo_trade_alias()).strip()
        or mongo_trade_alias()
    )


def run() -> JobResult:
    from rq_paths import bootstrap
    from trade_date_utils import is_trade_day, previous_trade_date

    bootstrap(str(_ROOT / "rq_daily_update" / "sync_basic_rq_to_remote.py"), daily=True)
    from sync_basic_rq_to_remote import (
        BASIC_RQ_COLLECTIONS,
        sync_basic_rq_for_dates,
    )

    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = date.today().isoformat()
    source_alias = _sync_source_alias()
    target_alias = _sync_target_alias()
    trade_alias = mongo_trade_alias()

    if not is_trade_day(today, mongo_alias=trade_alias):
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=True,
            skipped=True,
            message=f"今天 {today} 不是交易日，已跳过。",
            detail={"run_at": run_at, "today": today},
        )

    target = previous_trade_date(mongo_alias=trade_alias, fmt="%Y-%m-%d")
    result = sync_basic_rq_for_dates(
        [target],
        source_alias=source_alias,
        target_alias=target_alias,
    )

    summary_lines = [f"同步交易日 {target}：源 {source_alias} -> 目标 {target_alias}"]
    for name in BASIC_RQ_COLLECTIONS:
        stats = result["per_date"][target][name]
        summary_lines.append(
            f"  {name}: 源 {stats['source']}，删 {stats['deleted']}，写 {stats['inserted']}"
        )

    ok = bool(result["ok"])
    msg = (
        f"已同步上一交易日 {target} 共 {len(BASIC_RQ_COLLECTIONS)} 张表"
        if ok
        else f"同步 {target} 完成但有异常，见 errors"
    )

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=ok,
        skipped=False,
        message=msg,
        detail={
            "run_at": run_at,
            "today": today,
            "target_date": target,
            "source_alias": source_alias,
            "target_alias": target_alias,
            "collections": list(BASIC_RQ_COLLECTIONS),
            "errors": result["errors"],
            "summary": "\n".join(summary_lines),
        },
    )
