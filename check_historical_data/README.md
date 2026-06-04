# check_historical_data — 历史数据质量检查（只读）

对 MongoDB `basic_rq`（9 表）与 `rq_minute`（按年分表）做**只读**检查：不写库、不调用米筐 API。结果输出为 `reports/*.txt`。

脚本：**`check_historical_data.py`**（唯一性 + 字段完整 + 日期覆盖）

---

## 依赖

```bash
pip install pymongo
```

---

## 用法

```bash
# 检查 basic_rq 9 表 + rq_minute（按区间涉及年份自动检查 rq_minute_none_YYYY）
python check_historical_data/check_historical_data.py

python check_historical_data/check_historical_data.py --end 2026-05-30
python check_historical_data/check_historical_data.py --start 2026-05-01 --end 2026-05-30

# 只查分钟线 2026 年分表
python check_historical_data/check_historical_data.py --collection rq_minute_none_2026 --start 2026-05-01 --end 2026-05-30

# 跳过分钟线（数据量大）
python check_historical_data/check_historical_data.py --skip-minute

python check_historical_data/check_historical_data.py --collection rq_base_info
python check_historical_data/check_historical_data.py --output check_historical_data/reports/my_report.txt
```

---

## 各表默认起始日（说明文档.md）

| 集合 | 默认起始日 |
|------|------------|
| `rq_base_info`, `rq_basic_financial`, `rq_base_index` | 2015-01-05 |
| `rq_quarterly`, `rq_yearly` | 2015-01-15 |
| `rq_daily_price_none`, `rq_bench`, `rq_daily_indusSWL2`, `rq_daily_indusSWL2_price` | 2020-01-02 |
| `rq_minute.rq_minute_none_YYYY` | 2026-01-05（按检查区间年份展开，如 `rq_minute_none_2026`） |

截止日未传 `--end` 时，取 `economic.trade_dates` 最新 `trade_date`。

运行时会**边运行边写入** `reports/data_quality_*.txt`；全部问题 `_id` 写入同目录 `data_quality_*_issue_ids.txt`（按 `库.集合` 分节）。加 `--quiet` 可关闭控制台进度。

---

## 三类检查

1. **唯一性**：`norm_date` + 业务键不得重复（分钟线：`time + code_rq`）  
2. **字段完整**：按交易日逐日拉取，检查必填字段是否存在（值可为 null）  
3. **日期完整**：区间内每个交易日均有数据；`rq_bench` 额外提示每日是否少于 6 条  

修改默认起始日或字段：编辑 `check_historical_data.py` 内 `TABLE_SPECS`。
