import rqdatac as rq
import pandas as pd
import pymongo
from typing import Any
import psutil
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import logging
import time

logger = logging.getLogger(__name__)

# 初始化RQData连接
try:
    rq.init('18616633529', 'wuzhi2020')
    print("✅ RQData连接成功")
except Exception as e:
    print(f"❌ RQData连接失败: {e}")
    raise


def get_client(c_from='89mango'):
    # 统一配置为字典格式，明确各字段
    client_dict = {
        'local': {'host': '127.0.0.1', 'port': 27017, 'user': None, 'pwd': None},  # 无认证
        'neo': {'host': '192.168.1.77', 'port': 27017, 'user': None, 'pwd': None},   # 无认证
        'bob': {'host': '192.168.1.87', 'port': 27017, 'user': None, 'pwd': None},   # 无认证
        'db_u': {'user': 'Tom', 'pwd': 'tom', 'host': '192.168.1.99', 'port': 29900},  # 带认证
        'db_w': {'user': 'Amy', 'pwd': 'amy', 'host': '192.168.1.99', 'port': 29900},  # 带认证
        'admin': {'host': '192.168.1.58', 'port': 27017, 'user': None, 'pwd': None},    # 无认证
        'readonly': {'host': '192.168.1.58', 'port': 27017, 'user': None, 'pwd': None}, # 无认证
        '89mango': {'host': '192.168.1.226', 'port': 27017, 'user': None, 'pwd': None},   # 无认证
        
        # 新服务器 192.168.110.199 的三个账号
        'wonderwz_admin': {'user': 'admin', 'pwd': 'admin_wonderwz', 'host': '192.168.110.199', 'port': 27017},
        'wonderwz_rw': {'user': 'readwriter', 'pwd': 'readwrite_wonderwz', 'host': '192.168.110.199', 'port': 27017},
        'wonderwz_ro': {'user': 'reader', 'pwd': 'readonly_wonderwz', 'host': '192.168.110.199', 'port': 27017},
    }
    
    config = client_dict.get(c_from)
    if not config:
        raise ValueError(f'传入的数据库目标服务器有误 {c_from}，请检查 {list(client_dict.keys())}')
    
    # 动态构造 URI（自动处理认证）
    if config.get('user') and config.get('pwd'):
        client_uri = f"mongodb://{config['user']}:{config['pwd']}@{config['host']}:{config['port']}"
    else:
        client_uri = f"mongodb://{config['host']}:{config['port']}"
    
    try:
        print(f"正在连接到 {c_from} 数据库：{config['host']}:{config['port']}")
        return pymongo.MongoClient(client_uri)
    except pymongo.errors.PyMongoError as e:
        print(f"无法连接到 MongoDB 服务器：{e}")
        raise



# 数据库插入相关函数
def _thread_insert2db(table, df):
    """线程插入数据到数据库"""
    try:
        # 使用insert_many插入数据
        data_list = df.to_dict('records')
        if data_list:
            table.insert_many(data_list, ordered=False)
            print(f"线程插入成功，记录数: {len(data_list)}")
    except Exception as e:
        print(f"线程插入失败: {e}")
        raise

def insert_db_from_df(table: Any, df: pd.DataFrame) -> None:
    """
    将DataFrame数据插入到数据库表中
    :param table: 数据库表对象
    :param df: 要插入的数据
    """
    if table is None or df is None:
        raise Exception("必须传入数据表，数据(df格式)")
    if df.empty:
        raise Exception("数据 df 为空，请检查！目标table：{}".format(table))
    df_len = df.shape[0]
    if df_len > 1500000:
        cpus = int(psutil.cpu_count(logical=False) * 0.7)
        print(f'数据量为：{df_len}，将分拆成 {cpus} 个线程 分布入库')
        df_list = np.array_split(df, cpus)
        arg_list = [(table, df_) for df_ in df_list]
        with ThreadPoolExecutor(max_workers=cpus) as pool:
            pool.map(lambda arg: _thread_insert2db(*arg), arg_list)
    else:
        _thread_insert2db(table=table, df=df)

