"""
分钟线历史倒序补数：查已有最大 date，按自然月倒序批量落库（对齐 rq_getRangeDailyPriceLongrun）。

10:00 启动后持续跑至 14:40；lookup 优先读 ``basic_rq.rq_base_info``，拉数复用 ``rq_getRangeMinPrice``。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent
_PKG_ROOT = _PKG_DIR.parent
for _p in (_PKG_ROOT, _PKG_DIR):
    _s = str(_p)
    if _s in sys.path:
        sys.path.remove(_s)
    sys.path.insert(0, _s)

from trade_date_utils import list_trade_dates, norm_trade_date_str, previous_trade_date
from usedbdef import DEFAULT_MONGO_ALIAS, get_client

from minute_mongo import MINUTE_DB, find_latest_minute_trade_date

DEFAULT_BACKFILL_START = "2015-01-05"
BACKFILL_WINDOW_START = time(10, 0)
BACKFILL_WINDOW_END = time(14, 40)
DATE_FMT_DB = "%Y-%m-%d"


def in_backfill_time_window(now: datetime | None = None) -> bool:
    """是否在 10:00–14:40 补数窗口内。"""
    now = now or datetime.now()
    t = now.time()
    return BACKFILL_WINDOW_START <= t <= BACKFILL_WINDOW_END


def backfill_deadline_today(now: datetime | None = None) -> datetime:
    """当日 14:40 截止时刻。"""
    now = now or datetime.now()
    return datetime.combine(now.date(), BACKFILL_WINDOW_END)


def iter_month_segments(start_s: str, end_s: str):
    """将 [start_s, end_s] 按自然月切段。yield (seg_start, seg_end, 'YYYY-MM')。"""
    d0 = pd.Timestamp(start_s).normalize()
    d1 = pd.Timestamp(end_s).normalize()
    if d0 > d1:
        raise ValueError(f"start 不能晚于 end：{d0.date()} > {d1.date()}")

    cur = d0.replace(day=1)
    while cur <= d1:
        month_end = cur + pd.offsets.MonthEnd(0)
        seg_start = max(d0, cur)
        seg_end = min(d1, month_end)
        if seg_start <= seg_end:
            yield (
                seg_start.strftime(DATE_FMT_DB),
                seg_end.strftime(DATE_FMT_DB),
                cur.strftime("%Y-%m"),
            )
        cur = (cur + pd.offsets.MonthBegin(1)).normalize()


def resolve_backfill_range(
    client: Any,
    *,
    mongo_alias: str = DEFAULT_MONGO_ALIAS,
    start: str = DEFAULT_BACKFILL_START,
) -> tuple[str | None, str | None, str | None]:
    """
    返回 (补数起点, 补数终点, anchor 最大 date)。

    补数区间为 [start_s, end_s]，均在 anchor 之前。
    """
    anchor = find_latest_minute_trade_date(client, minute_db=MINUTE_DB)
    if anchor is None:
        anchor = previous_trade_date(mongo_alias=mongo_alias, client=client, fmt=DATE_FMT_DB)

    anchor = norm_trade_date_str(anchor)
    start_s = norm_trade_date_str(start)

    trade_days = list_trade_dates(start_s, anchor, mongo_alias=mongo_alias, client=client)
    before_anchor = [d for d in trade_days if d < anchor]
    if not before_anchor:
        return None, None, anchor

    end_s = before_anchor[-1]
    if start_s > end_s:
        return None, None, anchor
    return start_s, end_s, anchor


def run_minute_monthly_backfill(
    *,
    mongo_alias: str = DEFAULT_MONGO_ALIAS,
    start: str = DEFAULT_BACKFILL_START,
    enforce_window: bool = True,
    skip_existing: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    在 10:00–14:40 内按自然月倒序持续批量落库，直到截止或历史补齐。
    """
    from rq_getRangeMinPrice import log_rq_quota_status, run_minute_range_to_mongo

    now = now or datetime.now()
    run_at = now.strftime("%Y-%m-%d %H:%M:%S")

    if enforce_window and not in_backfill_time_window(now):
        return {
            "ok": True,
            "skipped": True,
            "message": f"当前 {now.strftime('%H:%M')} 不在补数窗口 10:00–14:40，已跳过",
            "run_at": run_at,
        }

    client = get_client(mongo_alias)
    start_s, end_s, anchor = resolve_backfill_range(
        client, mongo_alias=mongo_alias, start=start
    )
    if start_s is None or end_s is None:
        return {
            "ok": True,
            "skipped": True,
            "message": f"历史已补齐（库内最大 date={anchor}，起点={norm_trade_date_str(start)}）",
            "anchor_date": anchor,
            "run_at": run_at,
        }

    deadline = backfill_deadline_today(now)
    months = list(iter_month_segments(start_s, end_s))
    months.reverse()

    print(
        f"\n{'='*60}\n分钟历史倒序补数\n"
        f"anchor={anchor} | 区间 {start_s} ~ {end_s} | 共 {len(months)} 个自然月（倒序）\n"
        f"截止 {deadline.strftime('%H:%M')}\n{'='*60}"
    )
    log_rq_quota_status("补数启动")

    total_ok = 0
    total_days = 0
    total_skipped = 0
    months_done = 0

    for m_idx, (m_start, m_end, month_label) in enumerate(months, start=1):
        if datetime.now() >= deadline:
            msg = (
                f"已达 14:40 截止；anchor={anchor} | "
                f"本月进度 {months_done}/{len(months)} 月 | "
                f"写入 {total_ok} 日 / 跳过 {total_skipped} 日"
            )
            return {
                "ok": True,
                "skipped": False,
                "message": msg,
                "anchor_date": anchor,
                "range_start": start_s,
                "range_end": end_s,
                "months_total": len(months),
                "months_done": months_done,
                "ok_days": total_ok,
                "skipped_days": total_skipped,
                "stopped_by_deadline": True,
                "run_at": run_at,
            }

        print(f"\n{'-'*50}\n[{m_idx}/{len(months)}] 倒序自然月 {month_label}：{m_start} ~ {m_end}\n{'-'*50}")

        stats = run_minute_range_to_mongo(
            m_start,
            m_end,
            mongo_alias=mongo_alias,
            skip_existing=skip_existing,
            deadline=deadline,
            use_mongo_base_info=True,
            month_label=month_label,
        )

        total_ok += int(stats.get("ok_days", 0))
        total_days += int(stats.get("total_days", 0))
        total_skipped += int(stats.get("skipped_days", 0))
        months_done += 1

        print(
            f"✅ 月 {month_label}：写入 {stats.get('ok_days', 0)}/{stats.get('total_days', 0)} 日"
            f"（跳过 {stats.get('skipped_days', 0)}）"
        )

        if stats.get("stopped_by_deadline"):
            msg = (
                f"14:40 截止暂停；anchor={anchor} | "
                f"进度 {months_done}/{len(months)} 月 | 累计写入 {total_ok} 日"
            )
            return {
                "ok": True,
                "skipped": False,
                "message": msg,
                "anchor_date": anchor,
                "months_total": len(months),
                "months_done": months_done,
                "ok_days": total_ok,
                "skipped_days": total_skipped,
                "stopped_by_deadline": True,
                "run_at": run_at,
            }

        if stats.get("stopped_by_quota"):
            return {
                "ok": False,
                "skipped": False,
                "message": f"RQ 配额达阈值，中止于 {month_label}（anchor={anchor}）",
                "anchor_date": anchor,
                "months_done": months_done,
                "ok_days": total_ok,
                "stopped_by_quota": True,
                "run_at": run_at,
            }

    msg = (
        f"历史倒序补数完成 anchor={anchor} | 区间 {start_s}~{end_s} | "
        f"{len(months)} 月 | 写入 {total_ok} 日 / 跳过 {total_skipped} 日"
    )
    return {
        "ok": True,
        "skipped": False,
        "message": msg,
        "anchor_date": anchor,
        "range_start": start_s,
        "range_end": end_s,
        "months_total": len(months),
        "months_done": months_done,
        "ok_days": total_ok,
        "skipped_days": total_skipped,
        "run_at": run_at,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="分钟线历史倒序补数（按自然月批量）")
    p.add_argument("--mongo-alias", default=DEFAULT_MONGO_ALIAS)
    p.add_argument("--start", default=DEFAULT_BACKFILL_START, help="历史补数起点")
    p.add_argument(
        "--force",
        action="store_true",
        help="忽略 10:00–14:40 窗口（或设 RQ_MINUTE_BACKFILL_FORCE=1）",
    )
    p.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过已有分钟数据的交易日",
    )
    args = p.parse_args()

    force = args.force or os.environ.get("RQ_MINUTE_BACKFILL_FORCE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    result = run_minute_monthly_backfill(
        mongo_alias=args.mongo_alias,
        start=args.start,
        enforce_window=not force,
        skip_existing=not args.no_skip_existing,
    )
    print(result["message"])
    if result.get("skipped"):
        raise SystemExit(0)
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
