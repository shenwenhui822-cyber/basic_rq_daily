import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from rq_paths import bootstrap

bootstrap(__file__)

import argparse
import logging
import time
import traceback
from typing import Any

import pandas as pd
import rqdatac as rq

from trade_date_utils import parse_explicit_date_arg, parse_start_end_range
from usedbdef import get_client, insert_db_from_df

logger = logging.getLogger(__name__)

DATE_FMT_DB = "%Y-%m-%d"

_RQ_INITIALIZED = False


def _init_rq() -> None:
    global _RQ_INITIALIZED
    if _RQ_INITIALIZED:
        return
    try:
        rq.init("18616633529", "wuzhi2020")
        print("RQData 连接成功")
        _RQ_INITIALIZED = True
    except Exception as e:
        print(f"RQData 连接失败: {e}")
        raise


def _mongo_date_variants(d: str) -> list[str]:
    """库内 date 可能为 YYYY/MM/DD 或 YYYY-MM-DD，删除时两种都匹配。"""
    s = str(d).strip()
    out = [s]
    if "/" in s:
        out.append(s.replace("/", "-"))
    elif "-" in s and s.count("-") >= 2:
        parts = s.split("-")
        if len(parts) == 3 and all(parts):
            y, m, n = parts[0], parts[1].zfill(2), parts[2].zfill(2)
            out.append(f"{y}/{m}/{n}")
    return list(dict.fromkeys(out))


def _input_date_to_yyyymmdd(input_date: str) -> str:
    return pd.Timestamp(str(input_date).replace("/", "-")).strftime("%Y%m%d")


def _instruments_map_for_codes(codes: list[str]) -> dict[str, Any]:
    if not codes:
        return {}
    try:
        insts = rq.instruments(codes)
        if insts is None:
            return {}
        if not isinstance(insts, list):
            insts = [insts]
        return {
            getattr(x, "order_book_id", None): x
            for x in insts
            if x is not None and getattr(x, "order_book_id", None) is not None
        }
    except Exception as e:
        logger.warning("rq.instruments 批量失败: %s", e)
        return {}


def _listing_days_for_code(inst, date_yyyymmdd: str) -> int | None:
    if inst is None:
        return None
    try:
        v = inst.days_from_listed(date_yyyymmdd)
        return int(v) if v is not None else None
    except Exception:
        return None


