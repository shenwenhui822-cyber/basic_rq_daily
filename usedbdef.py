import pandas as pd
import numpy as np
from typing import Any
import psutil
from concurrent.futures import ThreadPoolExecutor

from mongo_connect import get_client

DEFAULT_MONGO_ALIAS = "wonderwz27018_rw"

__all__ = ["get_client", "insert_db_from_df", "DEFAULT_MONGO_ALIAS"]


# ==================== 数据库插入相关函数 ====================
def _thread_insert2db(table, df):
    """线程插入数据到数据库"""
    try:
        data_list = df.to_dict("records")
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
        print(f"数据量为：{df_len}，将分拆成 {cpus} 个线程 分布入库")
        df_list = np.array_split(df, cpus)
        arg_list = [(table, df_) for df_ in df_list]
        with ThreadPoolExecutor(max_workers=cpus) as pool:
            pool.map(lambda arg: _thread_insert2db(*arg), arg_list)
    else:
        _thread_insert2db(table=table, df=df)
