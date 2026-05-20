import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pymongo
import os
import sys
from loguru import logger
from typing import Any
import psutil
from concurrent.futures import ThreadPoolExecutor

# ==================== 数据库连接 ====================
def get_client(c_from='local'):
    # 统一配置为字典格式，明确各字段
    client_dict = {
        'local': {'host': '127.0.0.1', 'port': 27017, 'user': None, 'pwd': None},  # 无认证
        'neo': {'host': '192.168.1.77', 'port': 27017, 'user': None, 'pwd': None},   # 无认证
        'bob': {'host': '192.168.1.87', 'port': 27017, 'user': None, 'pwd': None},   # 无认证
        'db_u': {'user': 'Tom', 'pwd': 'tom', 'host': '192.168.1.99', 'port': 29900},  # 带认证
        'db_w': {'user': 'Amy', 'pwd': 'amy', 'host': '192.168.1.99', 'port': 29900},  # 带认证
        'admin': {'host': '192.168.1.58', 'port': 27017, 'user': None, 'pwd': None},    # 无认证
        'readonly': {'host': '192.168.1.58', 'port': 27017, 'user': None, 'pwd': None}, # 无认证
        '89mango': {'host': '192.168.1.226', 'port': 27017, 'user': None, 'pwd': None},   # 无认证（若需要认证需补充 user/pwd）
        # 新服务器 192.168.110.199
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


# ==================== 数据库插入相关函数 ====================
def _thread_insert2db(table, df):
    """线程插入数据到数据库"""
    try:
        data_list = df.to_dict('records')
        if data_list:
            table.insert_many(data_list, ordered=False)
            print(f"线程插入成功，记录数：{len(data_list)}")
    except Exception as e:
        print(f"线程插入失败：{e}")
        raise


def insert_db_from_df(table: Any, df: pd.DataFrame) -> None:
    """将 DataFrame 数据插入到数据库表中"""
    if table is None or df is None:
        raise Exception("必须传入数据表，数据 (df 格式)")
    if df.empty:
        raise Exception("数据 df 为空，请检查！目标 table：{}".format(table))
    df_len = df.shape[0]
    if df_len > 1500000:
        phy_cpu = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 2
        cpus = max(1, int(phy_cpu * 0.7))
        print(f'数据量为：{df_len}，将分拆成 {cpus} 个线程 分布入库')
        df_list = np.array_split(df, cpus)
        arg_list = [(table, df_) for df_ in df_list]
        with ThreadPoolExecutor(max_workers=cpus) as pool:
            pool.map(lambda arg: _thread_insert2db(*arg), arg_list)
    else:
        _thread_insert2db(table=table, df=df)