def get_ra_base_info(
    input_date: str,
    *,
    mongo_alias: str = "wonderwz27018_rw",
    delete_before_insert: bool = True,
) -> pd.DataFrame:
    """
    获取指定日期的RQData基础信息并入库
    
    :param input_date: 交易日期，格式如 "2015-09-30" / "2015/09/30"
    :param delete_before_insert: True 时先删当日旧记录再插入（重跑历史可补 list_days）
    :return: 处理后的DataFrame
    """
    date_str = parse_explicit_date_arg(str(input_date), fmt=DATE_FMT_DB)
    rq_date = date_str.replace("-", "/")
    date_yyyymmdd = _input_date_to_yyyymmdd(date_str)

    print(f"\n=== 开始获取 {date_str} 的 RQData 基础信息 ===")

    df_allinstrument = rq.all_instruments(type="CS", date=rq_date, market="cn")
    print(f"共获取到 {len(df_allinstrument)} 只股票")
    print(f"数据结构: {df_allinstrument.columns.tolist()}")
    
    # 检查股票代码列名
    if 'order_book_id' in df_allinstrument.columns:
        stock_col = 'order_book_id'
    elif 'symbol' in df_allinstrument.columns:
        stock_col = 'symbol'
    elif 'code' in df_allinstrument.columns:
        stock_col = 'code'
    else:
        # 默认使用第一列
        stock_col = df_allinstrument.columns[0]
        print(f"未找到标准股票代码列，使用第一列: {stock_col}")
    
    print(f"使用的股票代码列: {stock_col}")
    
    # 创建结果列表
    results = []
    
    # 获取所有股票代码
    stock_codes = df_allinstrument[stock_col].tolist()
    
    print(f"\n开始获取 {len(stock_codes)} 只股票的交易状态...")
    
    # 批量处理股票，减少连接数
    batch_size = 2000  # 批量大小，可根据实际情况调整
    total_processed = 0
    
    for i in range(0, len(stock_codes), batch_size):
        batch_codes = stock_codes[i:i+batch_size]
        batch_results = []
        
        try:
            # 批量查询停牌状态
            dfsus = rq.is_suspended(batch_codes, start_date=rq_date, end_date=rq_date, market="cn")
            st_status = rq.is_st_stock(batch_codes, start_date=rq_date, end_date=rq_date, market="cn")
            inst_map = _instruments_map_for_codes(batch_codes)

            # 处理每只股票的结果
            for j, stock_code in enumerate(batch_codes):
                try:
                    # 提取停牌状态
                    is_suspended = False
                    if stock_code in dfsus.columns:
                        stock_data = dfsus[stock_code]
                        if not stock_data.empty:
                            is_suspended = stock_data.iloc[0]
                    
                    # 转换为交易状态：未停牌(false)→1，停牌(true)→0
                    trade_status = 1 if not is_suspended else 0
                    
                    # 转换股票代码格式：000001.XSHE → SZ000001，600000.XSHG → SH600000
                    if '.XSHE' in stock_code:
                        # 深圳市场
                        code = 'SZ' + stock_code.split('.')[0]
                    elif '.XSHG' in stock_code:
                        # 沪市
                        code = 'SH' + stock_code.split('.')[0]
                    else:
                        # 其他市场或格式，保持原样
                        code = stock_code
                    
                    # 提取ST状态
                    st_flag = False
                    if stock_code in st_status.columns:
                        st_data = st_status[stock_code]
                        if not st_data.empty:
                            st_flag = st_data.iloc[0]
                    
                    # 转换ST状态为1/0：True→1，False→0
                    riskwarning = 1 if st_flag else 0
                    list_days = _listing_days_for_code(inst_map.get(stock_code), date_yyyymmdd)

                    batch_results.append({
                        "date": date_str,
                        "code": code,
                        "code_rq": stock_code,
                        "trade_status": trade_status,
                        "riskwarning": riskwarning,
                        "list_days": list_days,
                    })

                except Exception as e:
                    print(f"处理股票 {stock_code} 时出错: {e}")
                    batch_results.append({
                        "date": date_str,
                        "code": stock_code,
                        "code_rq": stock_code,
                        "trade_status": None,
                        "riskwarning": None,
                        "list_days": None,
                    })
            
            # 将批次结果添加到总结果
            results.extend(batch_results)
            total_processed += len(batch_codes)
            
            # 显示进度
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            
            # 添加短暂延迟，避免请求过于密集
            time.sleep(0.1)
            
        except Exception as e:
            print(f"处理批次时出错: {e}")
            traceback.print_exc()
            for stock_code in batch_codes:
                try:
                    dfsus = rq.is_suspended(stock_code, start_date=rq_date, end_date=rq_date, market="cn")
                    
                    # 提取布尔值结果
                    is_suspended = False
                    if hasattr(dfsus, 'values') and dfsus.values.size > 0:
                        is_suspended = dfsus.values[0]
                    elif isinstance(dfsus, bool):
                        is_suspended = dfsus
                    elif hasattr(dfsus, '__bool__'):
                        is_suspended = bool(dfsus)
                    
                    # 转换为交易状态：未停牌(false)→1，停牌(true)→0
                    trade_status = 1 if not is_suspended else 0
                    
                    # 转换股票代码格式
                    if '.XSHE' in stock_code:
                        code = 'SZ' + stock_code.split('.')[0]
                    elif '.XSHG' in stock_code:
                        code = 'SH' + stock_code.split('.')[0]
                    else:
                        code = stock_code
                    
                    # 获取股票ST状态
                    try:
                        is_st = rq.is_st_stock(stock_code, start_date=rq_date, end_date=rq_date, market="cn")
                        st_flag = False
                        if hasattr(is_st, 'values') and is_st.values.size > 0:
                            st_flag = is_st.values[0]
                        elif isinstance(is_st, bool):
                            st_flag = is_st
                        elif hasattr(is_st, '__bool__'):
                            st_flag = bool(is_st)
                    except Exception as st_err:
                        print(f"获取 {stock_code} ST状态时出错: {st_err}")
                        st_flag = None
                    
                    # 转换ST状态
                    riskwarning = 1 if st_flag else 0 if st_flag is not None else None

                    try:
                        inst_one = rq.instruments(stock_code)
                        if isinstance(inst_one, list):
                            inst_one = inst_one[0] if inst_one else None
                        list_days = _listing_days_for_code(inst_one, date_yyyymmdd)
                    except Exception:
                        list_days = None

                    results.append({
                        "date": date_str,
                        "code": code,
                        "code_rq": stock_code,
                        "trade_status": trade_status,
                        "riskwarning": riskwarning,
                        "list_days": list_days,
                    })

                except Exception as stock_err:
                    print(f"处理股票 {stock_code} 时出错: {stock_err}")
                    results.append({
                        "date": date_str,
                        "code": stock_code,
                        "code_rq": stock_code,
                        "trade_status": None,
                        "riskwarning": None,
                        "list_days": None,
                    })
            
            total_processed += len(batch_codes)
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            time.sleep(0.2)  # 出错时增加延迟
    
    # 将结果转换为DataFrame
    df_results = pd.DataFrame(results)
    
    # 只保留需要的列，确保顺序正确
    df_results = df_results[
        ["date", "code", "code_rq", "trade_status", "riskwarning", "list_days"]
    ]

    if "trade_status" in df_results.columns:
        trade_count = df_results["trade_status"].sum() if df_results["trade_status"].notna().any() else 0
        suspended_count = len(df_results) - trade_count
        list_days_ok = df_results["list_days"].notna().sum()
        print(f"\n✅ 处理完成")
        print(f"正常交易股票数量(trade_status=1): {trade_count}")
        print(f"停牌股票数量(trade_status=0): {suspended_count}")
        print(f"list_days 非空: {list_days_ok}/{len(df_results)}")

    print("\n正在连接数据库...")
    try:
        client = get_client(mongo_alias)
        table = client["basic_rq"]["rq_base_info"]
        print(f"✅ 数据库连接成功，表：{table}")

        if delete_before_insert:
            variants = _mongo_date_variants(date_str)
            dr = table.delete_many({"date": {"$in": variants}})
            print(f"已删除当日旧记录: {dr.deleted_count} 条（date in {variants}）")

        print("\n正在插入数据到数据库...")
        insert_db_from_df(table=table, df=df_results)
        print(f"✅ 数据插入完成，共插入 {len(df_results)} 条记录")
        
    except Exception as e:
        print(f"❌ 数据库操作失败: {e}")
        import traceback
        traceback.print_exc()
    

    
    return df_results


