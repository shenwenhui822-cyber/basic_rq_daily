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
    ("09:05", "update_rq_basic_financial", {"scheduler_job_key": "rq_basic_financial"}),
    ("09:20", "update_rq_base_info", {"scheduler_job_key": "rq_base_info"}),
    ("09:25", "update_rq_in_index", {"scheduler_job_key": "rq_in_index"}),
    # 示例：后续可继续添加
    # ("09:00", "auto_import_htzq_ht1_capital_mail", {"scheduler_job_key": "htzq_ht1_capital"}),
]


def parse_hhmm(hhmm: str) -> tuple[int, int]:
    """解析 "09:05" -> (9, 5)。"""
    parts = str(hhmm).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"无效时刻格式: {hhmm!r}，应为 HH:MM")
    return int(parts[0]), int(parts[1])
