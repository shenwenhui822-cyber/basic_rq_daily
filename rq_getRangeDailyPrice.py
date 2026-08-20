"""
区间日线/分钟共用：交易日基础表（RQ 全市）→ ``date`` + ``code_rq`` lookup。

被 ``rq_getRangeMinPrice.py`` 引用；逻辑与 ``rq_history_backfill/rq_getRangeDailyPriceLongrun.py`` 前半段对齐。
"""

from __future__ import annotations

import sys
import time
from datetime import date

import numpy as np
import pandas as pd
import rqdatac as rq

_BASE_COLUMNS = ["date", "code", "code_rq", "trade_status", "riskwarning"]

# 当日流量占用达到 bytes_limit 的该比例即退出（与其它脚本一致）
QUOTA_STOP_FRACTION = 0.6

try:
    rq.init("15317321758", "WuZhi@2026")
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
    raise


def _parse_input_date(s: str) -> date:
    return pd.Timestamp(s).date()


def check_rq_quota_or_exit(
    *,
    fraction: float = QUOTA_STOP_FRACTION,
    label: str = "",
) -> None:
    q = rq.user.get_quota()
    used = int(q.get("bytes_used", 0) or 0)
    limit = int(q.get("bytes_limit", 0) or 0)
    remaining = q.get("remaining_days")
    lic = q.get("license_type")

    prefix = f"[{label}] " if label else ""
    print(
        f"{prefix}RQData 配额: bytes_used={used}, bytes_limit={limit}, "
        f"remaining_days={remaining}, license_type={lic}"
    )

    if limit <= 0:
        print(f"{prefix}bytes_limit=0（不限额），不按比例中止。")
        return

    ratio = used / limit
    pct = ratio * 100
    print(f"{prefix}当日流量占用: {pct:.2f}% （阈值 {fraction * 100:.0f}%）")

    if used >= fraction * limit:
        print(
            f"{prefix}❌ 已用流量已达上限的 {fraction * 100:.0f}% 及以上，停止执行。",
            file=sys.stderr,
        )
        sys.exit(1)


def _rq_code_to_display(code_rq: str) -> str:
    if ".XSHE" in code_rq:
        return "SZ" + code_rq.split(".")[0]
    if ".XSHG" in code_rq:
        return "SH" + code_rq.split(".")[0]
    return code_rq


