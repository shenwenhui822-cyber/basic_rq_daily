# =============================================================================
# Author: Keven Wang
# Date: 2026-03-18
# Illustration:
# 1. 每日更新 rq_base_info 数据
# =============================================================================
import rqdatac as rq
import pandas as pd
from typing import Any
import logging
import time
import traceback
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

from usedbdef import DEFAULT_MONGO_ALIAS, get_client, insert_db_from_df

# 初始化 RQData 连接
try:
    rq.init('18616633529', 'wuzhi2020')
    print("✅ RQData 连接成功")
except Exception as e:
    print(f"❌ RQData 连接失败：{e}")
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
    """将 '2026/02/10' 等格式转为 days_from_listed 所需的 YYYYMMDD（与 load_rqbaseInfofastmain 一致）。"""
    return pd.Timestamp(str(input_date).replace("/", "-")).strftime("%Y%m%d")


def _instruments_map_for_codes(codes: list[str]) -> dict[str, Any]:
    """批量 rq.instruments，返回 order_book_id -> Instrument。"""
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
        logger.warning("rq.instruments 批量失败: {}", e)
        return {}


def _listing_days_for_code(inst, date_yyyymmdd: str) -> int | None:
    """instruments(...).days_from_listed(YYYYMMDD)，与 test_rq_get_list_days / fastmain 一致。"""
    if inst is None:
        return None
    try:
        v = inst.days_from_listed(date_yyyymmdd)
        return int(v) if v is not None else None
    except Exception:
        return None