def get_ra_base_info(input_date: str) -> pd.DataFrame:
    """
    获取指定日期的RQData基础信息并入库
    
    :param input_date: 交易日期，格式如 "2026/02/10"
    :return: 处理后的DataFrame
    """
    print(f"\n=== 开始获取 {input_date} 的RQData基础信息 ===")
    
    # 获取所有股票基本信息
    df_allinstrument = rq.all_instruments(type='CS', date=input_date, market='cn')
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
            dfsus = rq.is_suspended(batch_codes, start_date=input_date, end_date=input_date, market="cn")
            
            # 批量查询ST状态
            st_status = rq.is_st_stock(batch_codes, start_date=input_date, end_date=input_date, market="cn")
            
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
                    
                    # 添加到结果
                    batch_results.append({
                        'date': str(input_date),  # 交易日期，字符串格式
                        'code': code,           # 新的代码格式：SZ000001/SH600000
                        'code_rq': stock_code,  # 原始RQ格式：000001.XSHE/600000.XSHG
                        'trade_status': trade_status,
                        'riskwarning': riskwarning  # 是否为ST股票：1=是，0=否
                    })
                    
                except Exception as e:
                    print(f"处理股票 {stock_code} 时出错: {e}")
                    # 添加错误信息到结果
                    batch_results.append({
                        'date': str(input_date),
                        'code': stock_code,
                        'code_rq': stock_code,
                        'trade_status': None,
                        'riskwarning': None
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
            # 出错时降级为单只处理
            for stock_code in batch_codes:
                try:
                    # 获取停牌状态
                    dfsus = rq.is_suspended(stock_code, start_date=input_date, end_date=input_date, market="cn")
                    
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
                        is_st = rq.is_st_stock(stock_code, start_date=input_date, end_date=input_date, market="cn")
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
                    
                    # 添加到结果
                    results.append({
                        'date': str(input_date),
                        'code': code,
                        'code_rq': stock_code,
                        'trade_status': trade_status,
                        'riskwarning': riskwarning
                    })
                    
                except Exception as stock_err:
                    print(f"处理股票 {stock_code} 时出错: {stock_err}")
                    results.append({
                        'date': str(input_date),
                        'code': stock_code,
                        'code_rq': stock_code,
                        'trade_status': None,
                        'riskwarning': None
                    })
            
            total_processed += len(batch_codes)
            print(f"已处理 {total_processed}/{len(stock_codes)} 只股票")
            time.sleep(0.2)  # 出错时增加延迟
    
    # 将结果转换为DataFrame
    df_results = pd.DataFrame(results)
    
    # 只保留需要的列，确保顺序正确
    df_results = df_results[['date', 'code', 'code_rq', 'trade_status', 'riskwarning']]
    
    # 统计交易状态
    if 'trade_status' in df_results.columns:
        trade_count = df_results['trade_status'].sum() if df_results['trade_status'].notna().any() else 0
        suspended_count = len(df_results) - trade_count
        print(f"\n✅ 处理完成")
        print(f"正常交易股票数量(trade_status=1): {trade_count}")
        print(f"停牌股票数量(trade_status=0): {suspended_count}")
    

    
    # 连接数据库并插入数据
    print("\n正在连接数据库...")
    try:
        # 获取本地数据库连接
        client = get_client('local')
        table = client['basic_rq']['rq_base_info']
        print(f"✅ 数据库连接成功，表：{table}")
        
        # 插入数据
        print("\n正在插入数据到数据库...")
        insert_db_from_df(table=table, df=df_results)
        print(f"✅ 数据插入完成，共插入 {len(df_results)} 条记录")
        
    except Exception as e:
        print(f"❌ 数据库操作失败: {e}")
        import traceback
        traceback.print_exc()
    

    
    return df_results

if __name__ == '__main__':
    # 示例用法
    # input_date = "2026/02/11"
    Client = get_client('local')
    df_dates2 = pd.DataFrame(Client.economic.trade_dates.find(
        {'trade_date': {'$gte': "2026-03-17", '$lte': "2026-03-17"}},
        {'_id': 0})).sort_values('trade_date').trade_date.to_list()
    logger.warning(f'数据下载区间: {df_dates2[0]} ~ {df_dates2[-1]}')

    for input_date in df_dates2:
        df = get_ra_base_info(input_date)
    print(f"\n=== {input_date} 的RQData基础信息获取完成 ===")