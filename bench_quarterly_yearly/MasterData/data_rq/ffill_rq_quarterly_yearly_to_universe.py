"""
按 rq_base_info 当日全市场 code_rq（约 5000+）为「目标截面」，对 rq_quarterly / rq_yearly
逐交易日、逐股票前向填充：某日缺该票则插入「该票上一有效快照」的副本并改写 date。

与 ffill_rq_quarterly_yearly_missing.py 的区别：
  - 旧脚本：只要该 date 在集合里已有任意文档，就认为「该日已齐」，整表从上一日抄。
  - 本脚本：按票检查；某日可能已有部分股票，仍会为缺失的 code_rq 补行，直到与 base 对齐（在
    该票历史上曾出现过至少一次财务行之后，才能向前链式补；从未出现过的票无法造数）。

日期混用 YYYY-MM-DD / YYYY/MM/DD：复用同目录下 ffill_rq_quarterly_yearly_missing 的规范化与 _day_variants。

用法（项目根目录）:
  python -u MasterData/data_rq/ffill_rq_quarterly_yearly_to_universe.py --dry-run
  python -u MasterData/data_rq/ffill_rq_quarterly_yearly_to_universe.py
  python -u MasterData/data_rq/ffill_rq_quarterly_yearly_to_universe.py --start 2020-01-17 --end 2026-04-17
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time

import pymongo.errors as _me

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_rq_dir = os.path.dirname(os.path.abspath(__file__))
if _rq_dir not in sys.path:
    sys.path.insert(0, _rq_dir)

from DataBase.db_client import get_client

from ffill_rq_quarterly_yearly_missing import (
    _day_variants,
    _load_all_trade_days_sorted,
    _norm_date,
    _range_from_base_info,
    _trade_days_in_sorted,
)


def _is_transient_mongo_err(e: BaseException) -> bool:
    """游标 getMore 时 mongod 重启 / 主切换 / 网络闪断等可重试。"""
    if isinstance(
        e,
        (
            _me.NotPrimaryError,
            _me.ConnectionFailure,
            _me.AutoReconnect,
            _me.NetworkTimeout,
            _me.ServerSelectionTimeoutError,
        ),
    ):
        return True
    if isinstance(e, _me.OperationFailure):
        code = getattr(e, "code", None)
        if code in (11600, 11602, 13436, 91):
            return True
        msg = str(e)
        if "InterruptedAtShutdown" in msg or "interrupted at shutdown" in msg.lower():
            return True
    return False


def _retry_mongo_call(fn, *, attempts: int, delay_sec: float):
    last: BaseException | None = None
    for k in range(attempts):
        try:
            return fn()
        except Exception as e:
            if not _is_transient_mongo_err(e):
                raise
            last = e
            if k + 1 >= attempts:
                raise
            time.sleep(delay_sec * (k + 1))
    assert last is not None
    raise last


def _codes_rq_from_base(
    base_coll,
    d_iso: str,
    *,
    mongo_attempts: int,
    mongo_delay: float,
) -> list[str]:
    """当日 rq_base_info 全市场 code_rq 列表（顺序保留、去重）。"""
    variants = _day_variants(d_iso)

    def _fetch() -> list[dict]:
        cur = base_coll.find(
            {"date": {"$in": variants}},
            {"_id": 0, "code_rq": 1},
            batch_size=1000,
        )
        return list(cur)

    docs = _retry_mongo_call(_fetch, attempts=mongo_attempts, delay_sec=mongo_delay)
    out: list[str] = []
    seen: set[str] = set()
    for doc in docs:
        cr = doc.get("code_rq")
        if cr is None:
            continue
        s = str(cr).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _rows_by_code_rq(
    coll,
    d_iso: str,
    *,
    mongo_attempts: int,
    mongo_delay: float,
) -> dict[str, dict]:
    """当日财务表 date=variants(d) 的文档，按 code_rq 索引（重复则后者覆盖）。"""
    variants = _day_variants(d_iso)

    def _fetch() -> list[dict]:
        cur = coll.find({"date": {"$in": variants}}, projection={}, batch_size=500)
        return list(cur)

    raw = _retry_mongo_call(_fetch, attempts=mongo_attempts, delay_sec=mongo_delay)
    out: dict[str, dict] = {}
    for doc in raw:
        doc.pop("_id", None)
        cr = doc.get("code_rq")
        if cr is None:
            continue
        out[str(cr).strip()] = doc
    return out


def ffill_fin_to_universe(
    fin_coll,
    base_coll,
    trade_list: list[str],
    *,
    dry_run: bool,
    verbose: bool,
    insert_chunk: int,
    progress_every: int,
    mongo_attempts: int,
    mongo_delay: float,
) -> tuple[int, int]:
    """
    按时间顺序遍历 trade_list，维护每只股票最后见到的财务行，对 universe 中缺票补行。
    返回 (有插入的交易日个数, 插入总行数)。
    """
    last_by_code: dict[str, dict] = {}
    days_with_inserts = 0
    total_inserts = 0
    n_days = len(trade_list)
    t_batch = time.perf_counter()

    print(
        f"  本段共 {n_days} 个交易日；每日需读 rq_base_info + 财务表各约数千行，"
        f"磁盘慢时单日可能数十秒～数分钟。"
        + ("" if progress_every > 0 else "（--progress-every 0：不逐日打印，仅结束时报总耗时）"),
        flush=True,
    )

    for i, d in enumerate(trade_list):
        t0 = time.perf_counter()
        if i == 0 and progress_every > 0:
            print(f"  [{i + 1}/{n_days}] {d} 查询 rq_base_info …", flush=True)
        uni = _codes_rq_from_base(
            base_coll, d, mongo_attempts=mongo_attempts, mongo_delay=mongo_delay
        )
        if not uni:
            if verbose:
                print(f"    [warn] {d} rq_base_info 无 code_rq，跳过该日")
            continue

        if i == 0 and progress_every > 0:
            print(
                f"  [{i + 1}/{n_days}] {d} base={len(uni)} 只，查询财务表 …",
                flush=True,
            )
        present = _rows_by_code_rq(
            fin_coll, d, mongo_attempts=mongo_attempts, mongo_delay=mongo_delay
        )
        to_insert: list[dict] = []

        for c in uni:
            if c in present:
                last_by_code[c] = present[c]
            elif c in last_by_code:
                newd = copy.deepcopy(last_by_code[c])
                newd.pop("_id", None)
                newd["date"] = d
                to_insert.append(newd)
                last_by_code[c] = newd

        if to_insert:
            days_with_inserts += 1
            total_inserts += len(to_insert)
            if dry_run:
                if verbose or i < 2:
                    print(
                        f"    [dry-run] {d} 拟插入 {len(to_insert)} 条 "
                        f"(universe={len(uni)} 已有票={len(present)})"
                    )
            else:
                for j in range(0, len(to_insert), insert_chunk):
                    chunk = to_insert[j : j + insert_chunk]

                    def _ins(c=chunk):
                        fin_coll.insert_many(c, ordered=False)

                    _retry_mongo_call(_ins, attempts=mongo_attempts, delay_sec=mongo_delay)
                if verbose:
                    print(
                        f"    ✅ {d} +{len(to_insert)} "
                        f"(universe={len(uni)} 已有票={len(present)})"
                    )
        elif verbose and (len(present) < len(uni)):
            print(
                f"    [verbose] {d} 缺 {len(uni) - len(present)} 票但无法前填"
                f"（这些票尚未在财务表出现过，无 last）"
            )

        dt_day = time.perf_counter() - t0
        if verbose:
            pass
        elif progress_every > 0 and (
            (i + 1) % progress_every == 0 or i == 0 or (i + 1) == n_days
        ):
            print(
                f"  [{i + 1}/{n_days}] {d} base={len(uni)} fin={len(present)} "
                f"拟补={len(to_insert)} 累计拟插入={total_inserts} 本日耗时={dt_day:.1f}s",
                flush=True,
            )

    print(
        f"  本表本段总耗时 {time.perf_counter() - t_batch:.1f}s",
        flush=True,
    )

    return days_with_inserts, total_inserts


def main() -> None:
    p = argparse.ArgumentParser(
        description="按 rq_base_info 全市场 code_rq 将 rq_quarterly/rq_yearly 补至每日截面完整（逐票前填）"
    )
    p.add_argument("--start", default=None, help="与 --end 成对；省略则用 rq_base_info min/max + --floor")
    p.add_argument("--end", default=None)
    p.add_argument(
        "--floor",
        default="2020-01-02",
        help="未同时指定 start/end 时，起点取 max(rq_base_info 最早日, floor)",
    )
    p.add_argument("--mongo-alias", default="local")
    p.add_argument("--mongo-db", default="basic_rq")
    p.add_argument(
        "--collections",
        default="both",
        choices=("both", "quarterly", "yearly"),
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--insert-chunk",
        type=int,
        default=2000,
        help="insert_many 每批条数",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=1,
        help="非 verbose 时每 N 个交易日打印一行摘要；首日与末日总会打印。1=每日一行；"
        "设大一些可减少输出；0 表示几乎不打进度（仅首日一条提示）",
    )
    p.add_argument(
        "--mongo-retries",
        type=int,
        default=6,
        help="读/写 Mongo 遇短暂断连、NotPrimary、11600 InterruptedAtShutdown 等时的重试次数",
    )
    p.add_argument(
        "--mongo-retry-delay",
        type=float,
        default=1.25,
        help="重试间隔基数秒，实际为 delay * (1 + 第几次重试)",
    )
    args = p.parse_args()

    c = get_client(args.mongo_alias)
    print(
        "提示：若报 11600 InterruptedAtShutdown / NotPrimary，多为 mongod 重启或副本集切换；"
        "请确认本机 Mongo 已稳定运行后再跑。",
        flush=True,
    )
    print("正在加载 economic.trade_dates …", flush=True)
    all_td = _load_all_trade_days_sorted(c)
    if not all_td:
        print("economic.trade_dates 为空，退出")
        return

    if args.start and args.end:
        start = _norm_date(args.start) or args.start[:10]
        end = _norm_date(args.end) or args.end[:10]
    else:
        mn, mx = _range_from_base_info(c, args.mongo_db)
        fl = _norm_date(args.floor) or (args.floor or "2020-01-02")[:10]
        start = max(mn, fl)
        end = mx
        print(f"区间（rq_base_info min/max，起点不低于 floor={fl}）: {start} ~ {end}\n")
    if start > end:
        print("无效区间，退出")
        return

    trade_list = _trade_days_in_sorted(all_td, start, end)
    if not trade_list:
        print("无交易日，退出")
        return

    db = c[args.mongo_db]
    base_coll = db["rq_base_info"]
    names: list[tuple[str, str]] = []
    if args.collections in ("both", "quarterly"):
        names.append(("rq_quarterly", "季报"))
    if args.collections in ("both", "yearly"):
        names.append(("rq_yearly", "年报"))

    for coll_name, label in names:
        print(f"=== {coll_name}（{label}，补至 base 当日 universe）===")
        fin_coll = db[coll_name]
        di, rows = ffill_fin_to_universe(
            fin_coll,
            base_coll,
            trade_list,
            dry_run=args.dry_run,
            verbose=args.verbose,
            insert_chunk=max(100, args.insert_chunk),
            progress_every=max(0, args.progress_every),
            mongo_attempts=max(1, args.mongo_retries),
            mongo_delay=max(0.1, args.mongo_retry_delay),
        )
        print(
            f"  小结：有插入的交易日 {di} 个，插入行数约 {rows}（dry_run={args.dry_run}）\n"
        )


if __name__ == "__main__":
    main()
