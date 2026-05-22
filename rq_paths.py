"""将 basic_rq_daily 根目录及日更/历史子目录加入 sys.path（子目录脚本 import 用）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DAILY = ROOT / "rq_daily_update"
BACKFILL = ROOT / "rq_history_backfill"


def bootstrap(
    file: str,
    *,
    daily: bool = False,
    backfill: bool = False,
) -> Path:
    """由子目录脚本的 ``__file__`` 调用；返回仓库根路径。"""
    root = Path(file).resolve().parents[1]
    script_dir = Path(file).resolve().parent
    extra: list[Path] = []
    if daily:
        extra.append(DAILY)
    if backfill:
        extra.append(BACKFILL)
    for p in (root, script_dir, *extra):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return root
