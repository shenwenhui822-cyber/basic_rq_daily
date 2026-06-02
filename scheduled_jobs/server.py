# -*- coding: utf-8 -*-
"""HTTP 服务 + APScheduler，按注册表调度多个任务。"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.registry import JOB_REGISTRY, resolve_run_targets, run_job
from scheduled_jobs.notify.email import notify_configured
from scheduled_jobs.notify.rq_logs import ensure_rq_logs_indexes
from trade_date_utils import is_trade_day


def _should_run_today(spec) -> bool:
    if not spec.only_on_trade_day:
        return True
    alias = mongo_trade_alias()
    try:
        return is_trade_day(date.today().isoformat(), mongo_alias=alias)
    except Exception:
        logger.exception("判断交易日失败（mongo_alias={}）", alias)
        return False


# rq_minute_backfill 业务窗口（与 backfill_rq_minute.py 一致）
_MINUTE_BACKFILL_WINDOW_START = time(10, 0)
_MINUTE_BACKFILL_WINDOW_END = time(14, 40)
_STARTUP_CATCHUP_JOB_IDS = frozenset({"rq_minute_backfill"})


def _in_minute_backfill_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    t = now.time()
    return _MINUTE_BACKFILL_WINDOW_START <= t <= _MINUTE_BACKFILL_WINDOW_END


def _run_spec_background(
    spec,
    locks: dict[str, threading.Lock],
    *,
    notify: bool,
    reason: str,
) -> None:
    def _bg() -> None:
        lock = locks[spec.job_id]
        if not lock.acquire(blocking=False):
            logger.warning("{}：任务 {} 仍在执行，跳过", reason, spec.job_id)
            return
        try:
            if not _should_run_today(spec):
                logger.info("{}：任务 {} 今天不满足执行条件，跳过", reason, spec.job_id)
                return
            logger.info("{}：开始执行任务 {}", reason, spec.job_id)
            run_job(spec, notify=notify)
        finally:
            lock.release()

    threading.Thread(target=_bg, daemon=True).start()


def _startup_catchup_window_jobs(
    locks: dict[str, threading.Lock],
    *,
    notify: bool,
) -> None:
    """
    服务启动时：若当前仍在任务业务窗口内，立即补跑一次（避免 10:00  cron 已过后重启不再执行）。
    """
    now = datetime.now()
    for spec in JOB_REGISTRY:
        if spec.job_id not in _STARTUP_CATCHUP_JOB_IDS:
            continue
        if spec.job_id == "rq_minute_backfill":
            if not _in_minute_backfill_window(now):
                logger.info(
                    "rq_minute_backfill 将于今日 10:00 由 cron 触发（当前 {} 不在 10:00–14:40 窗口）",
                    now.strftime("%H:%M:%S"),
                )
                continue
            _run_spec_background(
                spec,
                locks,
                notify=notify,
                reason="启动补跑（处于 10:00–14:40 窗口）",
            )


def start_server(*, port: int = 7331, notify: bool = True) -> None:
    alias = mongo_trade_alias()
    logger.info("Mongo 交易日历别名：{}", alias)
    ensure_rq_logs_indexes()
    locks: dict[str, threading.Lock] = {s.job_id: threading.Lock() for s in JOB_REGISTRY}

    def _wrap(spec):
        def _job() -> None:
            lock = locks[spec.job_id]
            if not lock.acquire(blocking=False):
                logger.warning("任务 {} 仍在执行，跳过本次触发", spec.job_id)
                return
            try:
                if not _should_run_today(spec):
                    logger.info("任务 {}：今天非交易日，跳过", spec.job_id)
                    return
                run_job(spec, notify=notify)
            finally:
                lock.release()

        return _job

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    for spec in JOB_REGISTRY:
        scheduler.add_job(
            _wrap(spec),
            CronTrigger(
                hour=spec.cron_hour,
                minute=spec.cron_minute,
                timezone="Asia/Shanghai",
            ),
            id=spec.job_id,
            replace_existing=True,
        )
        logger.info(
            "已注册 {} [{}] {} {}",
            spec.schedule_time,
            spec.job_id,
            spec.task_name,
            spec.description,
        )
    scheduler.start()
    _startup_catchup_window_jobs(locks, notify=notify)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug(fmt, *args)

        def _send_json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/health":
                jobs = [
                    {
                        "job_id": s.job_id,
                        "task_name": s.task_name,
                        "schedule_time": s.schedule_time,
                        "cron": f"{s.cron_hour}:{s.cron_minute}",
                        "description": s.description,
                        "only_on_trade_day": s.only_on_trade_day,
                    }
                    for s in JOB_REGISTRY
                ]
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "port": port,
                        "notify": notify and notify_configured(),
                        "mongo_trade_alias": alias,
                        "jobs": jobs,
                    },
                )
                return

            if path == "/run":
                job_param = (qs.get("job") or [None])[0]
                targets = resolve_run_targets(job_param)
                if targets is None:
                    self._send_json(
                        400,
                        {
                            "error": "missing job parameter",
                            "hint": "须指定 job=all 或 job=<scheduler_job_key>，裸 /run 不会执行任何任务",
                            "examples": [
                                "/run?job=all",
                                "/run?job=rq_base_info",
                                "/run?job=rq_basic_financial",
                                "/run?job=rq_in_index",
                            ],
                        },
                    )
                    return
                if not targets:
                    self._send_json(404, {"error": f"unknown job: {job_param}"})
                    return

                def _bg() -> None:
                    for spec in targets:
                        if spec.only_on_trade_day and not _should_run_today(spec):
                            logger.info("手动触发 {}：今天非交易日，跳过", spec.job_id)
                            continue
                        run_job(spec, notify=notify)

                threading.Thread(target=_bg, daemon=True).start()
                self._send_json(
                    202,
                    {
                        "status": "accepted",
                        "jobs": [s.job_id for s in targets],
                        "order": "SCHEDULE_ENTRIES" if str(job_param).strip().lower() == "all" else "single",
                    },
                )
                return

            self._send_json(
                404,
                {
                    "error": "not found",
                    "paths": [
                        "/health",
                        "/run?job=all",
                        "/run?job=rq_base_info",
                    ],
                },
            )

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info("HTTP 监听 0.0.0.0:{}", port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("退出服务")
    finally:
        scheduler.shutdown(wait=False)
        httpd.shutdown()
