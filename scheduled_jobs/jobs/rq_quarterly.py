# -*- coding: utf-8 -*-
"""每日 9:20 拉取季报（前一交易日），含 backfill 统计。"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult
from scheduled_jobs.notify.email import DATE_FMT_DB

SCHEDULER_JOB_KEY = "rq_quarterly"


def _unpack_update_result(result) -> tuple[bool, int]:
    """兼容 update_rq_* 返回 bool 或 (bool, int)。"""
    if isinstance(result, tuple):
        return bool(result[0]), int(result[1] or 0)
    return bool(result), 0


def run() -> JobResult:
    # 直接把 bench_quarterly_yearly 目录加入 sys.path，使 MasterData 可作为顶层包导入
    _BENCH_DIR = _ROOT / "bench_quarterly_yearly"
    if str(_BENCH_DIR) not in sys.path:
        sys.path.insert(0, str(_BENCH_DIR))

    from MasterData.data_rq.update_rq_quarterly_yearly_bench import update_rq_quarterly
    from MasterData.data_rq.ffill_rq_quarterly_yearly_to_universe import ffill_fin_to_universe
    from MasterData.data_rq.ffill_rq_quarterly_yearly_missing import (
        _load_all_trade_days_sorted,
        _trade_days_in_sorted,
    )
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

    pre = previous_trade_date(mongo_alias=mongo_alias, client=client, fmt=DATE_FMT_DB)
    # today_for_update = 下一交易日
    all_td = _load_all_trade_days_sorted(client)
    trade_list = _trade_days_in_sorted(all_td, pre, pre)
    if not trade_list:
        return JobResult(job_id=SCHEDULER_JOB_KEY, ok=False, message=f"无法找到 {pre} 的交易日信息")

    # 简化：取 pre 的下一交易日
    idx = all_td.index(pre) if pre in all_td else -1
    today_for_update = all_td[idx + 1] if idx + 1 < len(all_td) else pre

    # 1. 拉取真实 PIT
    ok, pulled = _unpack_update_result(
        update_rq_quarterly(
            pre,
            today_for_update,
            mongo_alias=mongo_alias,
            mongo_db=mongo_db,
            target_coll="rq_quarterly",
        )
    )

    if not ok:
        return JobResult(
            job_id=SCHEDULER_JOB_KEY,
            ok=False,
            message=f"季报 PIT 更新失败 pre={pre}",
            detail={"run_at": run_at, "target_date": pre},
        )

    # 2. 做近期 backfill（最近 30 个交易日）以统计拟补
    start_back = all_td[max(0, idx - 30)] if idx > 0 else pre
    backfill_days = _trade_days_in_sorted(all_td, start_back, pre)

    db = client[mongo_db]
    di, backfilled_rows = ffill_fin_to_universe(
        db["rq_quarterly"],
        db["rq_base_info"],
        backfill_days,
        dry_run=False,
        verbose=False,
        insert_chunk=2000,
        progress_every=0,
        mongo_attempts=3,
        mongo_delay=0.5,
    )

    total_inserted = pulled + backfilled_rows

    msg = (
        f"季报更新完成 pre={pre} | "
        f"拉取 {pulled} 条 | 拟补 {backfilled_rows} 条 | 总插入 {total_inserted} 条"
    )

    return JobResult(
        job_id=SCHEDULER_JOB_KEY,
        ok=True,
        skipped=False,
        message=msg,
        detail={
            "run_at": run_at,
            "target_date": pre,
            "pulled": pulled,
            "backfilled_days": di,
            "backfilled_rows": backfilled_rows,
            "total_inserted": total_inserted,
            "collection": "basic_rq.rq_quarterly",
        },
    )
