# 每日更新（T-1 日更）

本目录脚本用于 **定时/每日** 写入 `basic_rq`，默认上一交易日（`trade_date_utils`）。

| 脚本 | 集合 |
|------|------|
| `update_rqbaseInfo.py` | `rq_base_info` |
| `update_rq_basic_financail.py` | `rq_basic_financial` |
| `update_rq_in_index.py` | `rq_base_index` |
| `update_rq_SWL2.py` | `rq_daily_indusSWL2` |
| `update_rq_SWL2_price.py` | `rq_daily_indusSWL2_price` |
| `update_rqDailyPrice.py` | `rq_daily_price_none`（全市场不复权日线，依赖 `rq_base_info`） |
| `update_rq_bench.py` | `rq_bench`（基准指数：300/500/1000/全A 等） |

**A 链路顺序**（不可颠倒）：`update_rqbaseInfo` → `update_rq_basic_financail` → `update_rq_in_index`。

一键：上级目录 `run_basic_rq_daily.bat`（A）、`run_swl2_daily.bat`（申万二级）。

运行示例（工作目录为 **`basic_rq_daily` 根目录**）：

```bash
python rq_daily_update/update_rqbaseInfo.py
python rq_daily_update/update_rq_basic_financail.py
python rq_daily_update/update_rq_in_index.py
python rq_daily_update/update_rqDailyPrice.py
python rq_daily_update/update_rq_bench.py
```

共用上级 `mongo_connect.py`、`trade_date_utils.py`、`usedbdef.py`；`update_rq_in_index` / `update_rq_SWL2_price` 会引用 `rq_history_backfill` 中的逻辑模块。
