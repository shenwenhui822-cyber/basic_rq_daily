# -*- coding: utf-8 -*-
"""交易日 08:45 检查最近两个交易日的 basic_rq / rq_minute 数据质量。"""
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
from scheduled_jobs.notify.email import DATE_FMT_DB

SCHEDULER_JOB_KEY = "rq_data_quality_check"
RECENT_TRADE_DAYS = 2


def _recent_trade_day_range(
    *,
    as_of: str,
    n_days: int,
    mongo_alias: str,
) -> tuple[str, str]:
    from trade_date_utils import previous_trade_date

    cursor = date.fromisoformat(as_of)
    days: list[str] = []
    for _ in range(n_days):
        d_str = previous_trade_date(
            as_of=cursor,
            mongo_alias=mongo_alias,
            fmt=DATE_FMT_DB,
        )
        days.append(d_str)
        cursor = date.fromisoformat(d_str)
    days.reverse()
    return days[0], days[-1]


def run() -> JobResult:
    from check_historical_data.check_historical_data import run_check
    from trade_date_utils import is_trade_day

    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = date.today().isoformat()
    mongo_alias = os.environ.get(
        "DATA_QUALITY_MONGO_ALIAS",
        os.environ.get("MONGO_TRADE_ALIAS", "wonderwz27018_ro"),
    ).strip() or "wonderwz27018_ro"

    if not is_trade_day(today, mongo_alias=mongo_trade_alias()):
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=True,
            skipped=True,
            message=f"今天 {today} 不是交易日，已跳过。",
            detail={"run_at": run_at, "today": today},
        )

    check_start, check_end = _recent_trade_day_range(
        as_of=today,
        n_days=RECENT_TRADE_DAYS,
        mongo_alias=mongo_trade_alias(),
    )
    skip_minute = os.environ.get("DATA_QUALITY_SKIP_MINUTE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    report_dir = _ROOT / "check_historical_data" / "reports"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"scheduled_{stamp}.txt"

    try:
        check_result = run_check(
            mongo_alias=mongo_alias,
            start_override=check_start,
            end=check_end,
            report_path=report_path,
            verbose=False,
            skip_minute=skip_minute,
        )
    except Exception as e:
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=False,
            skipped=False,
            message=f"数据质量检查异常：{e}",
            detail={
                "run_at": run_at,
                "today": today,
                "check_start": check_start,
                "check_end": check_end,
                "mongo_alias": mongo_alias,
            },
        )

    conclusion = check_result.summary_lines[-1] if check_result.summary_lines else ""
    msg = (
        f"检查区间 {check_start} ~ {check_end}（最近 {RECENT_TRADE_DAYS} 个交易日）；"
        f"{conclusion}；耗时 {check_result.elapsed_seconds:.1f}s"
    )

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=check_result.passed,
        skipped=False,
        message=msg,
        detail={
            "run_at": run_at,
            "today": today,
            "check_start": check_start,
            "check_end": check_end,
            "recent_trade_days": RECENT_TRADE_DAYS,
            "passed": check_result.passed,
            "elapsed_seconds": round(check_result.elapsed_seconds, 1),
            "report_path": str(check_result.report_path),
            "issue_ids_path": str(check_result.issue_ids_path),
            "summary": check_result.summary_text,
            "mongo_alias": mongo_alias,
            "skip_minute": skip_minute,
        },
    )
