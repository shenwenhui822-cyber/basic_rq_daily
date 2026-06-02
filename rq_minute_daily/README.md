# RQ 1 分钟线（给同事 · 独立分包）

本目录**仅**含：米筐 **1 分钟线** 单日更新与**区间落库**相关脚本，与 **`basic_rq_daily`**（含 SWL2、财务、指数等）**分开打包**。

> 分钟线流量与 Mongo 体积远大于日线，请注意 RQ 配额；`rq_getRangeMinPrice.py` 内有流量检查。

---

## 一、文件清单

| 文件 | 说明 |
|------|------|
| `run_minute_daily.bat` | 先 `update_rqbaseInfo` 再 `update_rqMinPrice`（改 `PYTHON=`） |
| `update_rqbaseInfo.py` | 写入 `basic_rq.rq_base_info`（分钟脚本读当日 `code_rq`） |
| `update_rqMinPrice.py` | 单日全市场 1m → **`rq_minute.rq_minute_none_YYYY`**（按数据日年份自动分表） |
| `rq_getRangeMinPrice.py` | 区间 1m，同样按交易日年份写入 `rq_minute_none_YYYY` |
| `rq_getRangeDailyPrice.py` | 被区间分钟引用（lookup `date`+`code_rq`） |
| `get_Min1Test1.py` / `get_dayDataTest1.py` | 分钟拉数与字段辅助 |
| `usedbdef.py` | `get_client` / `insert_db_from_df`（连接读上级 **`../mongo_connect.py`**） |
| （上级）`trade_date_utils.py` | 读 Mongo `economic.trade_dates` 判断交易日 / T-1 |
| `sync_from_repo.bat` | 维护：从 `UpdataDaily` 根覆盖上述列表 |
| `requirements-rq_minute.txt` | pip 依赖 |

---

## 二、调度建议

- **单日**：`run_minute_daily.bat`（计划任务请注释末尾 `pause`）。  
- **区间**：编辑 `rq_getRangeMinPrice.py` 底部 `START_DATE` / `END_DATE` 或 `SINGLE_DAY`，再 `python rq_getRangeMinPrice.py`。

---

## 三、与 `basic_rq_daily` 的关系

若同事已跑 **`basic_rq_daily/rq_daily_update/update_rqbaseInfo.py`**（或本目录自带的 `update_rqbaseInfo.py` 副本），可**只跑** `update_rqMinPrice.py`，避免重复拉基础表；若无法保证 `rq_base_info` 已更新，请继续用完整 `run_minute_daily.bat`。

---

## 四、安装

```bat
pip install -r requirements-rq_minute.txt
```

- **MongoDB**：与 `basic_rq_daily` 共用上级 **`mongo_connect.py`**（默认写库 `wonderwz27018_rw` → `192.168.110.199:27018`）；单独 zip 本目录时须同时带上该文件并保持 `rq_minute_daily` 在其下一级。  
- 米筐账号在 `update_rqbaseInfo.py`、`rq_getRangeDailyPrice.py` 等模块的 `rq.init` 中，外发前请替换。
