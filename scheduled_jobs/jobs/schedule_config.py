# -*- coding: utf-8 -*-
"""
定时任务时间表（增删任务只改本文件）。

格式与外部调度配置一致：
    (时刻 "HH:MM", 任务名, {"scheduler_job_key": "..."})
"""
from __future__ import annotations

from typing import Any

# (触发时刻, 任务显示名, 参数)
SCHEDULE_ENTRIES: list[tuple[str, str, dict[str, Any]]] = [
    ("07:03", "update_rq_base_info", {"scheduler_job_key": "rq_base_info"}),
    ("07:05", "update_rq_basic_financial", {"scheduler_job_key": "rq_basic_financial"}),
    ("07:07", "update_rq_in_index", {"scheduler_job_key": "rq_in_index"}),
    ("07:10", "update_rq_SWL2", {"scheduler_job_key": "rq_swl2"}),
    ("07:13", "update_rq_SWL2_price", {"scheduler_job_key": "rq_swl2_price"}),
    ("07:15", "update_rq_bench", {"scheduler_job_key": "rq_bench"}),
    ("07:18", "update_rqDailyPrice", {"scheduler_job_key": "rq_daily_price"}),
    ("07:20", "update_rq_quarterly", {"scheduler_job_key": "rq_quarterly"}),
    ("07:25", "update_rq_yearly", {"scheduler_job_key": "rq_yearly"}),
    ("07:30", "update_rqMinPrice", {"scheduler_job_key": "rq_minute"}),
    ("07:40", "sync_basic_rq_to_remote", {"scheduler_job_key": "rq_sync_basic_rq"}),
    # ("07:45", "check_historical_data", {"scheduler_job_key": "rq_data_quality_check"}),
    # (
    #     "10:00",
    #     "backfill_rqMinPrice",
    #     {
    #         "scheduler_job_key": "rq_minute_backfill",
    #         "only_on_trade_day": False,
    #     },
    # ),
]


def parse_hhmm(hhmm: str) -> tuple[int, int]:
    """解析 "09:05" -> (9, 5)。"""
    parts = str(hhmm).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"无效时刻格式: {hhmm!r}，应为 HH:MM")
    return int(parts[0]), int(parts[1])