def fetch_trade_dates_in_range(
    client,
    start: str,
    end: str,
) -> list[str]:
    """从 economic.trade_dates 取 [start, end] 内全部交易日（含端点）。"""
    start_s, end_s = parse_start_end_range(start, end)
    rows = list(
        client.economic.trade_dates.find(
            {"trade_date": {"$gte": start_s, "$lte": end_s}},
            {"_id": 0},
        )
    )
    if not rows:
        raise ValueError(f"economic.trade_dates 在 {start_s} ~ {end_s} 无记录")
    df_dates = (
        pd.DataFrame(rows)
        .sort_values("trade_date")["trade_date"]
        .astype(str)
        .tolist()
    )
    return df_dates


def main(
    *,
    start: str,
    end: str,
    mongo_alias: str = "wonderwz27018_rw",
) -> None:
    _init_rq()
    client = get_client(mongo_alias)
    trade_dates = fetch_trade_dates_in_range(client, start, end)
    logger.warning("数据下载区间: %s ~ %s，共 %d 个交易日", trade_dates[0], trade_dates[-1], len(trade_dates))

    for input_date in trade_dates:
        get_ra_base_info(input_date, mongo_alias=mongo_alias)

    print(f"\n=== 区间 {trade_dates[0]} ~ {trade_dates[-1]} 的 RQData 基础信息获取完成 ===")


def _cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="历史补齐 rq_base_info：按 economic.trade_dates 区间逐日拉取并入库",
    )
    p.add_argument(
        "--start",
        default="2026-03-16",
        help="区间起（含）：YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD，默认 2026-03-16",
    )
    p.add_argument(
        "--end",
        default="2026-03-18",
        help="区间止（含）：格式同 --start，默认 2026-03-18",
    )
    p.add_argument(
        "--mongo-alias",
        default="wonderwz27018_rw",
        help="Mongo 连接别名，默认 wonderwz27018_rw",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _cli_args()
    main(start=args.start, end=args.end, mongo_alias=args.mongo_alias)