def get_ra_base_info_one_day(input_date: str) -> pd.DataFrame:
    """
    单日：全市场 CS + 停牌/ST 状态。
    ``input_date`` 建议 ``YYYY/MM/DD``，与 ``rq.all_instruments`` 的 date 兼容。
    """
    print(f"\n=== 开始获取 {input_date} 的 RQData 基础信息 ===")

    df_all = rq.all_instruments(type="CS", date=input_date, market="cn")
    print(f"共获取到 {len(df_all)} 只股票")

    if "order_book_id" in df_all.columns:
        stock_col = "order_book_id"
    elif "symbol" in df_all.columns:
        stock_col = "symbol"
    elif "code" in df_all.columns:
        stock_col = "code"
    else:
        stock_col = df_all.columns[0]
        print(f"未找到标准股票代码列，使用第一列: {stock_col}")

    stock_codes = df_all[stock_col].tolist()
    results: list[dict] = []
    batch_size = 2000
    total_processed = 0

    for i in range(0, len(stock_codes), batch_size):
        batch_codes = stock_codes[i : i + batch_size]
        batch_results: list[dict] = []

        try:
            dfsus = rq.is_suspended(
                batch_codes, start_date=input_date, end_date=input_date, market="cn"
            )
            st_status = rq.is_st_stock(
                batch_codes, start_date=input_date, end_date=input_date, market="cn"
            )

            for stock_code in batch_codes:
                try:
                    is_suspended = False
                    if stock_code in dfsus.columns:
                        s = dfsus[stock_code]
                        if not s.empty:
                            is_suspended = bool(s.iloc[0])
                    trade_status = 1 if not is_suspended else 0

                    st_flag = False
                    if stock_code in st_status.columns:
                        t = st_status[stock_code]
                        if not t.empty:
                            st_flag = bool(t.iloc[0])
                    riskwarning = 1 if st_flag else 0

                    batch_results.append(
                        {
                            "date": str(input_date),
                            "code": _rq_code_to_display(stock_code),
                            "code_rq": stock_code,
                            "trade_status": trade_status,
                            "riskwarning": riskwarning,
                        }
                    )
                except Exception as e:
                    print(f"处理股票 {stock_code} 时出错: {e}")
                    batch_results.append(
                        {
                            "date": str(input_date),
                            "code": stock_code,
                            "code_rq": stock_code,
                            "trade_status": None,
                            "riskwarning": None,
                        }
                    )

            results.extend(batch_results)
            total_processed += len(batch_codes)
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            time.sleep(0.1)

        except Exception as e:
            print(f"处理批次时出错: {e}")
            for stock_code in batch_codes:
                try:
                    dfsus = rq.is_suspended(
                        stock_code, start_date=input_date, end_date=input_date, market="cn"
                    )
                    is_suspended = False
                    if hasattr(dfsus, "values") and dfsus.values.size > 0:
                        is_suspended = bool(dfsus.values[0])
                    elif isinstance(dfsus, bool):
                        is_suspended = dfsus
                    trade_status = 1 if not is_suspended else 0

                    try:
                        is_st = rq.is_st_stock(
                            stock_code,
                            start_date=input_date,
                            end_date=input_date,
                            market="cn",
                        )
                        st_flag = False
                        if hasattr(is_st, "values") and is_st.values.size > 0:
                            st_flag = bool(is_st.values[0])
                        elif isinstance(is_st, bool):
                            st_flag = is_st
                    except Exception as st_err:
                        print(f"获取 {stock_code} ST状态时出错: {st_err}")
                        st_flag = None

                    if st_flag is None:
                        riskwarning = None
                    else:
                        riskwarning = 1 if st_flag else 0

                    results.append(
                        {
                            "date": str(input_date),
                            "code": _rq_code_to_display(stock_code),
                            "code_rq": stock_code,
                            "trade_status": trade_status,
                            "riskwarning": riskwarning,
                        }
                    )
                except Exception as stock_err:
                    print(f"处理股票 {stock_code} 时出错: {stock_err}")
                    results.append(
                        {
                            "date": str(input_date),
                            "code": stock_code,
                            "code_rq": stock_code,
                            "trade_status": None,
                            "riskwarning": None,
                        }
                    )

            total_processed += len(batch_codes)
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            time.sleep(0.2)

    df_results = pd.DataFrame(results)
    if df_results.empty:
        return pd.DataFrame(columns=_BASE_COLUMNS)
    df_results = df_results[_BASE_COLUMNS]

    if df_results["trade_status"].notna().any():
        trade_count = int((df_results["trade_status"] == 1).sum())
        suspended = int((df_results["trade_status"] == 0).sum())
        print(f"正常交易(trade_status=1): {trade_count}，停牌(0): {suspended}")

    return df_results


def fetch_range_base_info(start_date: str, end_date: str, market: str = "cn") -> pd.DataFrame:
    """``[start_date, end_date]`` 内每个交易日拉取基础信息并纵向合并。"""
    d0 = _parse_input_date(start_date)
    d1 = _parse_input_date(end_date)
    if d0 > d1:
        raise ValueError(f"startdate 不能晚于 enddate：{d0} > {d1}")

    trading_days = rq.get_trading_dates(start_date=d0, end_date=d1, market=market)
    if not trading_days:
        return pd.DataFrame(columns=_BASE_COLUMNS)

    frames: list[pd.DataFrame] = []
    for k, d in enumerate(trading_days):
        date_str = d.strftime("%Y/%m/%d")
        print(f"\n>>> 交易日 {date_str}（{k + 1}/{len(trading_days)}）")
        frames.append(get_ra_base_info_one_day(date_str))

    out = pd.concat(frames, ignore_index=True)
    print(f"\n✅ 区间内基础数据合并完成：{len(trading_days)} 个交易日，{len(out)} 行")
    return out


def to_price_lookup_df(df: pd.DataFrame) -> pd.DataFrame:
    """仅 ``date``、``code_rq`` 去重，供 ``get_price`` 按日批量。"""
    if df.empty:
        return pd.DataFrame(columns=["date", "code_rq"])
    return df[["date", "code_rq"]].drop_duplicates().reset_index(drop=True)


def _is_rq_column_multiindex_wide(df: pd.DataFrame) -> bool:
    """列二级索引 ``(order_book_id, 字段)``。"""
    return isinstance(df.columns, pd.MultiIndex) and df.columns.nlevels == 2


def _df_nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace({np.nan: None})
