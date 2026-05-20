import rqdatac as rq
import pandas as pd
import pymongo
from usedbdef import insert_db_from_df

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
        '89mango': {'host': '192.168.1.226', 'port': 27017, 'user': None, 'pwd': None}   # 无认证（若需要认证需补充user/pwd）
    }
    
    config = client_dict.get(c_from)
    if not config:
        raise ValueError(f'传入的数据库目标服务器有误 {c_from}，请检查 {list(client_dict.keys())}')
    
    # 动态构造URI（自动处理认证）
    if config['user'] and config['pwd']:
        client_uri = f"mongodb://{config['user']}:{config['pwd']}@{config['host']}:{config['port']}"
    else:
        client_uri = f"mongodb://{config['host']}:{config['port']}"
    
    try:
        print(f"正在连接到 {c_from} 数据库: {config['host']}:{config['port']}")
        return pymongo.MongoClient(client_uri)
    except pymongo.errors.PyMongoError as e:
        print(f"无法连接到MongoDB服务器: {e}")
        raise


def main(
    date_range,
    mongo_client_name='local',
    save_db_name='basic_rq',
    save_table_name='rq_daily_indusSWL2'
):
    """
    主函数：获取申万二级行业成分股并保存到数据库
    
    :param date_range: 日期范围字典，格式如 {'$gte': "2026-01-01", '$lte': "2026-02-13"}
    """
    # 初始化rqdatac连接
    try:
        rq.init('18616633529', 'wuzhi2020')
        print("✅ RQData连接成功")
    except Exception as e:
        print(f"❌ RQData连接失败: {e}")
        raise
    
    # 连接数据库（落库位置通过参数传入，便于在 __main__ 统一配置）
    Client = get_client(mongo_client_name)
    table = Client[save_db_name][save_table_name]
    
    # 获取日期范围内的交易日
    try:
        df_dates = pd.DataFrame(Client.economic.trade_dates.find(
            {'trade_date': date_range},
            {'_id': 0})).sort_values('trade_date')
        
        if not df_dates.empty:
            date_list = df_dates['trade_date'].to_list()
            print(f'数据下载区间: {date_list[0]} ~ {date_list[-1]}')
            print(f'总共有 {len(date_list)} 个交易日需要处理')
        else:
            print('未获取到日期数据')
            date_list = []
    except Exception as e:
        print(f"获取日期范围时出错: {e}")
        date_list = []
    
    # 顺序处理每个日期
    success_count = 0
    total_count = len(date_list)
    
    # 顺序处理每个日期
    for inputdate in date_list:
        print(f"\n=== 处理日期: {inputdate} ===")
        
        try:
            # 将日期格式转换为YYYY/MM/DD格式（RQData需要）
            start_date = inputdate.replace('-', '/')
            
            # 获取申万行业映射数据
            print("正在获取申万行业映射数据...")
            industry_mapping = rq.get_industry_mapping(source='sws', date=start_date, market='cn')



            # 处理数据格式
            try:
                if isinstance(industry_mapping, pd.DataFrame):
                    # 如果已经是DataFrame
                    df = industry_mapping.copy()
                    

                    
                    # 重命名列，确保使用标准名称
                    if 'index_code' in df.columns and 'index_name' in df.columns and 'second_index_code' in df.columns and 'second_index_name' in df.columns:
                        # 已经是完整的层级结构
                        pass
                    elif len(df.columns) >= 6:
                        # 按顺序重命名列
                        df.columns = ['index_code', 'index_name', 'second_index_code', 'second_index_name', 'third_index_code', 'third_index_name']
                    else:
                        raise ValueError("DataFrame列数不足，无法构建完整层级")
                        
                    print("数据格式识别成功 - 完整层级结构")
                    
                else:
                    raise ValueError(f"未知的数据类型，需要DataFrame格式: {type(industry_mapping)}")
                    

                
            except Exception as e:
                print(f"数据转换失败: {e}")
                print(f"原始数据详情: {industry_mapping}")
                continue

            # 提取二级行业数据（去重）
            df_level2 = df[['second_index_code', 'second_index_name']].drop_duplicates().reset_index(drop=True)

            # 重命名列为更清晰的名称
            df_level2 = df_level2.rename(columns={
                'second_index_code': 'level2_code',
                'second_index_name': 'level2_name'
            })

            # 输出二级行业列表
            print(f"共获取到 {len(df_level2)} 个二级行业")

            # 为每个二级行业获取成分股
            print("\n开始获取各二级行业成分股...")
            all_stocks = []
            total_industries = len(df_level2)
            
            # 批量处理行业，每批10个
            batch_size = 10
            
            for batch_start in range(0, total_industries, batch_size):
                batch_end = min(batch_start + batch_size, total_industries)
                batch_industries = df_level2.iloc[batch_start:batch_end]
                
                print(f"处理批次: {batch_start+1}-{batch_end}/{total_industries}")
                
                for _, row in batch_industries.iterrows():
                    industry_code = row['level2_code']
                    industry_name = row['level2_name']
                    
                    try:
                        # 获取行业成分股
                        stocks = rq.get_industry(industry_code, source='sws', date=start_date, market='cn')
                        
                        if stocks:
                            # 处理不同可能的数据格式
                            if isinstance(stocks, list):
                                # 如果是股票代码列表
                                for stock in stocks:
                                    all_stocks.append({
                                        'level2_code': industry_code,
                                        'level2_name': industry_name,
                                        'stock_code': stock
                                    })
                            elif isinstance(stocks, dict):
                                # 如果是字典格式
                                for stock_code, stock_info in stocks.items():
                                    all_stocks.append({
                                        'level2_code': industry_code,
                                        'level2_name': industry_name,
                                        'stock_code': stock_code
                                    })
                            elif hasattr(stocks, '__iter__') and not isinstance(stocks, (str, bytes)):
                                # 其他可迭代对象
                                for item in stocks:
                                    if isinstance(item, (list, tuple)) and len(item) > 0:
                                        all_stocks.append({
                                            'level2_code': industry_code,
                                            'level2_name': industry_name,
                                            'stock_code': item[0]
                                        })
                                    elif isinstance(item, str):
                                        all_stocks.append({
                                            'level2_code': industry_code,
                                            'level2_name': industry_name,
                                            'stock_code': item
                                        })
                        
                    except Exception as e:
                        # 只记录错误，不中断处理
                        continue

            # 将所有成分股转换为DataFrame
            if all_stocks:
                df_stocks = pd.DataFrame(all_stocks)
                
                # 转换为以行业为行的格式，每个行业包含股票代码列表并转换为字符串格式
                print("\n正在转换为行业分组格式...")
                
                # 按行业代码和行业名称分组，合并股票代码为列表并转换为字符串格式
                df_industry_stocks = df_stocks.groupby(['level2_code', 'level2_name'])['stock_code'].apply(lambda x: str(list(x))).reset_index()
                
                # 添加date列，使用原始日期格式
                df_industry_stocks['date'] = inputdate
                
                # 处理indus_code，去除.INDX后缀
                df_industry_stocks['indus_code'] = df_industry_stocks['level2_code'].str.replace('.INDX', '')
                
                # 重命名列并调整顺序
                df_industry_stocks = df_industry_stocks.rename(columns={
                    'level2_name': 'name',
                    'stock_code': 'stocks'
                })
                
                # 只保留需要的列，按指定顺序排列
                df_industry_stocks = df_industry_stocks[['date', 'indus_code', 'name', 'stocks']]
                
                # 插入到数据库
                print("\n正在将数据插入到数据库...")
                insert_db_from_df(table, df_industry_stocks)
                print(f"✅ 数据插入完成，共插入 {len(df_industry_stocks)} 条记录")
                

                
                success_count += 1
            else:
                print(f"\n❌ 未获取到任何成分股数据")
                
        except Exception as e:
            print(f"处理日期 {inputdate} 时出错: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # 输出处理结果
    print(f"\n=== 处理完成 ===")
    print(f"总共有 {total_count} 个交易日")
    print(f"成功处理 {success_count} 个交易日")
    print(f"失败 {total_count - success_count} 个交易日")


if __name__ == '__main__':
    # 定义日期范围
    date_range = {'$gte': "2025-12-01", '$lte': "2026-04-10"}
    # 定义落库位置
    mongo_client_name = 'local'
    save_db_name = 'basic_rq'
    save_table_name = 'rq_daily_indusSWL2'
    # 调用主函数
    main(
        date_range=date_range,
        mongo_client_name=mongo_client_name,
        save_db_name=save_db_name,
        save_table_name=save_table_name
    )