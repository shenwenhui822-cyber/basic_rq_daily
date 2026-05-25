# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class JobResult:
    """单次任务执行结果（供邮件与 HTTP 返回）。"""

    job_id: str
    ok: bool
    skipped: bool = False
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "skipped": self.skipped,
            "message": self.message,
            "detail": self.detail,
        }


JobCallable = Callable[[], JobResult]


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    description: str
    schedule_time: str
    """计划触发时刻，如 09:05。"""
    cron_hour: int
    cron_minute: int
    runner: JobCallable
    task_name: str = ""
    """schedule_config 中的任务显示名。"""
    only_on_trade_day: bool = True
    """为 True 时仅当「今天为交易日」才执行 runner。"""
