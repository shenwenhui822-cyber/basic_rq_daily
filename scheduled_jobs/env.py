# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv_simple(env_path: Path) -> None:
    """无 python-dotenv 时简易解析 KEY=VALUE（不覆盖已有环境变量）。"""
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def load_dotenv_if_present() -> bool:
    """加载项目根目录 .env；成功返回 True。"""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return True
    except ImportError:
        _load_dotenv_simple(env_path)
        return True


def default_port() -> int:
    return int(os.environ.get("RQBASE_SCHEDULE_PORT", "7331"))
