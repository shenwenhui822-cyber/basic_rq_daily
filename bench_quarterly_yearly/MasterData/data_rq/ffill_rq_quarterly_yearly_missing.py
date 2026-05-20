"""
对 rq_quarterly / rq_yearly 在交易日历上缺失的 date，用「上一交易日已有快照」复制一行并改写 date（前向填充语义）。
若需按 rq_base_info 当日约 5000+ 票逐票补全截面，请用 ffill_rq_quarterly_yearly_to_universe.py。

适用：RQ PIT 当日无返回导致未落库，但策略需要截面连续时，与「用 T-1 财务快照」的常见做法一致。
（与 backfill 不同：不从米筐重拉，只把已有快照按交易日向前复制到缺日。）

依赖：economic.trade_dates；basic_rq 中该表已有至少一个早于缺失日的交易日数据。

日期格式：库内若混用 YYYY-MM-DD 与 YYYY/MM/DD（或 BSON 日期），本脚本一律先规范为 YYYY-MM-DD 再比较；
查询/删除使用 _day_variants；新写入行的 date 统一为 YYYY-MM-DD（与交易日列表一致）。

用法（项目根目录，PYTHONPATH=.）:
  # 未写 --start/--end 时：用 rq_base_info 的 min/max，且起点不低于 --floor（默认 2020-01-02）
  python MasterData/data_rq/ffill_rq_quarterly_yearly_missing.py
  python MasterData/data_rq/ffill_rq_quarterly_yearly_missing.py --dry-run

  python MasterData/data_rq/ffill_rq_quarterly_yearly_missing.py --start 2020-01-02 --end 2026-04-17
  python MasterData/data_rq/ffill_rq_quarterly_yearly_missing.py --start 2020-01-02 --end 2026-04-17 --dry-run
  python MasterData/data_rq/ffill_rq_quarterly_yearly_missing.py --collections yearly
"""
from __future__ import annotations

import argparse
import bisect
import copy
import datetime as _dt
import os
import sys

import pymongo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from DataBase.db_client import get_client


def _norm_date(x) -> str | None:
    if x is None:
        return None
    if isinstance(x, _dt.datetime):
        return x.date().isoformat()
    if isinstance(x, _dt.date):
        return x.isoformat()
    s = str(x).strip()
    if not s:
        return None
    if "/" in s:
        return s.replace("/", "-")[:10]
    return s[:10]


def _day_variants(s: str) -> list[str]:
    s = _norm_date(s) or s
    y, m, d = s.split("-")
    return list(dict.fromkeys([s, f"{y}/{m}/{d}"]))


def _load_all_trade_days_sorted(client) -> list[str]:
    """
    读全表 economic.trade_dates 并规范为 YYYY-MM-DD 后排序。
    避免 trade_date 混用 - 与 / 时依赖 Mongo 字符串比较 / sort 顺序错误。
    """
    out: set[str] = set()
    for doc in client.economic.trade_dates.find({}, {"_id": 0, "trade_date": 1}):
        td = _norm_date(doc.get("trade_date"))
        if td:
            out.add(td)
    return sorted(out)


def _trade_days_in_sorted(all_sorted: list[str], start: str, end: str) -> list[str]:
    """在已排序 ISO 交易日列表上做闭区间切片（start/end 须已规范为 YYYY-MM-DD）。"""
    lo = bisect.bisect_left(all_sorted, start)
    hi = bisect.bisect_right(all_sorted, end)
    return all_sorted[lo:hi]


def _distinct_dates_normalized(coll) -> set[str]:
    out: set[str] = set()
    try:
        for v in coll.distinct("date"):
            n = _norm_date(v)
            if n:
                out.add(n)
    except Exception as e:
        print(f"  [warn] distinct('date') 失败: {e}，尝试聚合…")
        for doc in coll.aggregate([{"$group": {"_id": "$date"}}], allowDiskUse=True):
            n = _norm_date(doc.get("_id"))
            if n:
                out.add(n)
    return out


