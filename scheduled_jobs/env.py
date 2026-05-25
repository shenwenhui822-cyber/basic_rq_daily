# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv_if_present() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def default_port() -> int:
    return int(os.environ.get("RQBASE_SCHEDULE_PORT", "7331"))
