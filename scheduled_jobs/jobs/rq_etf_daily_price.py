# -*- coding: utf-8 -*-
"""日更 ETF 清单日线（写入上一交易日 rq_daily_price_none）。

目标日规则（与日历「昨天」不同）：
  使用 ``previous_trade_date`` = 严格早于「今天」的最近交易日。
  - 周一～周五（交易日）：写上一交易日
  - 周六/周日/节假日：仍执行，写最近已结束的交易日
    例：周六 → 周五（不会写成下周一）

去重：若目标日清单 ETF 已在 Mongo 完整落库（条数 ≥ 清单数），则跳过，
  避免周六写周五后，周日/周一再为同一天重复跑。

本任务 ``only_on_trade_day=False``，非交易日第一天（如周六）也会跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult
from scheduled_jobs.notify.email import DATE_FMT_DB

SCHEDULER_JOB_KEY = "rq_etf_daily_price"


def run() -> JobResult:
    from rq_paths import bootstrap

    bootstrap(str(_ROOT / "ETF_price_date" / "load_etf_daily_price.py"))
    from trade_date_utils import (
        is_trade_day,
        now_shanghai,
        previous_trade_date,
        today_shanghai,
    )
    from ETF_price_date.load_etf_daily_price import (
        DEFAULT_ETF_CODES,
        etf_day_already_complete,
        etf_day_row_count,
        load_etf_daily_prices,
    )

    run_at = now_shanghai().strftime("%Y-%m-%d %H:%M:%S")
    today = today_shanghai().isoformat()
    mongo_alias = mongo_trade_alias()
    codes = list(DEFAULT_ETF_CODES)

    # 严格早于今天的最近交易日：周六→周五，周一→上周五，周二→周一
    target = previous_trade_date(mongo_alias=mongo_alias, fmt=DATE_FMT_DB)
    today_is_trade = is_trade_day(today, mongo_alias=mongo_alias)

    existing, expect, _ = etf_day_row_count(
        target, codes, mongo_alias=mongo_alias
    )
    if etf_day_already_complete(target, codes, mongo_alias=mongo_alias):
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=True,
            skipped=True,
            message=(
                f"目标日 {target} 已有完整 ETF 数据 "
                f"（{existing}/{expect}），跳过重复拉取。"
            ),
            detail={
                "run_at": run_at,
                "today": today,
                "today_is_trade_day": today_is_trade,
                "target_date": target,
                "existing_rows": existing,
                "expected_rows": expect,
                "collection": "basic_rq.rq_daily_price_none",
            },
        )

    result = load_etf_daily_prices(
        trade_dates=[target],
        etf_codes=codes,
        mongo_alias=mongo_alias,
        dry_run=False,
    )
    ok = bool(result.get("ok"))
    inserted = int(result.get("inserted") or 0)
    errors = result.get("errors") or []

    if ok:
        msg = (
            f"写入上一交易日 {target} 共 {inserted} 条 ETF "
            f"（今天 {today}{'是' if today_is_trade else '不是'}交易日）"
        )
    else:
        msg = f"更新失败，目标交易日 {target}；errors={errors}"

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=ok,
        skipped=False,
        message=msg,
        detail={
            "run_at": run_at,
            "today": today,
            "today_is_trade_day": today_is_trade,
            "target_date": target,
            "target_rule": "previous_trade_date；已完整落库则跳过",
            "existing_rows_before": existing,
            "n_etf": len(codes),
            "inserted": inserted,
            "errors": errors,
            "collection": "basic_rq.rq_daily_price_none",
        },
    )
