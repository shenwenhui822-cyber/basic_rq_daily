# 历史数据补齐（一次性）

本目录脚本用于 **按区间/全量** 回填 `basic_rq`。运行前请保证 `economic.trade_dates` 可用；步骤 5 要求对应区间 **`rq_base_info` 已存在**（先跑步骤 1）。

**工作目录**：`basic_rq_daily` 根目录。日期参数支持 `YYYYMMDD`、`YYYY-MM-DD`、`YYYY/MM/DD`。

| 顺序 | 脚本 | 集合 |
|------|------|------|
| 1 | `load_rqbaseInfofastmain.py` | `rq_base_info` |
| 2 | `load_rq_basic_financialmain.py` | `rq_basic_financial` |
| 3 | `get_SWL2_2DB_Main.py` | `rq_daily_indusSWL2` |
| 4 | `get_SWL2_2DB_price_Main.py` | `rq_daily_indusSWL2_price` |
| 5 | `get_rq_in_index.py` | `rq_base_index` |

## 运行示例（同一区间）

将 `2026-03-16`、`2026-03-18` 换成你的起止日：

```bash
python rq_history_backfill/load_rqbaseInfofastmain.py --start 20150105 --end 20150305
python rq_history_backfill/get_rq_in_index.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/load_rq_basic_financialmain.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/get_SWL2_2DB_Main.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/get_SWL2_2DB_price_Main.py --start 2026-03-16 --end 2026-03-18

```

**单日**（财务脚本）：

```bash
python rq_history_backfill/load_rq_basic_financialmain.py --date 2026-05-12
```

**可选**：`--mongo-alias wonderwz27018_rw`（各脚本默认均为该别名）。

日更请改用上级 `rq_daily_update/` 中对应的 `update_*.py`。