def get_ra_base_info(input_date: str, *, delete_before_insert: bool = True) -> pd.DataFrame:
    """
    获取指定日期的 RQData 基础信息并入库

    :param input_date: 交易日期，格式如 "2026/02/10"
    :param delete_before_insert: True 时先按 date 删除库内当日旧记录再插入（与 load_rqbaseInfofastmain 一致）
    :return: 处理后的 DataFrame
    """
    print(f"\n=== 开始获取 {input_date} 的 RQData 基础信息 ===")
    # 落库统一用横杠，与历史 update 脚本一致；删除时用 variants 覆盖斜杠/横杠两种存量
    date_str = str(input_date).replace("/", "-")
    date_yyyymmdd = _input_date_to_yyyymmdd(input_date)

    df_allinstrument = rq.all_instruments(type='CS', date=input_date, market='cn')
    print(f"共获取到 {len(df_allinstrument)} 只股票")
    print(f"数据结构：{df_allinstrument.columns.tolist()}")

    if 'order_book_id' in df_allinstrument.columns:
        stock_col = 'order_book_id'
    elif 'symbol' in df_allinstrument.columns:
        stock_col = 'symbol'
    elif 'code' in df_allinstrument.columns:
        stock_col = 'code'
    else:
        stock_col = df_allinstrument.columns[0]
        print(f"未找到标准股票代码列，使用第一列：{stock_col}")

    print(f"使用的股票代码列：{stock_col}")

    results = []
    stock_codes = df_allinstrument[stock_col].tolist()
    print(f"\n开始获取 {len(stock_codes)} 只股票的交易状态...")

    batch_size = 2000
    total_processed = 0

    for i in range(0, len(stock_codes), batch_size):
        batch_codes = stock_codes[i:i+batch_size]
        batch_results = []

        try:
            dfsus = rq.is_suspended(batch_codes, start_date=input_date, end_date=input_date, market="cn")
            st_status = rq.is_st_stock(batch_codes, start_date=input_date, end_date=input_date, market="cn")
            inst_map = _instruments_map_for_codes(batch_codes)

            for j, stock_code in enumerate(batch_codes):
                try:
                    is_suspended = False
                    if stock_code in dfsus.columns:
                        stock_data = dfsus[stock_code]
                        if not stock_data.empty:
                            is_suspended = stock_data.iloc[0]

                    trade_status = 1 if not is_suspended else 0

                    if '.XSHE' in stock_code:
                        code = 'SZ' + stock_code.split('.')[0]
                    elif '.XSHG' in stock_code:
                        code = 'SH' + stock_code.split('.')[0]
                    else:
                        code = stock_code

                    st_flag = False
                    if stock_code in st_status.columns:
                        st_data = st_status[stock_code]
                        if not st_data.empty:
                            st_flag = st_data.iloc[0]

                    riskwarning = 1 if st_flag else 0
                    list_days = _listing_days_for_code(inst_map.get(stock_code), date_yyyymmdd)

                    batch_results.append({
                        'date': date_str,
                        'code': code,
                        'code_rq': stock_code,
                        'trade_status': trade_status,
                        'riskwarning': riskwarning,
                        'list_days': list_days,
                    })

                except Exception as e:
                    print(f"处理股票 {stock_code} 时出错：{e}")
                    batch_results.append({
                        'date': date_str,
                        'code': stock_code,
                        'code_rq': stock_code,
                        'trade_status': None,
                        'riskwarning': None,
                        'list_days': None,
                    })

            results.extend(batch_results)
            total_processed += len(batch_codes)
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            time.sleep(0.1)

        except Exception as e:
            print(f"处理批次时出错：{repr(e)}（{type(e).__name__}）")
            traceback.print_exc()
            for stock_code in batch_codes:
                try:
                    dfsus = rq.is_suspended(stock_code, start_date=input_date, end_date=input_date, market="cn")
                    is_suspended = False
                    if hasattr(dfsus, 'values') and dfsus.values.size > 0:
                        is_suspended = dfsus.values[0]
                    elif isinstance(dfsus, bool):
                        is_suspended = dfsus
                    elif hasattr(dfsus, '__bool__'):
                        is_suspended = bool(dfsus)

                    trade_status = 1 if not is_suspended else 0

                    if '.XSHE' in stock_code:
                        code = 'SZ' + stock_code.split('.')[0]
                    elif '.XSHG' in stock_code:
                        code = 'SH' + stock_code.split('.')[0]
                    else:
                        code = stock_code

                    try:
                        is_st = rq.is_st_stock(stock_code, start_date=input_date, end_date=input_date, market="cn")
                        st_flag = False
                        if hasattr(is_st, 'values') and is_st.values.size > 0:
                            st_flag = is_st.values[0]
                        elif isinstance(is_st, bool):
                            st_flag = is_st
                        elif hasattr(is_st, '__bool__'):
                            st_flag = bool(is_st)
                    except Exception as st_err:
                        print(f"获取 {stock_code} ST 状态时出错：{repr(st_err)}（{type(st_err).__name__}）")
                        st_flag = None

                    riskwarning = 1 if st_flag else 0 if st_flag is not None else None

                    try:
                        inst_one = rq.instruments(stock_code)
                        if isinstance(inst_one, list):
                            inst_one = inst_one[0] if inst_one else None
                        list_days = _listing_days_for_code(inst_one, date_yyyymmdd)
                    except Exception:
                        list_days = None

                    results.append({
                        'date': date_str,
                        'code': code,
                        'code_rq': stock_code,
                        'trade_status': trade_status,
                        'riskwarning': riskwarning,
                        'list_days': list_days,
                    })

                except Exception as stock_err:
                    print(f"处理股票 {stock_code} 时出错：{repr(stock_err)}（{type(stock_err).__name__}）")
                    results.append({
                        'date': date_str,
                        'code': stock_code,
                        'code_rq': stock_code,
                        'trade_status': None,
                        'riskwarning': None,
                        'list_days': None,
                    })

            total_processed += len(batch_codes)
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            time.sleep(0.2)

    df_results = pd.DataFrame(results)
    df_results = df_results[
        ['date', 'code', 'code_rq', 'trade_status', 'riskwarning', 'list_days']
    ]

    if 'trade_status' in df_results.columns:
        trade_count = df_results['trade_status'].sum() if df_results['trade_status'].notna().any() else 0
        suspended_count = len(df_results) - trade_count
        print(f"\n✅ 处理完成")
        print(f"正常交易股票数量 (trade_status=1): {trade_count}")
        print(f"停牌股票数量 (trade_status=0): {suspended_count}")

    print("\n正在连接数据库...")
    try:
        client = get_client(DEFAULT_MONGO_ALIAS)
        table = client['basic_rq']['rq_base_info']
        print(f"✅ 数据库连接成功，表：{table}")

        if delete_before_insert:
            variants = _mongo_date_variants(input_date)
            dr = table.delete_many({"date": {"$in": variants}})
            print(f"已删除当日旧记录: {dr.deleted_count} 条（date in {variants}）")

        print("\n正在插入数据到数据库...")
        insert_db_from_df(table=table, df=df_results)
        print(f"✅ 数据插入完成，共插入 {len(df_results)} 条记录")

    except Exception as e:
        print(f"❌ 数据库操作失败：{e}")
        traceback.print_exc()

    return df_results


