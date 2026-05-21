# -*- coding: utf-8 -*-
"""
MongoDB 连接配置（basic_rq_daily 自包含分包，与脚本同目录）。

默认 ``local`` → 192.168.110.199:27018（无认证）。
可通过环境变量 MONGO_HOST / MONGO_PORT 覆盖 ``local`` 的地址。
"""
from __future__ import annotations

import os
from typing import Any

import pymongo

_DEFAULT_HOST = "192.168.110.199"
_DEFAULT_PORT = 27018
_WONDERWZ_HOST = "192.168.110.199"
_WONDERWZ_PORT = 27018


def _local_endpoint() -> dict[str, Any]:
    host = os.environ.get("MONGO_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("MONGO_PORT", str(_DEFAULT_PORT)))
    return {"host": host, "port": port, "user": None, "pwd": None}


CLIENT_DICT: dict[str, dict[str, Any]] = {
    "local": _local_endpoint(),
    "wonderwz27018_rw": {
        "user": "readwriter",
        "pwd": "readwrite_wonderwz",
        "host": _WONDERWZ_HOST,
        "port": _WONDERWZ_PORT,
    },
    "wonderwz27018_ro": {
        "user": "reader",
        "pwd": "readonly_wonderwz",
        "host": _WONDERWZ_HOST,
        "port": _WONDERWZ_PORT,
    },
    "wonderwz27018_admin": {
        "user": "admin",
        "pwd": "admin_wonderwz",
        "host": _WONDERWZ_HOST,
        "port": _WONDERWZ_PORT,
    },
}


def _resolve_config(c_from: str) -> dict[str, Any]:
    if c_from == "local":
        return _local_endpoint()
    config = CLIENT_DICT.get(c_from)
    if not config:
        raise ValueError(
            f"未知 mongo 别名: {c_from}，可选: {sorted({*CLIENT_DICT.keys(), 'local'})}"
        )
    return config


def _build_uri(config: dict[str, Any]) -> str:
    user, pwd = config.get("user"), config.get("pwd")
    host, port = config["host"], config["port"]
    if user and pwd:
        return f"mongodb://{user}:{pwd}@{host}:{port}"
    return f"mongodb://{host}:{port}"


def connect_mongo(c_from: str = "local") -> pymongo.MongoClient:
    config = _resolve_config(c_from)
    uri = _build_uri(config)
    try:
        print(f"正在连接到 {c_from} 数据库: {config['host']}:{config['port']}")
        return pymongo.MongoClient(uri)
    except pymongo.errors.PyMongoError as e:
        print(f"无法连接到 MongoDB 服务器: {e}")
        raise


def get_client(c_from: str = "local") -> pymongo.MongoClient:
    return connect_mongo(c_from)
