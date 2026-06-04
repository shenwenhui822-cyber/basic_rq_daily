# -*- coding: utf-8 -*-
"""
basic_rq / rq_minute 历史数据质量检查（只读 MongoDB，不写库、不调用米筐 API）。

三类检查（默认起始日来自说明文档.md）：
  1. 唯一性：norm_date + 业务键 仅 1 条
  2. 字段完整：按交易日逐日拉取文档，在内存中检查必填字段是否存在（值可为 null）
  3. 日期完整：economic.trade_dates 区间内每个交易日均有数据

依赖: pip install pymongo

用法::

    python check_historical_data/check_historical_data.py
    python check_historical_data/check_historical_data.py --collection rq_minute_none_2026
    python check_historical_data/check_historical_data.py --skip-minute
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pymongo

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_REPORT_DIR = _SCRIPT_DIR / "reports"

mongodb_url = "mongodb://reader:readonly_wonderwz@192.168.110.199:27018"
DEFAULT_MONGO_ALIAS = "wonderwz27018_ro"

MINUTE_COLLECTION_PREFIX = "rq_minute_none_"

MAX_DUP_DETAIL = 500
MAX_REPORT_SAMPLES = 20
MAX_MISSING_DATES_LIST = 100

_RUN_T0 = time.perf_counter()
_VERBOSE = True


class ReportWriter:
    """边运行边写入报告/进度文件。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = path.open("w", encoding="utf-8")

    def write_line(self, line: str = "") -> None:
        self._fp.write(line + "\n")
        self._fp.flush()

    def write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._fp.write(line + "\n")
        self._fp.flush()

    def close(self) -> None:
        self._fp.close()


