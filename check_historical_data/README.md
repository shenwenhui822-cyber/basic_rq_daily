# check_historical_data — 历史数据质量检查（只读）

对 MongoDB `basic_rq` 九张表做**只读**检查：不写库、不调用米筐 API。结果输出为 `reports/*.txt`。

脚本：**`check_historical_data.py`**（唯一性 + 字段完整 + 日期覆盖，配置内嵌于脚本顶部）

---

## 依赖

```bash
pip install pymongo
```

---

## 用法

```bash
# 检查全部 9 表（各表用说明文档默认起始日 ~ 交易日历最新日）
python check_historical_data/check_historical_data.py

python check_historical_data/check_historical_data.py --end 2026-05-30
python check_historical_data/check_historical_data.py --start 2026-05-01 --end 2026-05-30
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

截止日未传 `--end` 时，取 `economic.trade_dates` 最新 `trade_date`。

运行时会**边运行边写入** `reports/data_quality_*.txt`；全部问题 `_id` 写入同目录 `data_quality_*_issue_ids.txt`（按 `库.集合` 分节，标明表名）。加 `--quiet` 可关闭控制台进度（文件仍实时写入）。

---

## 三类检查

1. **唯一性**：`norm_date` + 业务键（`code_rq` / `code` / `indus_code`）不得重复  
2. **字段完整**：按交易日逐日拉取文档，在内存中检查必填字段是否存在（值可为 null）  
3. **日期完整**：区间内每个交易日均有数据；`rq_bench` 额外提示每日是否少于 6 条  

修改默认起始日或字段：编辑 `check_historical_data.py` 内 `TABLE_SPECS`。