def _range_from_base_info(client, dbname: str) -> tuple[str, str]:
    """对 rq_base_info 的 distinct(date) 规范后取 min/max，避免 date 字段混用格式时 sort 顺序非时间序。"""
    db = client[dbname]
    t = db["rq_base_info"]
    try:
        norms: list[str] = []
        for v in t.distinct("date"):
            n = _norm_date(v)
            if n:
                norms.append(n)
        if not norms:
            return "2020-01-02", "2099-12-31"
        return min(norms), max(norms)
    except Exception as e:
        print(f"  [warn] rq_base_info distinct('date') 失败: {e}，回退 findOne sort（可能受混合格式影响）")
        mn = t.find_one(sort=[("date", 1)], projection={"date": 1, "_id": 0})
        mx = t.find_one(sort=[("date", -1)], projection={"date": 1, "_id": 0})
        if not mn or not mx:
            return "2020-01-02", "2099-12-31"
        return _norm_date(mn["date"]) or "2020-01-02", _norm_date(mx["date"]) or "2099-12-31"


def _prev_trade_with_data(all_trade_days_sorted: list[str], have: set[str], d: str) -> str | None:
    """早于 d 的最后一个交易日，且该日在集合中已有快照（不限制在 --start/--end 区间内）。"""
    idx = bisect.bisect_left(all_trade_days_sorted, d)
    i = idx - 1
    while i >= 0:
        cand = all_trade_days_sorted[i]
        if cand in have:
            return cand
        i -= 1
    return None


def _ffill_one_day(
    table: pymongo.collection.Collection,
    have: set[str],
    target_day: str,
    all_trade_days_sorted: list[str],
    *,
    dry_run: bool,
    pending: dict[str, list[dict]],
    verbose: bool,
) -> tuple[int, str | None]:
    """
    返回 (写入行数或 dry-run 拟写入数, 跳过原因)。
    跳过原因：no_prev = 早于目标日的交易日中无已有快照；empty_prev = 源日无文档。
    """
    prev = _prev_trade_with_data(all_trade_days_sorted, have, target_day)
    if prev is None:
        if verbose:
            print(f"    [skip] {target_day}：此前无可用快照")
        return 0, "no_prev"

    rows: list[dict] = []
    if prev in pending:
        for doc in copy.deepcopy(pending[prev]):
            doc.pop("_id", None)
            doc["date"] = target_day
            rows.append(doc)
    else:
        cursor = table.find({"date": {"$in": _day_variants(prev)}}, projection={})
        for doc in cursor:
            doc.pop("_id", None)
            doc["date"] = target_day
            rows.append(doc)

    if not rows:
        if verbose:
            print(f"    [skip] {target_day}：上一日 {prev} 无文档")
        return 0, "empty_prev"

    if dry_run:
        pending[target_day] = copy.deepcopy(rows)
        print(f"    [dry-run] {target_day} <- {prev}，将写入 {len(rows)} 条")
        return len(rows), None

    for dv in _day_variants(target_day):
        table.delete_many({"date": dv})
    table.insert_many(rows, ordered=False)
    print(f"    ✅ {target_day} <- {prev}，写入 {len(rows)} 条")
    return len(rows), None


def _print_have_diagnostic(have: set[str], range_start: str) -> None:
    if not have:
        print(
            "  [提示] 本集合 distinct(date) 为空或无法解析为日期；"
            "无法前填。请确认 rq_quarterly/rq_yearly 是否已有落库。"
        )
        return
    earliest = min(have)
    latest = max(have)
    print(f"  [提示] 已有快照：{len(have)} 个不同 date，最早 {earliest}，最晚 {latest}")
    if earliest > range_start:
        print(
            f"  [提示] 最早快照晚于本次区间起点 {range_start}；"
            f"从 {range_start} 到其前一交易日的缺失日无法前填（没有更早一行的「真快照」可抄）。"
            "若库里实际从更晚才有数据，可将 --start 提到最早快照当周，或接受段首空缺。"
        )


