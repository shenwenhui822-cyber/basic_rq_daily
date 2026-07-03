# -*- coding: utf-8 -*-
"""PIT 拉取无数据时，从前一有效交易日复制整表截面（前向填充）。"""
from __future__ import annotations

from typing import Any

from MasterData.data_rq.ffill_rq_quarterly_yearly_missing import (
    _distinct_dates_normalized,
    _ffill_one_day,
    _prev_trade_with_data,
)


def ffill_from_prev_trade_snapshot(
    coll: Any,
    target_day: str,
    all_trade_days_sorted: list[str],
    *,
    verbose: bool = True,
) -> tuple[bool, int, str | None]:
    """
    将 target_day 的截面从前一有效交易日已有快照复制。

    Returns:
        (是否成功, 写入行数, 源交易日)
    """
    have = _distinct_dates_normalized(coll)
    source = _prev_trade_with_data(all_trade_days_sorted, have, target_day)
    if source is None:
        if verbose:
            print(f"[ffill] {target_day}：此前无可用快照，无法前填")
        return False, 0, None

    rows, skip = _ffill_one_day(
        coll,
        have,
        target_day,
        all_trade_days_sorted,
        dry_run=False,
        pending={},
        verbose=verbose,
    )
    if skip or rows <= 0:
        if verbose:
            print(f"[ffill] {target_day}：从前日 {source} 复制失败（{skip or 'empty'}）")
        return False, 0, source
    return True, rows, source
