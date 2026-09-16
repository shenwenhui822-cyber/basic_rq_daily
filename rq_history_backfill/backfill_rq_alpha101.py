# -*- coding: utf-8 -*-
"""
历史补齐 rq_alpha101（WorldQuant_alpha001 … WorldQuant_alpha101）。

按交易日区间逐日拉取并落库；优先用同日 ``rq_base_info`` 的 code_rq。
流量占用 ≥ 50% 即停止。按自然年分段执行。

用法（仓库根目录）：
  python rq_history_backfill/backfill_rq_alpha101.py --date 2026-09-15
  python rq_history_backfill/backfill_rq_alpha101.py --start 2026-01-05 --end 2026-03-31
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__, backfill=True)

import pandas as pd
import rqdatac as rq

from trade_date_utils import parse_explicit_date_arg, parse_start_end_range

DATE_FMT_DB = "%Y-%m-%d"


def _parse_input_date(s: str) -> date:
    return pd.Timestamp(s).date()


def iter_year_segments(start_s: str, end_s: str) -> Iterable[tuple[str, str, int]]:
    d0 = _parse_input_date(start_s)
    d1 = _parse_input_date(end_s)
    if d0 > d1:
        raise ValueError(f"start 不能晚于 end：{d0} > {d1}")
    for year in range(d0.year, d1.year + 1):
        seg_start = max(d0, date(year, 1, 1))
        seg_end = min(d1, date(year, 12, 31))
        if seg_start <= seg_end:
            yield seg_start.strftime(DATE_FMT_DB), seg_end.strftime(DATE_FMT_DB), year


def run_pipeline_for_range(
    start_s: str,
    end_s: str,
    *,
    mongo_alias: str,
    dry_run: bool,
    segment_label: str,
    code_chunk: int,
    factor_batch: int,
) -> None:
    from rq_daily_update.update_rq_alpha101 import (
        check_rq_quota_or_raise,
        log_rq_quota,
        update_rq_alpha101,
    )

    banner = segment_label or f"{start_s} ~ {end_s}"
    print(f"\n{banner} | 区间: {start_s} ~ {end_s}")
    log_rq_quota(f"{banner} 开始前")
    check_rq_quota_or_raise(label=f"{banner} 开始前")

    d0 = _parse_input_date(start_s)
    d1 = _parse_input_date(end_s)
    trading_days = rq.get_trading_dates(start_date=d0, end_date=d1, market="cn")
    if not trading_days:
        print("无交易日，跳过。")
        return

    for idx, d in enumerate(trading_days, start=1):
        trade_date = d.strftime(DATE_FMT_DB)
        print(f"\n{trade_date} ({idx}/{len(trading_days)})")
        check_rq_quota_or_raise(label=f"{trade_date} 拉取前")
        ok = update_rq_alpha101(
            trade_date,
            mongo_alias=mongo_alias,
            dry_run=dry_run,
            code_chunk=code_chunk,
            factor_batch=factor_batch,
            allow_all_instruments_fallback=True,
        )
        print(f"  {'ok' if ok else 'fail'}")

    log_rq_quota(f"{banner} 结束后")


def main(
    *,
    start_date: str,
    end_date: str,
    single_day: str | None = None,
    split_by_year: bool = True,
    mongo_alias: str = "wonderwz27018_rw",
    dry_run: bool = False,
    code_chunk: int = 800,
    factor_batch: int = 20,
) -> None:
    if single_day:
        start_s = end_s = parse_explicit_date_arg(single_day, fmt=DATE_FMT_DB)
    else:
        start_s, end_s = parse_start_end_range(start_date, end_date, fmt=DATE_FMT_DB)

    print(f"总区间: {start_s} ~ {end_s}（含）| Alpha101 历史回填")
    from rq_daily_update.update_rq_alpha101 import log_rq_quota

    log_rq_quota("任务启动时")

    if single_day or not split_by_year:
        run_pipeline_for_range(
            start_s,
            end_s,
            mongo_alias=mongo_alias,
            dry_run=dry_run,
            segment_label="单日" if single_day else "整段（未按年拆分）",
            code_chunk=code_chunk,
            factor_batch=factor_batch,
        )
        return

    segments = list(iter_year_segments(start_s, end_s))
    print(f"将按自然年分段执行，共 {len(segments)} 段。")
    for seg_start, seg_end, year in segments:
        run_pipeline_for_range(
            seg_start,
            seg_end,
            mongo_alias=mongo_alias,
            dry_run=dry_run,
            segment_label=f"{year} 年",
            code_chunk=code_chunk,
            factor_batch=factor_batch,
        )

    print("\n全部年段执行完毕。")
    log_rq_quota("全部任务结束后")


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="历史补齐 rq_alpha101：按交易日区间拉取并落库",
    )
    p.add_argument(
        "--start",
        default="2026-09-15",
        help="区间起（含）：YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD",
    )
    p.add_argument("--end", default="2026-09-15", help="区间止（含），格式同 --start")
    p.add_argument(
        "--date",
        default=None,
        help="单日（含）；指定后忽略 --start / --end",
    )
    p.add_argument(
        "--mongo-alias",
        default="wonderwz27018_rw",
        help="get_client 别名，默认 wonderwz27018_rw",
    )
    p.add_argument("--dry-run", action="store_true", help="只拉取不写 Mongo")
    p.add_argument("--no-split-year", action="store_true", help="不按年分段")
    p.add_argument("--code-chunk", type=int, default=800)
    p.add_argument("--factor-batch", type=int, default=20)
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    try:
        main(
            start_date=args.start,
            end_date=args.end,
            single_day=args.date,
            split_by_year=not args.no_split_year,
            mongo_alias=args.mongo_alias,
            dry_run=args.dry_run,
            code_chunk=args.code_chunk,
            factor_batch=args.factor_batch,
        )
    except RuntimeError as e:
        print(f"\n中止: {e}", file=sys.stderr)
        sys.exit(1)