def ffill_collection(
    table: pymongo.collection.Collection,
    trade_list: list[str],
    all_trade_days_sorted: list[str],
    *,
    dry_run: bool,
    verbose: bool,
    range_start: str,
) -> tuple[int, int, int]:
    have = _distinct_dates_normalized(table)
    _print_have_diagnostic(have, range_start)
    pending: dict[str, list[dict]] = {}
    filled_days = 0
    total_rows = 0
    skip_no_prev = 0
    for d in trade_list:
        if d in have:
            continue
        n, skip = _ffill_one_day(
            table,
            have,
            d,
            all_trade_days_sorted,
            dry_run=dry_run,
            pending=pending,
            verbose=verbose,
        )
        if skip == "no_prev":
            skip_no_prev += 1
        if n > 0:
            filled_days += 1
            total_rows += n
            # 链式填充：后续缺失日应以前一日（含本轮刚补上的日）为源；dry-run 也更新 have 以反映真实行为
            have.add(d)
    if skip_no_prev and not verbose:
        print(
            f"  [提示] 共 {skip_no_prev} 个交易日因「此前无可用快照」跳过；"
            "加 --verbose 可逐日打印。若最早快照已晚于区间起点，属预期。"
        )
    return filled_days, total_rows, skip_no_prev


def main() -> None:
    p = argparse.ArgumentParser(description="rq_quarterly / rq_yearly 缺失日用上一交易日快照前向填充")
    p.add_argument("--start", default=None, help="区间起；与 --end 成对省略时见 --floor 与 rq_base_info")
    p.add_argument("--end", default=None, help="区间止；与 --start 成对省略时见 --floor 与 rq_base_info")
    p.add_argument(
        "--floor",
        default="2020-01-02",
        help="仅当未同时指定 --start 与 --end 时：自动区间的起点取 max(rq_base_info 最早日, 本值)，默认 2020-01-02",
    )
    p.add_argument("--mongo-alias", default="local")
    p.add_argument("--mongo-db", default="basic_rq")
    p.add_argument(
        "--collections",
        default="both",
        choices=("both", "quarterly", "yearly"),
        help="处理 rq_quarterly、rq_yearly 或二者",
    )
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    p.add_argument(
        "--verbose",
        action="store_true",
        help="逐日打印「此前无可用快照」等跳过原因（默认只打汇总，避免上千行刷屏）",
    )
    p.add_argument(
        "--ensure-indexes",
        action="store_true",
        help="确保 (date, code) 唯一索引存在（不依赖 RQ）",
    )
    args = p.parse_args()

    c = get_client(args.mongo_alias)
    print("正在加载 economic.trade_dates（全表规范为 YYYY-MM-DD 后排序，兼容 - 与 / 混存）…", flush=True)
    all_trade_days = _load_all_trade_days_sorted(c)
    if not all_trade_days:
        print("economic.trade_dates 为空或无法解析，退出")
        return

    if args.start and args.end:
        start = _norm_date(args.start) or args.start[:10]
        end = _norm_date(args.end) or args.end[:10]
    else:
        mn, mx = _range_from_base_info(c, args.mongo_db)
        fl = _norm_date(args.floor) or (args.floor or "2020-01-02")[:10]
        start = max(mn, fl)
        end = mx
        print(f"区间（rq_base_info min/max，起点不低于 --floor={fl}）: {start} ~ {end}\n")
    if start > end:
        print(f"无效区间：start={start} > end={end}，退出")
        return

    trade_list = _trade_days_in_sorted(all_trade_days, start, end)
    if not trade_list:
        print("无交易日，退出")
        return

    db = c[args.mongo_db]
    names: list[str] = []
    if args.collections in ("both", "quarterly"):
        names.append("rq_quarterly")
    if args.collections in ("both", "yearly"):
        names.append("rq_yearly")

    for coll_name in names:
        print(f"=== {coll_name} ===")
        t = db[coll_name]
        if args.ensure_indexes:
            t.create_index(
                [("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)],
                background=True,
                unique=True,
            )
        days, rows, _sk = ffill_collection(
            t,
            trade_list,
            all_trade_days,
            dry_run=args.dry_run,
            verbose=args.verbose,
            range_start=start,
        )
        print(f"  小结：补全日数 {days}，文档行数约 {rows}（dry_run={args.dry_run}）\n")


if __name__ == "__main__":
    main()
