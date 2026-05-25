# -*- coding: utf-8 -*-
"""HTTP 服务 + APScheduler，按注册表调度多个任务。"""
from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from scheduled_jobs.config import mongo_trade_alias
from scheduled_jobs.jobs.registry import JOB_REGISTRY, run_job
from scheduled_jobs.notify.email import notify_configured
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


def start_server(*, port: int = 7331, notify: bool = True) -> None:
    alias = mongo_trade_alias()
    logger.info("Mongo 交易日历别名：{}", alias)
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
                        "cron": f"{s.cron_hour:02d}:{s.cron_minute:02d}",
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
                job_id = (qs.get("job") or [None])[0]
                targets = JOB_REGISTRY
                if job_id:
                    targets = [s for s in JOB_REGISTRY if s.job_id == job_id]
                    if not targets:
                        self._send_json(404, {"error": f"unknown job: {job_id}"})
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
                    },
                )
                return

            self._send_json(
                404,
                {
                    "error": "not found",
                    "paths": ["/health", "/run", "/run?job=rq_base_info"],
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
