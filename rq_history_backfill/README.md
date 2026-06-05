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
| 6 | `rq_getRangeDailyPriceLongrun.py` | `rq_daily_price_none` |
| 7 | `backfill_rq_bench.py` | `rq_bench` |
| 8 | `backfill_rq_quarterly_yearly.py` | `rq_quarterly` / `rq_yearly` |

**季报 / 年报**完整命令与两阶段说明见 **[季报年报运行指南.md](./季报年报运行指南.md)**。

## 运行示例（同一区间）

将 `2026-03-16`、`2026-03-18` 换成你的起止日：

```bash
python rq_history_backfill/load_rqbaseInfofastmain.py --start 20150105 --end 20150305
python rq_history_backfill/get_rq_in_index.py --start 2024-01-01 --end 2024-12-31
python rq_history_backfill/load_rq_basic_financialmain.py --start 2017-09-02 --end 2017-11-01
python rq_history_backfill/get_SWL2_2DB_Main.py --start 2026-03-16 --end 2026-03-18
python3 rq_history_backfill/get_SWL2_2DB_price_Main.py --start 2023-04-01 --end 2026-05-25
python rq_history_backfill/rq_getRangeDailyPriceLongrun.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/backfill_rq_bench.py --start 2020-01-02 --end 2026-01-09

# 季报历史（步骤 1 拉米筐，详见 季报年报运行指南.md）
python -u rq_history_backfill/backfill_rq_quarterly_yearly.py --collections quarterly --start 2015-01-05 --end 2026-05-27 --no-ffill --skip-existing --mongo-alias wonderwz27018_rw

```

**全市场日线**（`rq_daily_price_none`）流量大：外层按自然年、内层按自然月拉基础信息，再**逐交易日拉取并立即落库**。入库 `date` 为 `YYYY-MM-DD`，价量两位小数。调试可加 `--no-mongo` 或 `--skip-price`。

**单日**（财务脚本）：

```bash
python rq_history_backfill/load_rq_basic_financialmain.py --date 2026-05-12
```

**可选**：`--mongo-alias wonderwz27018_rw`（各脚本默认均为该别名）。

日更请改用上级 `rq_daily_update/` 中对应的 `update_*.py`。
