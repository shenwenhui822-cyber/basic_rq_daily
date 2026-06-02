# scheduled_jobs — 定时任务与邮件通知

业务脚本仍在 **`rq_daily_update/`**；本目录负责调度与邮件。

## 时间表配置（增删任务改这里）

`jobs/schedule_config.py`：

**完整说明（时刻、落库位置、字段含义）** → **[定时任务说明.md](./定时任务说明.md)**

```python
SCHEDULE_ENTRIES = [
    ("09:03", "update_rq_base_info", {"scheduler_job_key": "rq_base_info"}),
    ("09:13", "update_rq_SWL2_price", {"scheduler_job_key": "rq_swl2_price"}),
]
```

| 时刻 | 任务 | 说明 |
|------|------|------|
| 09:03 | `rq_base_info` | 上一交易日 `rq_base_info` |
| 09:05 | `rq_basic_financial` | 上一交易日 `rq_basic_financial` |
| 09:07 | `rq_in_index` | 上一交易日 `rq_base_index`（依赖 T-1 `rq_base_info`） |
| 09:10 | `rq_swl2` | 上一交易日 `rq_daily_indusSWL2`（申万二级行业成分） |
| 09:13 | `rq_swl2_price` | 上一交易日 `rq_daily_indusSWL2_price`（申万二级行业价量） |
| 09:15 | `rq_bench` | 上一交易日 `rq_bench`（基准指数行情） |
| 09:18 | `rq_daily_price` | 上一交易日 `rq_daily_price_none`（全市场不复权日线） |
| 09:20 | `rq_quarterly` | 上一交易日 `rq_quarterly`（季报 PIT + ffill） |
| 09:25 | `rq_yearly` | 上一交易日 `rq_yearly`（年报 PIT + ffill） |
| 09:35 | `rq_minute` | 上一交易日 `rq_minute_none_YYYY`（全市场 1 分钟线，按年分表） |
| 10:00 | `rq_minute_backfill` | 10:00 起至 14:40 按月倒序批量补历史分钟线 |

新增任务：在 `jobs/` 增加 `xxx.py`（实现 `run()` + `SCHEDULER_JOB_KEY`），在 `registry.py` 的 `_RUNNER_MAP` 注册，并在 `SCHEDULE_ENTRIES` 追加一行。

## 启动

**Ubuntu（推荐）：**

```bash
chmod +x start_scheduled_jobs.sh
./start_scheduled_jobs.sh
```

**手动：**

```bash
python scheduled_jobs/run_server.py --port 7331
```

- `http://127.0.0.1:7331/health` — 服务状态
- `http://127.0.0.1:7331/run?job=all` — 按 `SCHEDULE_ENTRIES` 顺序手动跑全部日更
- `http://127.0.0.1:7331/run?job=rq_base_info` — 只跑单个任务
- `http://127.0.0.1:7331/run?job=rq_swl2` — 只跑申万二级行业成分
- `http://127.0.0.1:7331/run?job=rq_swl2_price` — 只跑申万二级行业价量
- `http://127.0.0.1:7331/run?job=rq_bench` — 只跑基准指数行情
- `http://127.0.0.1:7331/run?job=rq_quarterly` — 只跑季报
- `http://127.0.0.1:7331/run?job=rq_yearly` — 只跑年报
- `http://127.0.0.1:7331/run?job=rq_minute` — 只跑 1 分钟线
- `http://127.0.0.1:7331/run?job=rq_minute_backfill` — 倒序补 1 日历史分钟线
- `http://127.0.0.1:7331/run?job=rq_daily_price` — 只跑全市场日线价量
- 裸 `/run` **不会执行**任何任务

邮件：根目录 `.env` 中 `ALPHA_NOTIFY_*`。

## 运行日志（MongoDB）

每次任务执行完成后、**发送邮件之前**，写入 `basic_rq_logs.rq_logs`（可用环境变量 `RQ_LOGS_DB` / `RQ_LOGS_COLLECTION` 覆盖）。

主要字段：`run_at`、`job_id`、`task_name`、`schedule_time`、`ok`、`skipped`、`message`、`detail`、`email_pending`。

入库 **date** 格式统一为 **`YYYY-MM-DD`**（如 `2015-09-30`）。
