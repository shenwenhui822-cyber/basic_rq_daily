# -*- coding: utf-8 -*-
"""定时任务服务入口：python scheduled_jobs/run_server.py"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 必须先加载 .env，再 import server（否则会沿用 trade_date_utils 里 local 默认值）
from scheduled_jobs.env import default_port, load_dotenv_if_present

load_dotenv_if_present()

from scheduled_jobs.server import start_server  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="basic_rq 多任务定时服务（邮件 + HTTP）")
    p.add_argument("--port", type=int, default=default_port(), help="HTTP 端口，默认 7331")
    p.add_argument("--no-notify", action="store_true", help="不发送邮件")
    args = p.parse_args()
    start_server(port=args.port, notify=not args.no_notify)


if __name__ == "__main__":
    main()