class IssueIdsWriter:
    """将全部问题明细写入独立 txt（按表分节，边检查边写入）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = path.open("w", encoding="utf-8")
        self._counts = {"uniqueness": 0, "fields": 0}

    def write_line(self, line: str = "") -> None:
        self._fp.write(line + "\n")
        self._fp.flush()

    def write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._fp.write(line + "\n")
        self._fp.flush()

    def write_header(self, *, mongo_host: str, report_path: Path) -> None:
        self.write_lines(
            [
                "basic_rq / rq_minute 历史数据质量问题明细（全部 _id / 日期）",
                f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Mongo: {mongo_host}",
                f"对应检查报告: {report_path.name}",
                "格式: 每节标明 库.集合；唯一性/缺字段为文档 _id；日期问题为日期列表",
                "",
            ]
        )

    def begin_table(self, spec: TableSpec, start: str, end: str) -> None:
        self._counts = {"uniqueness": 0, "fields": 0}
        self.write_lines(
            [
                "=" * 60,
                f"表: {spec.db}.{spec.collection}",
                f"检查区间 norm_date: {start} ~ {end}",
                "",
            ]
        )

    def write_uniqueness_issues(self, spec: TableSpec, dup_groups: list[dict[str, Any]]) -> int:
        self.write_line(f"--- [1] 唯一性重复文档 _id（norm_date + {' + '.join(spec.unique_key)}）---")
        if not dup_groups:
            self.write_line("（无）")
            self.write_line("")
            return 0

        n_ids = 0
        for i, g in enumerate(dup_groups, start=1):
            gid = g["_id"] or {}
            ids = g.get("sample_ids") or []
            key_parts = ", ".join(f"{k}={gid.get(k)!r}" for k in spec.unique_key)
            self.write_line(
                f"# 重复组 {i} | norm_date={gid.get('norm_date')} | {key_parts} | n={int(g['n'])}"
            )
            for oid in ids:
                self.write_line(f"_id={oid}")
                n_ids += 1
            self.write_line("")
        self._counts["uniqueness"] = n_ids
        return n_ids

    def begin_field_section(self) -> None:
        self._counts["fields"] = 0
        self.write_line(
            "--- [2] 缺字段文档 _id（值可为 null 时字段须存在；下列为全部问题 _id）---"
        )

    def     write_field_issue_line(
        self,
        *,
        doc_id: Any,
        norm_date: str,
        code_rq: Any = None,
        code: Any = None,
        indus_code: Any = None,
        time: Any = None,
        missing_fields: list[str],
    ) -> None:
        biz = code_rq or code or indus_code or ""
        time_part = f" time={time}" if time is not None else ""
        self.write_line(
            f"_id={doc_id} | norm_date={norm_date}{time_part} | "
            f"业务键={biz} | 缺字段: {', '.join(missing_fields)}"
        )
        self._counts["fields"] += 1

    def end_field_section(self) -> int:
        if self._counts["fields"] == 0:
            self.write_line("（无）")
        self.write_line("")
        return self._counts["fields"]

    def write_date_issues(
        self,
        *,
        missing_dates: list[str],
        extra_dates: list[str],
        low_doc_days: list[tuple[str, int]],
        expected_docs_per_day: int | None,
        present_map: dict[str, int],
    ) -> None:
        self.write_line("--- [3] 日期覆盖问题（非文档 _id）---")
        if not missing_dates and not extra_dates and not low_doc_days:
            self.write_line("（无）")
            self.write_line("")
            return

        if missing_dates:
            self.write_line(f"缺失交易日（共 {len(missing_dates)} 个）:")
            for d in missing_dates:
                self.write_line(f"  {d}")
        if extra_dates:
            self.write_line(f"非交易日历但有数据（共 {len(extra_dates)} 个）:")
            for d in extra_dates:
                self.write_line(f"  {d} | 文档数={present_map.get(d, 0)}")
        if low_doc_days:
            self.write_line(f"文档数低于预期（共 {len(low_doc_days)} 个）:")
            for d, n in low_doc_days:
                self.write_line(f"  {d} | 文档数={n} | 预期>={expected_docs_per_day}")
        self.write_line("")

    def close(self) -> None:
        self._fp.close()


_REPORT_WRITER: ReportWriter | None = None


def _log(msg: str, *, level: str = "INFO") -> None:
    elapsed = time.perf_counter() - _RUN_T0
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts} +{elapsed:7.1f}s] [{level}] {msg}"
    if _VERBOSE:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("gbk", errors="replace").decode("gbk"), flush=True)
    if _REPORT_WRITER is not None:
        _REPORT_WRITER.write_line(line)


@dataclass(frozen=True)
class TableSpec:
    db: str
    collection: str
    default_start: str
    required_fields: tuple[str, ...]
    nullable_fields: tuple[str, ...]
    unique_key: tuple[str, ...]
    rule_desc: str
    expected_docs_per_day: int | None = None
    year_sharded: bool = False
    """True 时按年分表，集合名为 rq_minute_none_YYYY"""


@dataclass(frozen=True)
class CheckRunResult:
    report_path: Path
    issue_ids_path: Path
    passed: bool
    check_start: str
    check_end: str
    elapsed_seconds: float
    summary_lines: tuple[str, ...]
    table_summary: tuple[tuple[str, dict[str, bool]], ...]

    @property
    def summary_text(self) -> str:
        return "\n".join(self.summary_lines)


def resolve_mongodb_url(
    *,
    mongo_url: str | None = None,
    mongo_alias: str | None = None,
) -> str:
    if mongo_url:
        return mongo_url
    alias = (mongo_alias or DEFAULT_MONGO_ALIAS).strip()
    if alias:
        from mongo_connect import _build_uri, _resolve_config

        return _build_uri(_resolve_config(alias))
    return mongodb_url


# 说明文档.md · 已存在数据起始日 + JSON 示例字段
TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        db="basic_rq",
        collection="rq_base_info",
        default_start="2015-01-05",
        required_fields=(
            "date",
            "code",
            "code_rq",
            "trade_status",
            "riskwarning",
            "list_days",
        ),
        nullable_fields=(),
        unique_key=("code_rq",),
        rule_desc="全市场标的基础信息；每日每只股票一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_daily_price_none",
        default_start="2020-01-02",
        required_fields=(
            "date",
            "code",
            "code_rq",
            "open",
            "high",
            "low",
            "close",
            "prev_close",
            "volume",
            "total_turnover",
            "limit_up",
            "limit_down",
        ),
        nullable_fields=(),
        unique_key=("code_rq",),
        rule_desc="全市场不复权日线；每日每只股票一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_basic_financial",
        default_start="2015-01-05",
        required_fields=(
            "date",
            "code",
            "code_rq",
            "mkt_cap_ard",
            "total_shares",
            "free_float_shares",
            "or_ttm",
            "gr_ttm",
            "netprofit_ttm",
            "operatecashflow_ttm",
        ),
        nullable_fields=(),
        unique_key=("code_rq",),
        rule_desc="全市场基本财务截面；每日每只股票一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_base_index",
        default_start="2015-01-05",
        required_fields=(
            "date",
            "code",
            "code_rq",
            "in_SZ50",
            "in_HS300",
            "in_ZZ500",
            "in_ZZ1000",
            "in_ZZ2000",
        ),
        nullable_fields=(),
        unique_key=("code_rq",),
        rule_desc="宽基指数成分 0/1；每日每只股票一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_bench",
        default_start="2020-01-02",
        required_fields=(
            "date",
            "code",
            "code_rq",
            "rq_index_code",
            "rq_bench_substitute",
            "pct_chg",
            "volume",
            "amt",
            "pre_close",
            "close",
            "open",
            "high",
            "low",
        ),
        nullable_fields=(),
        unique_key=("code",),
        rule_desc="基准指数日行情；每日 6 个 Wind 指数 code 各一条",
        expected_docs_per_day=6,
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_daily_indusSWL2",
        default_start="2020-01-02",
        required_fields=("date", "indus_code", "name", "stocks"),
        nullable_fields=(),
        unique_key=("indus_code",),
        rule_desc="申万二级行业成分；每日每个 indus_code 一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_daily_indusSWL2_price",
        default_start="2020-01-02",
        required_fields=(
            "date",
            "indus_code",
            "name",
            "stocks",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "total_turnover",
        ),
        nullable_fields=(),
        unique_key=("indus_code",),
        rule_desc="申万二级行业指数价量；每日每个 indus_code 一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_quarterly",
        default_start="2015-01-15",
        required_fields=(
            "date",
            "code",
            "code_w",
            "code_rq",
            "rptdate",
            "stm_issuingdate",
            "monetary_cap",
            "minority_int",
            "tot_equity",
            "tot_assets",
            "tot_liab",
            "interestexpense_ttm",
            "gc_ttm2",
            "or_ttm2",
            "netprofit_ttm2",
            "ebit2_ttm",
        ),
        nullable_fields=("interestexpense_ttm",),
        unique_key=("code_rq",),
        rule_desc="季报 PIT 截面；每日每只股票一条",
    ),
    TableSpec(
        db="basic_rq",
        collection="rq_yearly",
        default_start="2015-01-15",
        required_fields=(
            "date",
            "code",
            "code_w",
            "code_rq",
            "rptdate",
            "stm_issuingdate",
            "stmnote_tax",
            "stmnote_audit_category",
        ),
        nullable_fields=(),
        unique_key=("code_rq",),
        rule_desc="年报 PIT 附注截面；每日每只股票一条",
    ),
    TableSpec(
        db="rq_minute",
        collection="rq_minute_none_{year}",
        default_start="2026-01-05",
        required_fields=(
            "date",
            "time",
            "code",
            "code_rq",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "total_turnover",
        ),
        nullable_fields=(),
        unique_key=("time", "code_rq"),
        rule_desc="全市场 1 分钟线（按年分表 rq_minute_none_YYYY）；每日每 time+code_rq 一条",
        year_sharded=True,
    ),
)


def _list_filterable_collections() -> list[str]:
    names: list[str] = []
    for s in TABLE_SPECS:
        if s.year_sharded:
            names.append("rq_minute")
        else:
            names.append(s.collection)
    return names


def _spec_matches_filter(spec: TableSpec, want: set[str]) -> bool:
    if spec.collection in want:
        return True
    if spec.year_sharded:
        if "rq_minute" in want:
            return True
        return any(w.startswith(MINUTE_COLLECTION_PREFIX) for w in want)
    return False


def _expand_check_targets(
    spec: TableSpec,
    client: pymongo.MongoClient,
    start_n: str,
    end_n: str,
    collection_filter: set[str] | None,
) -> list[tuple[TableSpec, str, str]]:
    """展开检查目标；按年分表时返回多个 (spec, start, end)。"""
    existing = set(client[spec.db].list_collection_names())
    if not spec.year_sharded:
        if spec.collection not in existing:
            return []
        return [(spec, start_n, end_n)]

    start_year = int(start_n[:4])
    end_year = int(end_n[:4])
    targets: list[tuple[TableSpec, str, str]] = []

    for year in range(start_year, end_year + 1):
        coll = f"{MINUTE_COLLECTION_PREFIX}{year}"
        if collection_filter and coll not in collection_filter and "rq_minute" not in collection_filter:
            continue
        if coll not in existing:
            continue
        shard_start = max(start_n, f"{year}-01-01")
        shard_end = min(end_n, f"{year}-12-31")
        if shard_start > shard_end:
            continue
        targets.append((replace(spec, collection=coll), shard_start, shard_end))
    return targets


def _norm_date_str(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s.replace("/", "-")[:10]


def _norm_date_add_fields() -> dict[str, Any]:
    return {
        "norm_date": {
            "$substr": [
                {"$replaceAll": {"input": {"$toString": "$date"}, "find": "/", "replacement": "-"}},
                0,
                10,
            ]
        }
    }


def _range_match(start: str, end: str) -> dict[str, Any]:
    return {"norm_date": {"$gte": start, "$lte": end}}


def _base_pipeline(start: str, end: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"date": {"$exists": True, "$ne": None}}},
        {"$addFields": _norm_date_add_fields()},
        {"$match": _range_match(start, end)},
    ]


def fetch_trade_dates(client: pymongo.MongoClient, start: str, end: str) -> list[str]:
    _log(f"读取交易日历 economic.trade_dates [{start} ~ {end}] …")
    t0 = time.perf_counter()
    rows = list(
        client.economic.trade_dates.find(
            {"trade_date": {"$gte": start, "$lte": end}},
            {"_id": 0, "trade_date": 1},
        ).sort("trade_date", 1)
    )
    out: list[str] = []
    for r in rows:
        d = _norm_date_str(r.get("trade_date"))
        if d:
            out.append(d)
    _log(f"交易日历共 {len(out)} 天，耗时 {time.perf_counter() - t0:.1f}s")
    return out


def resolve_end_date(client: pymongo.MongoClient, end: str | None) -> str:
    if end:
        return _norm_date_str(end) or end
    doc = client.economic.trade_dates.find_one(
        {},
        {"_id": 0, "trade_date": 1},
        sort=[("trade_date", -1)],
    )
    if not doc or not doc.get("trade_date"):
        return datetime.now().strftime("%Y-%m-%d")
    return _norm_date_str(doc["trade_date"]) or datetime.now().strftime("%Y-%m-%d")


def check_uniqueness(coll: Any, spec: TableSpec, start: str, end: str) -> dict[str, Any]:
    _log(f"{spec.collection} · [1/3] 唯一性 aggregate …")
    t0 = time.perf_counter()
    gid: dict[str, Any] = {"norm_date": "$norm_date"}
    for f in spec.unique_key:
        gid[f] = f"${f}"

    pipeline = _base_pipeline(start, end) + [
        {
            "$group": {
                "_id": gid,
                "n": {"$sum": 1},
                "raw_dates": {"$addToSet": "$date"},
                "sample_ids": {"$push": "$_id"},
            }
        },
        {"$match": {"n": {"$gt": 1}}},
        {"$sort": {"n": -1, "_id.norm_date": 1}},
    ]
    dup_groups = list(coll.aggregate(pipeline, allowDiskUse=True))
    dup_id_count = sum(len(g.get("sample_ids") or []) for g in dup_groups)
    _log(
        f"{spec.collection} · [1/3] 唯一性完成：重复组 {len(dup_groups)}，"
        f"涉及文档 {dup_id_count} 条，耗时 {time.perf_counter() - t0:.1f}s"
    )
    return {"dup_groups": dup_groups, "dup_id_count": dup_id_count}


def _field_projection(spec: TableSpec) -> dict[str, int]:
    proj: dict[str, int] = {"_id": 1, "date": 1, "code_rq": 1, "code": 1, "indus_code": 1}
    for f in spec.required_fields:
        proj[f] = 1
    return proj


def _doc_missing_fields(doc: dict[str, Any], required_fields: tuple[str, ...]) -> list[str]:
    """字段不存在则缺失；值为 null 视为存在。"""
    return [f for f in required_fields if f not in doc]


def _iter_docs_for_day(coll: Any, spec: TableSpec, day: str) -> Any:
    pipeline = _base_pipeline(day, day) + [{"$project": _field_projection(spec)}]
    return coll.aggregate(pipeline, allowDiskUse=True, batchSize=2000)


def check_field_completeness(
    coll: Any,
    spec: TableSpec,
    start: str,
    end: str,
    trade_dates: list[str],
    issue_writer: IssueIdsWriter | None = None,
) -> dict[str, Any]:
    _log(
        f"{spec.collection} · [2/3] 按交易日逐日拉取 [{start} ~ {end}]，"
        f"共 {len(trade_dates)} 天 …"
    )
    t0 = time.perf_counter()
    per_field = dict.fromkeys(spec.required_fields, 0)
    total = 0
    incomplete_count = 0
    samples: list[dict[str, Any]] = []

    if issue_writer is not None:
        issue_writer.begin_field_section()

    for day_idx, day in enumerate(trade_dates, start=1):
        t_day = time.perf_counter()
        day_total = 0
        day_incomplete = 0

        for doc in _iter_docs_for_day(coll, spec, day):
            day_total += 1
            total += 1
            missing = _doc_missing_fields(doc, spec.required_fields)
            if missing:
                day_incomplete += 1
                incomplete_count += 1
                for f in missing:
                    per_field[f] += 1
                if len(samples) < MAX_REPORT_SAMPLES:
                    samples.append(
                        {
                            "_id": doc.get("_id"),
                            "norm_date": day,
                            "time": doc.get("time"),
                            "code_rq": doc.get("code_rq"),
                            "code": doc.get("code"),
                            "indus_code": doc.get("indus_code"),
                            "missing_fields": missing,
                        }
                    )
                if issue_writer is not None:
                    issue_writer.write_field_issue_line(
                        doc_id=doc.get("_id"),
                        norm_date=day,
                        code_rq=doc.get("code_rq"),
                        code=doc.get("code"),
                        indus_code=doc.get("indus_code"),
                        time=doc.get("time"),
                        missing_fields=missing,
                    )

        _log(
            f"{spec.collection} · [2/3] 日期 ({day_idx}/{len(trade_dates)}) {day}："
            f"{day_total} 条，缺字段 {day_incomplete} 条，"
            f"耗时 {time.perf_counter() - t_day:.1f}s"
        )

    if issue_writer is not None:
        issue_writer.end_field_section()

    _log(
        f"{spec.collection} · [2/3] 字段检查完成：总 {total} 条，"
        f"缺字段 {incomplete_count} 条，耗时 {time.perf_counter() - t0:.1f}s"
    )
    return {
        "total": total,
        "incomplete_count": incomplete_count,
        "complete_count": total - incomplete_count,
        "samples": samples,
        "per_field_missing": per_field,
    }


def check_date_coverage(
    coll: Any,
    spec: TableSpec,
    start: str,
    end: str,
    trade_dates: list[str],
) -> dict[str, Any]:
    _log(f"{spec.collection} · [3/3] 日期覆盖 …")
    t0 = time.perf_counter()
    expected_set = set(trade_dates)

    _log(f"{spec.collection} · [3/3] 按 norm_date 分组统计 …")
    t_group = time.perf_counter()
    pipeline = _base_pipeline(start, end) + [
        {"$group": {"_id": "$norm_date", "doc_count": {"$sum": 1}}},
    ]
    rows = list(coll.aggregate(pipeline, allowDiskUse=True))
    _log(
        f"{spec.collection} · [3/3] 分组完成：{len(rows)} 个日期，"
        f"耗时 {time.perf_counter() - t_group:.1f}s"
    )
    present_map = {r["_id"]: int(r["doc_count"]) for r in rows if r.get("_id")}
    present_set = set(present_map)

    missing = sorted(expected_set - present_set)
    extra = sorted(present_set - expected_set)

    low_doc_days: list[tuple[str, int]] = []
    if spec.expected_docs_per_day is not None:
        for d in sorted(present_set & expected_set):
            n = present_map.get(d, 0)
            if n < spec.expected_docs_per_day:
                low_doc_days.append((d, n))

    _log(
        f"{spec.collection} · [3/3] 日期覆盖完成：缺失 {len(missing)} 天，"
        f"多余 {len(extra)} 天，总耗时 {time.perf_counter() - t0:.1f}s"
    )
    return {
        "expected_trade_days": len(trade_dates),
        "present_trade_days": len(present_set & expected_set),
        "missing_dates": missing,
        "extra_dates": extra,
        "low_doc_days": low_doc_days,
        "present_map": present_map,
    }


def _dup_issue(raw_dates: list[Any], n: int) -> str:
    dates = [str(d) for d in raw_dates if d is not None]
    if len(set(dates)) > 1:
        return "同一交易日 date 格式不一致导致重复"
    if n == 2:
        return "同一 date 下重复插入（多为未先删后插）"
    return f"同一交易日同一标的重复 {n} 次"


def _write_table_report(
    lines: list[str],
    spec: TableSpec,
    start: str,
    end: str,
    *,
    uniq: dict[str, Any],
    fields: dict[str, Any],
    dates: dict[str, Any],
    issue_ids_path: Path | None = None,
) -> dict[str, bool]:
    issues = {"uniqueness": False, "fields": False, "dates": False}

    lines.extend(
        [
            "=" * 60,
            f"{spec.db}.{spec.collection}",
            f"说明: {spec.rule_desc}",
            f"说明文档默认起始日: {spec.default_start}",
            f"本次检查区间 norm_date: {start} ~ {end}",
            f"必填字段: {', '.join(spec.required_fields)}",
            "字段规则: 须存在该字段，值可为 null",
        ]
    )
    lines.append("")

    lines.append("--- [1] 唯一性（norm_date + " + " + ".join(spec.unique_key) + "）---")
    dup_groups = uniq["dup_groups"]
    dup_id_count = int(uniq.get("dup_id_count") or 0)
    if not dup_groups:
        lines.append("结果: [OK] 无重复")
    else:
        issues["uniqueness"] = True
        lines.append(
            f"结果: [FAIL] 重复组 {len(dup_groups)} 组，涉及文档 {dup_id_count} 条"
        )
        if issue_ids_path:
            lines.append(f"全部重复 _id 见: {issue_ids_path.name}")
        for i, g in enumerate(dup_groups[:MAX_DUP_DETAIL], start=1):
            gid = g["_id"] or {}
            n = int(g["n"])
            raw_dates = g.get("raw_dates") or []
            ids = g.get("sample_ids") or []
            key_parts = ", ".join(f"{k}={gid.get(k)!r}" for k in spec.unique_key)
            lines.append(f"  [重复 {i}] {_dup_issue(raw_dates, n)}")
            lines.append(f"    norm_date={gid.get('norm_date')} | {key_parts} | n={n}")
            lines.append(
                f"    date 原始值: {', '.join(repr(d) for d in sorted({str(x) for x in raw_dates}))}"
            )
            lines.append(f"    _id 样例: {', '.join(str(x) for x in ids[:3])}")
        if len(dup_groups) > MAX_DUP_DETAIL:
            lines.append(f"  … 报告内仅展示前 {MAX_DUP_DETAIL} 组，完整 _id 见明细文件")
    lines.append("")

    lines.append("--- [2] 字段完整行（必填字段须存在，值可为 null）---")
    total = fields["total"]
    inc = fields["incomplete_count"]
    complete = fields["complete_count"]
    lines.append(f"区间内文档数: {total}")
    lines.append(f"完整行: {complete} | 缺字段行: {inc}")
    if inc == 0:
        lines.append("结果: [OK] 必填字段均存在")
    else:
        issues["fields"] = True
        lines.append("结果: [FAIL] 存在文档缺少必填字段")
        lines.append("各字段缺字段计数:")
        for f, c in fields["per_field_missing"].items():
            if c:
                lines.append(f"  {f}: {c} 条")
        if issue_ids_path:
            lines.append(
                f"全部缺字段 _id（共 {inc} 条）见: {issue_ids_path.name}"
            )
        samples = fields.get("samples") or []
        if samples:
            lines.append(f"报告内样例（前 {len(samples)} 条）:")
            for s in samples:
                missing = s.get("missing_fields") or []
                lines.append(
                    f"  _id={s.get('_id')} norm_date={s.get('norm_date')} "
                    f"time={s.get('time', '-')} "
                    f"code_rq={s.get('code_rq', s.get('code', s.get('indus_code')))} "
                    f"缺: {', '.join(missing)}"
                )
    lines.append("")

    lines.append("--- [3] 日期完整（交易日覆盖）---")
    exp = dates["expected_trade_days"]
    pres = dates["present_trade_days"]
    missing = dates["missing_dates"]
    extra = dates["extra_dates"]
    lines.append(f"交易日历应有: {exp} 天 | 库中已有: {pres} 天 | 缺失: {len(missing)} 天")
    if spec.expected_docs_per_day:
        lines.append(f"预期每日约 {spec.expected_docs_per_day} 条（{spec.collection}）")
    if not missing and not extra and not dates["low_doc_days"]:
        lines.append("结果: [OK] 交易日覆盖完整")
    else:
        issues["dates"] = True
        lines.append("结果: [FAIL] 日期覆盖有问题")
        if issue_ids_path:
            lines.append(f"全部缺失/异常日期见: {issue_ids_path.name}")
        if missing:
            lines.append(
                f"缺失交易日（共 {len(missing)} 个，报告内列出前 {MAX_MISSING_DATES_LIST} 个）:"
            )
            for d in missing[:MAX_MISSING_DATES_LIST]:
                lines.append(f"  {d}")
            if len(missing) > MAX_MISSING_DATES_LIST:
                lines.append(f"  … 完整列表见明细文件")
        if extra:
            lines.append(f"非交易日历日期但库中有数据（共 {len(extra)} 个）:")
            for d in extra[:MAX_REPORT_SAMPLES]:
                lines.append(f"  {d} 文档数={dates['present_map'].get(d, 0)}")
            if len(extra) > MAX_REPORT_SAMPLES:
                lines.append(f"  … 完整列表见明细文件")
        if dates["low_doc_days"]:
            lines.append("文档数低于预期的交易日:")
            for d, n in dates["low_doc_days"][:MAX_REPORT_SAMPLES]:
                lines.append(f"  {d}: {n} 条（预期>={spec.expected_docs_per_day}）")
            if len(dates["low_doc_days"]) > MAX_REPORT_SAMPLES:
                lines.append(f"  … 完整列表见明细文件")
    lines.append("")
    return issues


def run_check(
    *,
    mongo_url: str | None = None,
    mongo_alias: str | None = None,
    end: str | None = None,
    start_override: str | None = None,
    collections: list[str] | None = None,
    report_path: Path | None = None,
    verbose: bool = True,
    skip_minute: bool = False,
) -> CheckRunResult:
    global _RUN_T0, _VERBOSE, _REPORT_WRITER
    _RUN_T0 = time.perf_counter()
    _VERBOSE = verbose

    mongo_url_resolved = resolve_mongodb_url(mongo_url=mongo_url, mongo_alias=mongo_alias)

    report_path = report_path or (
        _DEFAULT_REPORT_DIR / f"data_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    )
    issue_ids_path = report_path.with_name(report_path.stem + "_issue_ids.txt")

    specs = list(TABLE_SPECS)
    if skip_minute:
        specs = [s for s in specs if not s.year_sharded]
    collection_filter = {c.strip() for c in collections} if collections else None
    if collection_filter:
        specs = [s for s in specs if _spec_matches_filter(s, collection_filter)]
        if not specs:
            raise ValueError(f"未匹配集合，可选: {_list_filterable_collections()}")

    mongo_host = mongo_url_resolved.split("@")[-1] if "@" in mongo_url_resolved else mongo_url_resolved
    writer = ReportWriter(report_path)
    issue_writer = IssueIdsWriter(issue_ids_path)
    _REPORT_WRITER = writer
    issue_writer.write_header(mongo_host=mongo_host, report_path=report_path)
    writer.write_lines(
        [
            "basic_rq / rq_minute 历史数据质量检查报告（只读 MongoDB）",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Mongo: {mongo_host}",
            f"问题明细 _id 文件: {issue_ids_path.name}",
            "说明: 各表起始日默认取自说明文档.md；可用 --start 全局覆盖。",
            "说明: 下方进度与检查结果边运行边写入本文件。",
            "",
            "=== 运行进度 ===",
        ]
    )

    _log("开始检查 basic_rq / rq_minute 历史数据质量（只读）")
    _log(f"报告文件: {report_path}")
    _log(f"问题 _id 明细: {issue_ids_path}")
    _log(f"配置表数: {len(specs)}")
    if start_override:
        _log(f"全局起始日 --start: {start_override}")
    if end:
        _log(f"截止日 --end: {end}")

    _log(f"连接 MongoDB {mongo_host} …")
    t_connect = time.perf_counter()
    client = pymongo.MongoClient(
        mongo_url_resolved,
        serverSelectionTimeoutMS=30_000,
        connectTimeoutMS=30_000,
        socketTimeoutMS=600_000,
    )
    client.admin.command("ping")
    _log(f"MongoDB 连接成功，耗时 {time.perf_counter() - t_connect:.1f}s")

    _log("解析截止交易日 …")
    end_n = resolve_end_date(client, end)
    _log(f"检查截止 norm_date: {end_n}")

    check_targets: list[tuple[TableSpec, str, str]] = []
    skip_entries: list[tuple[TableSpec, str, str, str]] = []
    for spec in specs:
        start_n = _norm_date_str(start_override) if start_override else spec.default_start
        if start_n > end_n:
            skip_entries.append((spec, start_n, end_n, f"起始日 {start_n} 晚于截止日 {end_n}"))
            continue
        expanded = _expand_check_targets(spec, client, start_n, end_n, collection_filter)
        if expanded:
            check_targets.extend(expanded)
        else:
            if spec.year_sharded:
                reason = f"区间内无 {MINUTE_COLLECTION_PREFIX}* 集合"
            else:
                reason = "集合不存在"
            skip_entries.append((spec, start_n, end_n, reason))

    summary: list[tuple[str, dict[str, bool]]] = []
    any_fail = False
    total_tables = len(check_targets) + len(skip_entries)
    results_section_opened = False

    def _flush_table_lines(table_lines: list[str]) -> None:
        nonlocal results_section_opened
        if not results_section_opened:
            writer.write_line("")
            writer.write_line("=== 检查结果 ===")
            writer.write_line("")
            results_section_opened = True
        writer.write_lines(table_lines)

    _log(f"待检查集合数: {len(check_targets)}（跳过 {len(skip_entries)}）")

    table_idx = 0
    for spec, start_n, end_n, reason in skip_entries:
        table_idx += 1
        _log(f"表 ({table_idx}/{total_tables}) {spec.db}.{spec.collection} · [SKIP] {reason}", level="WARN")
        _flush_table_lines(
            [
                "=" * 60,
                f"{spec.db}.{spec.collection}",
                f"结果: [SKIP] {reason}",
                "",
            ]
        )

    if not check_targets and not skip_entries:
        _log("无待检查集合", level="WARN")

    for spec, start_n, end_n in check_targets:
        table_idx += 1
        _log("=" * 50)
        _log(f"表 ({table_idx}/{total_tables}) {spec.db}.{spec.collection}")
        t_table = time.perf_counter()
        table_lines: list[str] = []

        _log(f"{spec.collection} · 检查区间 norm_date: {start_n} ~ {end_n}")
        trade_dates = fetch_trade_dates(client, start_n, end_n)
        coll = client[spec.db][spec.collection]
        issue_writer.begin_table(spec, start_n, end_n)
        uniq = check_uniqueness(coll, spec, start_n, end_n)
        issue_writer.write_uniqueness_issues(spec, uniq["dup_groups"])
        fields = check_field_completeness(
            coll, spec, start_n, end_n, trade_dates, issue_writer=issue_writer
        )
        dates = check_date_coverage(coll, spec, start_n, end_n, trade_dates)
        issue_writer.write_date_issues(
            missing_dates=dates["missing_dates"],
            extra_dates=dates["extra_dates"],
            low_doc_days=dates["low_doc_days"],
            expected_docs_per_day=spec.expected_docs_per_day,
            present_map=dates["present_map"],
        )
        issues = _write_table_report(
            table_lines,
            spec,
            start_n,
            end_n,
            uniq=uniq,
            fields=fields,
            dates=dates,
            issue_ids_path=issue_ids_path,
        )
        _flush_table_lines(table_lines)
        summary.append((f"{spec.db}.{spec.collection}", issues))
        if any(issues.values()):
            any_fail = True
            _log(
                f"{spec.collection} · 本表完成 [FAIL] "
                f"唯一性={'FAIL' if issues['uniqueness'] else 'OK'} | "
                f"字段={'FAIL' if issues['fields'] else 'OK'} | "
                f"日期={'FAIL' if issues['dates'] else 'OK'}，"
                f"耗时 {time.perf_counter() - t_table:.1f}s"
            )
        else:
            _log(
                f"{spec.collection} · 本表完成 [OK]，"
                f"耗时 {time.perf_counter() - t_table:.1f}s"
            )

    summary_lines = ["=" * 60, "汇总", "=" * 60]
    for name, issues in summary:
        parts = []
        for k, label in (
            ("uniqueness", "唯一性"),
            ("fields", "字段"),
            ("dates", "日期"),
        ):
            parts.append(f"{label}:{'FAIL' if issues.get(k) else 'OK'}")
        summary_lines.append(f"{name}: {' | '.join(parts)}")

    summary_lines.append("")
    summary_lines.append(
        "总体结论: [FAIL] 存在问题，详见上文。"
        if any_fail
        else "总体结论: [OK] 已检查项均通过。"
    )

    writer.write_line("")
    writer.write_lines(summary_lines)
    elapsed = time.perf_counter() - _RUN_T0
    _log(
        "全部完成，总耗时 "
        f"{elapsed:.1f}s，"
        f"总体结论: {'[FAIL]' if any_fail else '[OK]'}"
    )
    _log(f"报告已写入: {report_path}")
    _log(f"问题 _id 明细已写入: {issue_ids_path}")
    writer.close()
    issue_writer.close()
    _REPORT_WRITER = None

    if not verbose:
        text = "\n".join(summary_lines) + "\n"
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("gbk", errors="replace").decode("gbk"))

    range_starts = [s for _, s, _ in check_targets]
    check_start = min(range_starts) if range_starts else (_norm_date_str(start_override) or end_n)

    return CheckRunResult(
        report_path=report_path,
        issue_ids_path=issue_ids_path,
        passed=not any_fail,
        check_start=check_start,
        check_end=end_n,
        elapsed_seconds=elapsed,
        summary_lines=tuple(summary_lines),
        table_summary=tuple(summary),
    )


def main() -> int:
    p = argparse.ArgumentParser(description="basic_rq 历史数据质量检查（只读 MongoDB）")
    p.add_argument(
        "--start",
        default=None,
        help="全局覆盖起始 norm_date；默认各表使用说明文档中的 default_start",
    )
    p.add_argument("--end", default=None, help="截止 norm_date；默认 economic.trade_dates 最新日")
    p.add_argument("--collection", action="append", dest="collections", help="只检查指定集合")
    p.add_argument("--mongodb-url", default=None, help="Mongo 连接 URL；未指定则用 --mongo-alias")
    p.add_argument(
        "--mongo-alias",
        default=DEFAULT_MONGO_ALIAS,
        help=f"Mongo 别名（mongo_connect.py），默认 {DEFAULT_MONGO_ALIAS}",
    )
    p.add_argument("--output", default=None, help="报告 txt 路径")
    p.add_argument(
        "--quiet",
        action="store_true",
        help="不打印进度日志（结束时仍输出报告全文）",
    )
    p.add_argument(
        "--skip-minute",
        action="store_true",
        help="跳过 rq_minute 按年分表（数据量大，耗时长）",
    )
    args = p.parse_args()
    result = run_check(
        mongo_url=args.mongodb_url,
        mongo_alias=None if args.mongodb_url else args.mongo_alias,
        end=args.end,
        start_override=args.start,
        collections=args.collections,
        report_path=Path(args.output) if args.output else None,
        verbose=not args.quiet,
        skip_minute=args.skip_minute,
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
