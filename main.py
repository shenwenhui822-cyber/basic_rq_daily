#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
basic_rq_daily 统一入口：启动定时任务服务（APScheduler + HTTP）。

用法::

    python main.py
    python main.py --port 7331
    python main.py --no-notify

计划任务：scheduled_jobs/jobs/schedule_config.py
健康检查：http://<host>:7331/health
手动触发：http://<host>:7331/run?job=all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scheduled_jobs.env import default_port, load_dotenv_if_present

load_dotenv_if_present()

from scheduled_jobs.server import start_server  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="basic_rq 定时任务服务")
    p.add_argument("--port", type=int, default=default_port(), help="HTTP 端口，默认 7331")
    p.add_argument("--no-notify", action="store_true", help="不发送邮件")
    args = p.parse_args()
    start_server(port=args.port, notify=not args.no_notify)


if __name__ == "__main__":
    main()
