# scheduled_jobs — 定时任务与邮件通知

业务脚本仍在 **`rq_daily_update/`**；本目录负责调度与邮件。

## 时间表配置（增删任务改这里）

`jobs/schedule_config.py`：

```python
SCHEDULE_ENTRIES = [
    ("09:05", "update_rq_basic_financial", {"scheduler_job_key": "rq_basic_financial"}),
    ("09:20", "update_rq_base_info", {"scheduler_job_key": "rq_base_info"}),
]
```

| 时刻 | 任务 | 说明 |
|------|------|------|
| 09:05 | `rq_basic_financial` | 上一交易日 `rq_basic_financial`（依赖已有 T-1 `rq_base_info`） |
| 09:20 | `rq_base_info` | 上一交易日 `rq_base_info` |

新增任务：在 `jobs/` 增加 `xxx.py`（实现 `run()` + `SCHEDULER_JOB_KEY`），在 `registry.py` 的 `_RUNNER_MAP` 注册，并在 `SCHEDULE_ENTRIES` 追加一行。

## 启动

```bash
python scheduled_jobs/run_server.py --port 7331
```

- `http://127.0.0.1:7331/health`
- `http://127.0.0.1:7331/run?job=rq_basic_financial`
- `http://127.0.0.1:7331/run?job=rq_base_info`

邮件：根目录 `.env` 中 `ALPHA_NOTIFY_*`。

入库 **date** 格式统一为 **`YYYY-MM-DD`**（如 `2015-09-30`）。
