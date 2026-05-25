# -*- coding: utf-8 -*-
"""定时任务共用配置（读环境变量，须于 import 业务模块前加载 .env）。"""
from __future__ import annotations

import os

# 读 economic.trade_dates / 写 basic_rq 均用带认证的别名（勿用无认证的 local）
DEFAULT_MONGO_ALIAS = "wonderwz27018_rw"


def mongo_trade_alias() -> str:
    return os.environ.get("MONGO_TRADE_ALIAS", DEFAULT_MONGO_ALIAS).strip() or DEFAULT_MONGO_ALIAS
