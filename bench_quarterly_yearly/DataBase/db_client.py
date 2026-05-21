# -*- coding: utf-8 -*-
"""
Mongo 连接：默认 alias ``local`` → 192.168.110.199:27018。
可通过环境变量 MONGO_HOST / MONGO_PORT 覆盖。
"""
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from mongo_connect import connect_mongo, get_client  # noqa: E402


def get_client_U(c_from: str = "local"):
    """B 链路历史命名，与 get_client 行为一致。"""
    return connect_mongo(c_from)
