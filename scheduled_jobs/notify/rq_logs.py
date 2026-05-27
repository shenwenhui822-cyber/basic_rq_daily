# -*- coding: utf-8 -*-
"""定时任务运行结果写入 MongoDB ``basic_rq_logs.rq_logs``。"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from loguru import logger

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.base import JobResult, JobSpec

LOG_DB = os.environ.get("RQ_LOGS_DB", "basic_rq_logs").strip() or "basic_rq_logs"
LOG_COLLECTION = os.environ.get("RQ_LOGS_COLLECTION", "rq_logs").strip() or "rq_logs"


def _logs_collection():
    from mongo_connect import get_client

    alias = mongo_trade_alias()
    client = get_client(alias)
    return client[LOG_DB][LOG_COLLECTION]


def write_job_log(spec: JobSpec, result: JobResult, *, notify: bool) -> str | None:
    """
    将单次任务执行结果写入 ``basic_rq_logs.rq_logs``（在发送邮件之前调用）。

    返回 inserted_id 字符串；写入失败时记录日志并返回 None。
    """
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc: dict[str, Any] = {
        "run_at": run_at,
        "job_id": result.job_id,
        "task_name": spec.task_name,
        "schedule_time": spec.schedule_time,
        "description": spec.description,
        "ok": result.ok,
        "skipped": result.skipped,
        "message": result.message,
        "detail": result.detail,
        "mongo_alias": mongo_trade_alias(),
        "notify": notify,
        "email_pending": bool(notify and not result.skipped),
    }
    try:
        coll = _logs_collection()
        inserted = coll.insert_one(doc)
        logger.info(
            "rq_logs 已写入 {}.{} _id={} job={}",
            LOG_DB,
            LOG_COLLECTION,
            inserted.inserted_id,
            result.job_id,
        )
        return str(inserted.inserted_id)
    except Exception:
        logger.exception(
            "写入 {}.{} 失败 job_id={}",
            LOG_DB,
            LOG_COLLECTION,
            result.job_id,
        )
        return None


def ensure_rq_logs_indexes() -> None:
    """可选：为查询建索引（服务启动时调用一次）。"""
    try:
        coll = _logs_collection()
        coll.create_index([("run_at", -1)], background=True)
        coll.create_index([("job_id", 1), ("run_at", -1)], background=True)
    except Exception:
        logger.exception("rq_logs 索引创建失败")
