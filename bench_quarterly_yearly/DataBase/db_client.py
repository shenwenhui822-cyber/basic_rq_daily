# -*- coding: utf-8 -*-
"""
@author: Neo
@software: PyCharm
@file: db_client.py
@time: 2023-09-10 13:35
说明: 对再用数据库服务器精选剥离，并对基础库服务器做权限控制
"""
import pymongo
from urllib.parse import quote_plus
from loguru import logger


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

def get_client_U(c_from='89mango'):
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
