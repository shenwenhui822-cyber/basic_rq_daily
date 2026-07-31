# -*- coding: utf-8 -*-
"""交易日将 basic_rq 9 表 + rq_minute 同日数据同步到远端 MongoDB。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult

SCHEDULER_JOB_KEY = "rq_sync_basic_rq"


def _sync_target_alias() -> str:
    return (
        os.environ.get("BASIC_RQ_SYNC_TARGET_ALIAS", "wonderwz203_19_rw").strip()
        or "wonderwz203_19_rw"
    )


def _sync_source_alias() -> str:
    return (
        os.environ.get("BASIC_RQ_SYNC_SOURCE_ALIAS", mongo_trade_alias()).strip()
        or mongo_trade_alias()
    )


def _sync_with_minute() -> bool:
    """默认同步分钟线；``BASIC_RQ_SYNC_SKIP_MINUTE=1`` 可跳过。"""
    return os.environ.get("BASIC_RQ_SYNC_SKIP_MINUTE", "").strip() not in (
        "1",
        "true",
        "True",
        "yes",
        "YES",
    )


def run() -> JobResult:
    from rq_paths import bootstrap
    from trade_date_utils import is_trade_day, now_shanghai, previous_trade_date, today_shanghai

    bootstrap(str(_ROOT / "rq_daily_update" / "sync_basic_rq_to_remote.py"), daily=True)
    from sync_basic_rq_to_remote import (
        BASIC_RQ_COLLECTIONS,
        sync_basic_rq_for_dates,
    )

    run_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S")
    today = today_shanghai().isoformat()
    source_alias = _sync_source_alias()
    target_alias = _sync_target_alias()
    trade_alias = mongo_trade_alias()
    with_minute = _sync_with_minute()

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
        with_minute=with_minute,
    )

    summary_lines = [f"同步交易日 {target}：源 {source_alias} -> 目标 {target_alias}"]
    for name in BASIC_RQ_COLLECTIONS:
        stats = result["per_date"][target][name]
        summary_lines.append(
            f"  {name}: 源 {stats['source']}，删 {stats['deleted']}，写 {stats['inserted']}"
        )

    minute_detail = result.get("minute") or {}
    if with_minute and minute_detail.get("per_date"):
        mstats = minute_detail["per_date"].get(target) or {}
        coll = mstats.get("collection", "rq_minute_none_YYYY")
        summary_lines.append(
            f"  rq_minute.{coll}: 源 {mstats.get('source', 0)}，"
            f"删 {mstats.get('deleted', 0)}，写 {mstats.get('inserted', 0)}"
        )

    ok = bool(result["ok"])
    parts = [f"{len(BASIC_RQ_COLLECTIONS)} 张 basic_rq 表"]
    if with_minute:
        parts.append("rq_minute 同日分钟线")
    msg = (
        f"已同步上一交易日 {target}（{' + '.join(parts)}）"
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
            "with_minute": with_minute,
            "minute": minute_detail,
            "errors": result["errors"],
            "summary": "\n".join(summary_lines),
        },
    )
