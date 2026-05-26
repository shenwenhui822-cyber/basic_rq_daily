# -*- coding: utf-8 -*-
"""由 schedule_config.SCHEDULE_ENTRIES 构建任务注册表。"""
from __future__ import annotations

import traceback

from loguru import logger

from scheduled_jobs.jobs.base import JobResult, JobSpec
from scheduled_jobs.jobs.schedule_config import SCHEDULE_ENTRIES, parse_hhmm
from scheduled_jobs.notify.email import notify_configured, send_task_email

# scheduler_job_key -> (runner, 描述)
_RUNNER_MAP: dict[str, tuple] = {}


def _ensure_runner_map() -> None:
    if _RUNNER_MAP:
        return
    from scheduled_jobs.jobs import (
        rq_base_info,
        rq_basic_financial,
        rq_in_index,
        rq_swl2,
    )

    _RUNNER_MAP.update(
        {
            rq_basic_financial.SCHEDULER_JOB_KEY: (
                rq_basic_financial.run,
                "rq_basic_financial：交易日更新上一交易日财务字段",
            ),
            rq_base_info.SCHEDULER_JOB_KEY: (
                rq_base_info.run,
                "rq_base_info：交易日更新上一交易日基础信息",
            ),
            rq_in_index.SCHEDULER_JOB_KEY: (
                rq_in_index.run,
                "rq_base_index：交易日更新上一交易日宽基成分（依赖 rq_base_info）",
            ),
            rq_swl2.SCHEDULER_JOB_KEY: (
                rq_swl2.run,
                "rq_daily_indusSWL2：交易日更新上一交易日申万二级行业成分",
            ),
        }
    )


def run_job(spec: JobSpec, *, notify: bool = True) -> JobResult:
    """执行单个任务，并按结果发邮件。"""
    try:
        result = spec.runner()
    except Exception as e:
        logger.exception("任务 {} 异常", spec.job_id)
        result = JobResult(
            job_id=spec.job_id,
            ok=False,
            message=str(e),
            detail={"traceback": traceback.format_exc()},
        )

    if notify and not result.skipped:
        if not notify_configured():
            logger.warning("任务 {} 完成但未发邮件：ALPHA_NOTIFY_* 未载入进程环境", spec.job_id)
        else:
            title = (
                f"[{spec.job_id}] 执行成功"
                if result.ok
                else f"[{spec.job_id}] 执行失败"
            )
            body = (
                f"{spec.description}\n"
                f"计划时刻：{spec.schedule_time}\n\n"
                f"{result.message}\n\n详情：{result.detail}"
            )
            send_task_email(title, body)

    return result


def _build_registry() -> list[JobSpec]:
    _ensure_runner_map()
    specs: list[JobSpec] = []
    for hhmm, task_name, opts in SCHEDULE_ENTRIES:
        job_key = str(opts.get("scheduler_job_key", "")).strip()
        if not job_key:
            raise ValueError(f"任务 {task_name!r} 缺少 scheduler_job_key")
        if job_key not in _RUNNER_MAP:
            raise ValueError(f"未注册 runner: {job_key!r}（任务 {task_name!r}）")
        runner, description = _RUNNER_MAP[job_key]
        hour, minute = parse_hhmm(hhmm)
        specs.append(
            JobSpec(
                job_id=job_key,
                task_name=task_name,
                description=description,
                schedule_time=hhmm,
                cron_hour=hour,
                cron_minute=minute,
                runner=runner,
                only_on_trade_day=True,
            )
        )
    return specs


JOB_REGISTRY: list[JobSpec] = _build_registry()


def resolve_run_targets(job_param: str | None) -> list[JobSpec] | None:
    """
    解析 /run 的 job 参数。

    - 未传 job：返回 None（不执行）
    - job=all：按 SCHEDULE_ENTRIES 顺序返回全部任务
    - 其它：单个 scheduler_job_key
    """
    if job_param is None or not str(job_param).strip():
        return None
    key = str(job_param).strip()
    if key.lower() == "all":
        return list(JOB_REGISTRY)
    matches = [s for s in JOB_REGISTRY if s.job_id == key]
    return matches if matches else []