def update_rqbaseInfo(today_str: str, trade_dates_path: str) -> bool:
    """
    每日更新 rq_base_info 数据
    根据传入的 today_str 日期，判断是否是交易日，如果是则获取该日期的 RQData 基础信息并入库

    :param today_str: 日期字符串，格式如 "2026/02/10"
    :param trade_dates_path: trade_dates_all.csv 文件路径
    :return: 更新成功返回 True，失败返回 False
    """
    print(f"\n=== 开始更新 rq_base_info，日期：{today_str} ===")

    try:
        # 读取本地 trade_dates_all.csv 文件判断是否交易日
        df = pd.read_csv(trade_dates_path)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        
        # 将 today_str 转换为 date 对象进行判断（与 Updatedemo.py 保持一致）
        today_date = datetime.strptime(today_str.replace('/', '-'), '%Y-%m-%d').date()
        is_trade_day = today_date in df['trade_date'].dt.date.values

        if not is_trade_day:
            logger.info(f"{today_str} 不是交易日，跳过更新")
            print(f"❌ {today_str} 不是交易日，跳过更新")
            return False

        print(f"✅ {today_str} 是交易日，开始获取 RQData 基础信息...")
        df_result = get_ra_base_info(today_str)

        if df_result is not None and not df_result.empty:
            print(f"\n=== {today_str} 的 RQData 基础信息更新完成 ===")
            return True
        else:
            print(f"\n=== {today_str} 的 RQData 基础信息更新失败：数据为空 ===")
            return False

    except Exception as e:
        logger.error(f"更新 rq_base_info 失败：{e}")
        print(f"❌ 更新失败：{e}")
        traceback.print_exc()
        return False


def _cli_target_date_str() -> str:
    """命令行 --date：YYYYMMDD 或 YYYY/MM/DD、YYYY-MM-DD；省略则为今天。"""
    p = argparse.ArgumentParser(description="更新 rq_base_info")
    p.add_argument(
        "--date",
        "-d",
        default=None,
        help="目标日期，如 20260507、2026/05/07、2026-05-07；默认今天",
    )
    args = p.parse_args()
    if not args.date:
        return datetime.now().strftime("%Y/%m/%d")
    s = str(args.date).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}/{s[4:6]}/{s[6:8]}"
    return pd.Timestamp(s.replace("/", "-")).strftime("%Y/%m/%d")


if __name__ == '__main__':
    # 配置参数
    trade_dates_path = str(Path(__file__).resolve().parent / 'trade_dates_all.csv')

    today_str = _cli_target_date_str()
    result = update_rqbaseInfo(today_str, trade_dates_path)

    if result:
        print(f"\n✅ 数据更新成功")
    else:
        print(f"\n❌ 数据更新失败或不是交易日")
