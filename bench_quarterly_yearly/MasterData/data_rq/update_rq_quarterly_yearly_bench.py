"""
每日更新 basic_rq 三张新表（不改 rq_base_info / rq_basic_financial / rq_daily_price_none）：
  - rq_quarterly  （对标 Wind w_quarterly）
  - rq_yearly     （对标 Wind w_yearly）
  - rq_bench      （对标 Wind w_bench，实现见 **update_rq_bench.py**）

依赖：当日 rq_base_info 已更新；rqdatac 已 init。
执行顺序建议：update_rqbaseInfo -> （财务/日线）-> 本脚本。

rq_bench 指数说明见 **update_rq_bench.py** 模块文档。
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any
import time

import os
import sys

import numpy as np
import pandas as pd
import rqdatac as rq

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from DataBase.db_client import get_client
from Utils.utils_datetime import get_season_key_day

from MasterData.data_rq.update_rq_bench import create_indexes_rq_bench, update_rq_bench

# 与项目内其他 data_rq 脚本保持一致（可改为环境变量）
try:
    rq.init("18616633529", "wuzhi2020")
    print("✅ RQData 连接成功 (update_rq_quarterly_yearly_bench)")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def _norm_day(s: str) -> str:
    s = str(s).strip()
    if "/" in s:
        return pd.Timestamp(s.replace("/", "-")).strftime("%Y-%m-%d")
    return pd.Timestamp(s).strftime("%Y-%m-%d")


def _day_variants(s: str) -> list[str]:
    s = _norm_day(s)
    y, m, d = s.split("-")
    return list(dict.fromkeys([s, f"{y}/{m}/{d}"]))


def _rq_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def _rq_to_code_w(code_rq: str) -> str:
    if code_rq.endswith(".XSHG"):
        return code_rq.split(".")[0] + ".SH"
    if code_rq.endswith(".XSHE"):
        return code_rq.split(".")[0] + ".SZ"
    return code_rq


def _iso_to_quarter(rpt: str) -> str:
    d = dt.datetime.strptime(str(rpt)[:10], "%Y-%m-%d").date()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}q{q}"


def _quarter_last_day(q: str) -> str:
    """'2024q3' -> '2024-09-30'"""
    y, qn = q.split("q")
    y = int(y)
    m = int(qn) * 3
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-{last:02d}"


def _bool_rpt_first(today: str, pre_trade_day: str) -> bool:
    ll = ["01", "04", "07", "10"]
    if today[5:7] in ll and pre_trade_day[5:7] not in ll:
        return True
    return False


def _season_params(today: str, pre_trade_day: str) -> tuple[str, str, str, str, str]:
    td = dt.datetime.strptime(_norm_day(today), "%Y-%m-%d")
    if _bool_rpt_first(_norm_day(today), _norm_day(pre_trade_day)):
        td = dt.datetime.strptime(_norm_day(pre_trade_day), "%Y-%m-%d")
    keys = get_season_key_day(td)
    return keys[0], keys[1], keys[2], keys[3], keys[4]


def _load_codes_from_base(table: Any, day: str) -> list[str]:
    variants = _day_variants(day)
    df = pd.DataFrame(table.find({"date": {"$in": variants}}, {"_id": 0, "code_rq": 1}))
    if df.empty or "code_rq" not in df.columns:
        return []
    return df["code_rq"].dropna().astype(str).drop_duplicates().tolist()


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})


def _pit_quarterly(
    codes: list[str],
    quarter: str,
    pre_trade_day: str,
) -> pd.DataFrame:
    """单报告期 PIT；列名映射到 Wind 风格小写。"""
    if not codes:
        return pd.DataFrame()
    date_arg = _norm_day(pre_trade_day).replace("-", "")
    # 勿在 fields 中请求 info_date：新版 rqdatac 会报 invalid field，但返回表仍带 info_date 列
    fields_try = [
        [
            "cash_equivalent",
            "minority_interest",
            "total_equity",
            "total_assets",
            "total_liabilities",
            "financing_interest_expense",
            "total_expense",
            "operating_revenue",
            "net_profit_parent_company",
            "ebit",
        ],
        [
            "cash_equivalent",
            "minority_interest",
            "total_equity",
            "total_assets",
            "total_liabilities",
            "financing_interest_expense",
            "total_expense",
            "operating_revenue",
            "net_profit_parent_company",
        ],
    ]
    last_err = None
    for fields in fields_try:
        try:
            raw = rq.get_pit_financials_ex(
                order_book_ids=codes,
                fields=fields,
                start_quarter=quarter,
                end_quarter=quarter,
                date=date_arg,
                statements="latest",
                market="cn",
            )
            if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
                return pd.DataFrame()
            df = raw.reset_index()
            if "order_book_id" not in df.columns:
                for c in df.columns:
                    if "order_book" in c.lower() or c in ("index", "level_0"):
                        df = df.rename(columns={c: "order_book_id"})
                        break
            if "order_book_id" not in df.columns:
                print("⚠️ pit 返回无 order_book_id 列，跳过")
                return pd.DataFrame()
            rename = {
                "cash_equivalent": "monetary_cap",
                "minority_interest": "minority_int",
                "total_equity": "tot_equity",
                "total_assets": "tot_assets",
                "total_liabilities": "tot_liab",
                "financing_interest_expense": "interestexpense_ttm",
                "total_expense": "gc_ttm2",
                "operating_revenue": "or_ttm2",
                "net_profit_parent_company": "netprofit_ttm2",
                "ebit": "ebit2_ttm",
            }
            for a, b in rename.items():
                if a in df.columns:
                    df[b] = df[a]
            if "ebit" in df.columns and "ebit2_ttm" not in df.columns:
                df["ebit2_ttm"] = df["ebit"]
            df["rptdate"] = _quarter_last_day(quarter)
            if "info_date" in df.columns:
                df["stm_issuingdate"] = pd.to_datetime(df["info_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            else:
                df["stm_issuingdate"] = None
            df["code_rq"] = df["order_book_id"].astype(str)
            df["code"] = df["code_rq"].map(_rq_to_display)
            df["code_w"] = df["code_rq"].map(_rq_to_code_w)
            return df
        except Exception as e:
            last_err = e
            continue
    print(f"⚠️ get_pit_financials_ex 失败 quarter={quarter}: {last_err}")
    return pd.DataFrame()


def _disclosure_ok(stm: Any) -> bool:
    if stm is None or (isinstance(stm, float) and np.isnan(stm)):
        return False
    s = str(stm)[:10]
    if s < "1990-01-01":
        return False
    return True


def _merge_quarterly_cohorts(
    codes: list[str],
    latest_report: str,
    last_report: str,
    last_last_report: str,
    pre_trade_day: str,
) -> pd.DataFrame:
    """对齐 Wind：先取最近报告期，未披露则依次用上一期、上上期。"""
    q1 = _iso_to_quarter(latest_report)
    q2 = _iso_to_quarter(last_report)
    q3 = _iso_to_quarter(last_last_report)

    df1 = _pit_quarterly(codes, q1, pre_trade_day)
    if df1.empty:
        return df1
    mask = df1["stm_issuingdate"].map(_disclosure_ok) if "stm_issuingdate" in df1.columns else pd.Series(False, index=df1.index)
    part_a = df1.loc[mask].copy()
    need_b = df1.loc[~mask, "code_rq"].astype(str).tolist()
    if not need_b:
        return part_a
    df2 = _pit_quarterly(need_b, q2, pre_trade_day)
    if df2.empty:
        return pd.concat([part_a, df1.loc[~mask]], ignore_index=True)
    mask2 = df2["stm_issuingdate"].map(_disclosure_ok) if "stm_issuingdate" in df2.columns else pd.Series(False, index=df2.index)
    part_b = df2.loc[mask2].copy()
    need_c = df2.loc[~mask2, "code_rq"].astype(str).tolist()
    if not need_c:
        return pd.concat([part_a, part_b], ignore_index=True)
    df3 = _pit_quarterly(need_c, q3, pre_trade_day)
    return pd.concat([part_a, part_b, df3], ignore_index=True)


def update_rq_quarterly(
    pre_trade_day: str,
    today: str,
    *,
    mongo_alias: str = "local",
    mongo_db: str = "basic_rq",
    base_coll: str = "rq_base_info",
    target_coll: str = "rq_quarterly",
) -> tuple[bool, int]:
    """返回 (是否成功, 实际写入行数)。"""
    pre_trade_day = _norm_day(pre_trade_day)
    today = _norm_day(today)
    if pre_trade_day >= today:
        print("❌ pre_trade_day 应小于 today")
        return False, 0

    client = get_client(mongo_alias)
    base = client[mongo_db][base_coll]
    table = client[mongo_db][target_coll]

    codes = _load_codes_from_base(base, pre_trade_day)
    if not codes:
        print("❌ rq_base_info 无当日 code_rq，请先更新 rq_base_info")
        return False, 0

    latest_report, last_report, last_last_report, _, _ = _season_params(today, pre_trade_day)
    df = _merge_quarterly_cohorts(codes, latest_report, last_report, last_last_report, pre_trade_day)
    if df.empty:
        print("❌ 季报数据为空")
        return False, 0

    df["date"] = pre_trade_day
    if "minority_int" in df.columns:
        df["minority_int"] = df["minority_int"].fillna(0)

    keep = [
        c
        for c in [
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
        ]
        if c in df.columns
    ]
    df = df[keep]
    df = _df_nan_to_none(df)

    t_insert = time.perf_counter()
    for dv in _day_variants(pre_trade_day):
        table.delete_many({"date": dv})
    table.insert_many(df.to_dict("records"), ordered=False)
    print(f"[timing] mongo_insert: {time.perf_counter() - t_insert:.2f}s")
    print(f"✅ {target_coll} 写入 {len(df)} 条 (date={pre_trade_day})")
    return True


def _pit_yearly_tax(codes: list[str], year_q: str, pre_trade_day: str) -> pd.DataFrame:
    date_arg = _norm_day(pre_trade_day).replace("-", "")
    t0 = time.perf_counter()
    try:
        raw = rq.get_pit_financials_ex(
            order_book_ids=codes,
            fields=["income_tax", "profit_before_tax"],
            start_quarter=year_q,
            end_quarter=year_q,
            date=date_arg,
            statements="latest",
            market="cn",
        )
        if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
            return pd.DataFrame()
        df = raw.reset_index()
        if "order_book_id" not in df.columns:
            for c in list(df.columns):
                if "order_book" in c.lower():
                    df = df.rename(columns={c: "order_book_id"})
                    break
        if "order_book_id" not in df.columns:
            return pd.DataFrame()
        pb = df["profit_before_tax"] if "profit_before_tax" in df.columns else pd.Series(np.nan, index=df.index)
        it = df["income_tax"] if "income_tax" in df.columns else pd.Series(np.nan, index=df.index)
        df["stmnote_tax"] = np.where(pb.notna() & (pb != 0), it / pb, np.nan)
        if "info_date" in df.columns:
            df["stm_issuingdate"] = pd.to_datetime(df["info_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            df["stm_issuingdate"] = None
        df["rptdate"] = _quarter_last_day(year_q)
        df["code_rq"] = df["order_book_id"].astype(str)
        df["code"] = df["code_rq"].map(_rq_to_display)
        df["code_w"] = df["code_rq"].map(_rq_to_code_w)
        print(f"[timing] pit_yearly_tax ({len(codes)} codes): {time.perf_counter() - t0:.2f}s")
        return df
    except Exception as e:
        print(f"⚠️ 年报税负 pit 拉取失败: {e}")
        return pd.DataFrame()


def _audit_map(opinion: Any) -> int:
    """对齐 Wind：无保留类=1，其余=0。"""
    if opinion is None or (isinstance(opinion, float) and np.isnan(opinion)):
        return 0
    s = str(opinion).lower()
    ok = {"unqualified", "unqualified_with_explanation"}
    return 1 if s in ok else 0


def _fetch_audit(codes: list[str], year_q: str, pre_trade_day: str) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    date_arg = _norm_day(pre_trade_day).replace("-", "")
    t0 = time.perf_counter()
    rows: list[dict] = []
    try:
        # 优先尝试批量调用
        a = rq.get_audit_opinion(
            codes,
            start_quarter=year_q,
            end_quarter=year_q,
            date=date_arg,
            type="financial_statements",
        )
        if a is None or (isinstance(a, pd.DataFrame) and a.empty):
            # 全部无结果，统一置 None
            rows = [{"order_book_id": ob, "opinion_type": None} for ob in codes]
        else:
            a = a.reset_index()
            for ob in codes:
                sub = a[(a.get("order_book_id") == ob) & (a.get("type") == "financial_statements")] if "type" in a.columns else a[a.get("order_book_id") == ob]
                if sub.empty:
                    ot = None
                else:
                    ot = sub.iloc[0].get("opinion_type", sub.iloc[0].get("opinion_type".lower(), None))
                rows.append({"order_book_id": ob, "opinion_type": ot})
    except Exception:
        # 批量失败，回退逐只
        for ob in codes:
            try:
                a = rq.get_audit_opinion(
                    ob,
                    start_quarter=year_q,
                    end_quarter=year_q,
                    date=date_arg,
                    type="financial_statements",
                )
                if a is None or (isinstance(a, pd.DataFrame) and a.empty):
                    rows.append({"order_book_id": ob, "opinion_type": None})
                    continue
                if isinstance(a, pd.DataFrame):
                    a = a.reset_index()
                    sub = a[a["type"] == "financial_statements"] if "type" in a.columns else a
                    if sub.empty:
                        ot = None
                    else:
                        ot = sub.iloc[0].get("opinion_type", sub.iloc[0].get("opinion_type".lower(), None))
                    rows.append({"order_book_id": ob, "opinion_type": ot})
            except Exception:
                rows.append({"order_book_id": ob, "opinion_type": None})
    print(f"[timing] fetch_audit batch ({len(codes)} codes): {time.perf_counter() - t0:.2f}s")
    return pd.DataFrame(rows)


def update_rq_yearly(
    pre_trade_day: str,
    today: str,
    *,
    mongo_alias: str = "local",
    mongo_db: str = "basic_rq",
    base_coll: str = "rq_base_info",
    target_coll: str = "rq_yearly",
    chunk_size: int = 800,
) -> tuple[bool, int]:
    """返回 (是否成功, 实际写入行数)。"""
    pre_trade_day = _norm_day(pre_trade_day)
    today = _norm_day(today)
    _, _, _, latest_year, last_year = _season_params(today, pre_trade_day)
    q_y = _iso_to_quarter(latest_year)
    q_ly = _iso_to_quarter(last_year)

    t_conn = time.perf_counter()
    client = get_client(mongo_alias)
    print(f"[timing] connect_mongo: {time.perf_counter() - t_conn:.2f}s")

    base = client[mongo_db][base_coll]
    table = client[mongo_db][target_coll]

    t_query = time.perf_counter()
    codes = _load_codes_from_base(base, pre_trade_day)
    print(f"[timing] query_base_info: {time.perf_counter() - t_query:.2f}s")
    if not codes:
        print("❌ rq_base_info 无当日 code_rq")
        return False, 0

    def _one_year(chunk: list[str], yq: str) -> pd.DataFrame:
        tax = _pit_yearly_tax(chunk, yq, pre_trade_day)
        if tax.empty:
            return tax
        aud = _fetch_audit(chunk, yq, pre_trade_day)
        if not aud.empty:
            tax = tax.merge(aud, on="order_book_id", how="left")
        else:
            tax["opinion_type"] = None
        tax["stmnote_audit_category"] = tax["opinion_type"].map(_audit_map)
        return tax.drop(columns=[c for c in ["opinion_type"] if c in tax.columns])

    parts: list[pd.DataFrame] = []
    t_pit = time.perf_counter()
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        parts.append(_one_year(chunk, q_y))
    print(f"[timing] pit_yearly_tax total: {time.perf_counter() - t_pit:.2f}s")

    nonempty = [p for p in parts if not p.empty]
    if not nonempty:
        print("❌ 年报数据为空")
        return False, 0
    df = pd.concat(nonempty, ignore_index=True)
    if df.empty:
        print("❌ 年报数据为空")
        return False, 0

    if "stm_issuingdate" in df.columns:
        ok = df["stm_issuingdate"].map(_disclosure_ok)
        df_good = df.loc[ok].copy()
        need = df.loc[~ok, "code_rq"].astype(str).tolist()
    else:
        df_good = df.copy()
        need = []

    if need:
        parts2: list[pd.DataFrame] = []
        for i in range(0, len(need), chunk_size):
            parts2.append(_one_year(need[i : i + chunk_size], q_ly))
        ne2 = [p for p in parts2 if not p.empty]
        df2 = pd.concat(ne2, ignore_index=True) if ne2 else pd.DataFrame()
        df = pd.concat([df_good, df2], ignore_index=True)
        df = df.drop_duplicates(subset=["code_rq"], keep="last")
    else:
        df = df_good

    if df.empty:
        print("❌ 年报数据为空")
        return False

    df["date"] = pre_trade_day
    keep = [
        c
        for c in [
            "date",
            "code",
            "code_w",
            "code_rq",
            "rptdate",
            "stm_issuingdate",
            "stmnote_tax",
            "stmnote_audit_category",
        ]
        if c in df.columns
    ]
    df = df[keep]
    df = _df_nan_to_none(df)

    for dv in _day_variants(pre_trade_day):
        table.delete_many({"date": dv})
    table.insert_many(df.to_dict("records"), ordered=False)
    print(f"✅ {target_coll} 写入 {len(df)} 条 (date={pre_trade_day})")
    return True, len(df)


def create_indexes_rq_financial_extensions(
    mongo_alias: str = "local",
    mongo_db: str = "basic_rq",
) -> None:
    """为 rq_quarterly / rq_yearly / rq_bench 建索引（与 WindData2DB 思路一致）。"""
    create_indexes_rq_bench(mongo_alias=mongo_alias, mongo_db=mongo_db)
    c = get_client(mongo_alias)
    db = c[mongo_db]
    for name in ("rq_quarterly", "rq_yearly"):
        t = db[name]
        t.create_index([("date", pymongo.ASCENDING), ("code", pymongo.ASCENDING)], background=True, unique=True)
        t.create_index([("rptdate", pymongo.ASCENDING), ("code_w", pymongo.ASCENDING)], background=True)
        t.create_index([("stm_issuingdate", pymongo.ASCENDING)], background=True)


def update_rq_quarterly_yearly_bench(
    pre_trade_day: str,
    today: str,
    *,
    mongo_alias: str = "local",
    mongo_db: str = "basic_rq",
    do_quarterly: bool = True,
    do_yearly: bool = True,
    do_bench: bool = True,
) -> None:
    """一次跑完三个表（今日=当前交易日，pre_trade_day=数据日）。"""
    if do_quarterly:
        update_rq_quarterly(pre_trade_day, today, mongo_alias=mongo_alias, mongo_db=mongo_db)
    if do_yearly:
        update_rq_yearly(pre_trade_day, today, mongo_alias=mongo_alias, mongo_db=mongo_db)
    if do_bench:
        update_rq_bench(pre_trade_day, mongo_alias=mongo_alias, mongo_db=mongo_db)


if __name__ == "__main__":
    # 示例：与 Wind 更新语义一致 —— today 为「当前交易日」，pre_trade_day 为「前一交易日」
    PRE = "2025-07-07"
    TD = "2025-07-08"
    create_indexes_rq_financial_extensions()
    update_rq_quarterly_yearly_bench(PRE, TD)
