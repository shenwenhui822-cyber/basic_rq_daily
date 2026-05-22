# basic_rq 每日更新（给同事 · 自包含分包）

本目录可 **单独打 zip** 发给同事。**安全**：多个脚本内含米筐 `rq.init(账号, 密码)`，外发前请改为同事账号或删除密码由对方本地填写。

---

## 一、本目录应包含的内容

| 路径 | 说明 |
|------|------|
| `README.md` | 本说明 |
| `requirements-basic_rq_daily.txt` | pip 依赖（含 bench/季/年 所需的 `python-dateutil`） |
| `run_basic_rq_daily.bat` | **A 链路**总控：`rq_base_info` → `rq_basic_financial` → `rq_base_index` |
| `updatebench_quarterly_yearly.bat` | **B 链路**总控：`rq_bench` → `rq_quarterly` → `rq_yearly` |
| `sync_from_repo.bat` | **仅维护者**：从 `UpdataDaily` 根 +（可选）`v3_1` 工程同步副本 |
| `rq_daily_update/` | **日更**：`update_rqbaseInfo` 等 5 个脚本（见子目录 README） |
| `rq_history_backfill/` | **历史补齐**：`load_*` / `get_*` 5 个脚本（见子目录 README） |
| `rq_paths.py` | 子目录脚本 import 上级 `mongo_connect` / `usedbdef` 用 |
| `trade_date_utils.py` | T-1 / 是否交易日（读 Mongo `economic.trade_dates`） |
| `run_swl2_daily.bat` | **SWL2**：`rq_daily_indusSWL2` + `rq_daily_indusSWL2_price` |
| `mongo_connect.py` | Mongo 连接配置（`local` / `wonderwz27018_*` 等别名） |
| `usedbdef.py` | SWL2：`insert_db_from_df` / `get_client`（与 `update_rqbaseInfo` 并存，勿删） |
| `bench_quarterly_yearly\` | B：迷你工程根（见下文），**勿改其内部相对层级** |

若缺文件，维护者运行 `sync_from_repo.bat`（并确认其中 `V3=` 路径）后再打包。

---

## 二、`basic_rq` 集合与脚本对应（总表）

| 集合 | 作用 | 入口 |
|------|------|------|
| `rq_base_info` | 基础信息、`code_rq` 等 | A：`rq_daily_update/update_rqbaseInfo.py` |
| `rq_basic_financial` | 市值与部分财务 TTM | A：`rq_daily_update/update_rq_basic_financail.py` |
| `rq_base_index` | 宽基成分 0/1 | 日更：`rq_daily_update/update_rq_in_index.py`；区间：`rq_history_backfill/get_rq_in_index.py` |
| `rq_daily_indusSWL2` | 申万二级行业成分 | SWL2：`rq_daily_update/update_rq_SWL2.py`（`run_swl2_daily.bat`） |
| `rq_daily_indusSWL2_price` | 申万二级成分 + 行业指数日 K | SWL2：`rq_daily_update/update_rq_SWL2_price.py` |
| `rq_bench` | 基准指数行情（对标 Wind bench） | B：`updatebench_quarterly_yearly.bat` 第 1 步 |
| `rq_quarterly` | 季报 PIT + 按 universe 前向补齐 | B：第 2 步 |
| `rq_yearly` | 年报 PIT + 按 universe 前向补齐 | B：第 3 步 |

---

## 三、A 链路：`rq_daily_update/`（顺序不可颠倒）

1. `rq_daily_update/update_rqbaseInfo.py` → `rq_base_info`  
2. `rq_daily_update/update_rq_basic_financail.py` → `rq_basic_financial`（依赖**同一交易日**步骤 1 已入库）  
3. `rq_daily_update/update_rq_in_index.py` → `rq_base_index`（依赖**同一交易日** `rq_base_info`）

一键：本目录下 **`run_basic_rq_daily.bat`**（改好其中 `PYTHON=`；计划任务请注释 `pause`）。

### 日更默认交易日：T-1（上一交易日）

下列脚本在**未传 `--date`** 时，均通过 `trade_date_utils.previous_trade_date` 从 **`economic.trade_dates`** 取 **严格早于今天** 的最近交易日落库（米筐当日数据通常晚间才稳定，故不用「今天」）：

| 脚本 | 集合 |
|------|------|
| `rq_daily_update/update_rqbaseInfo.py` | `rq_base_info` |
| `rq_daily_update/update_rq_basic_financail.py` | `rq_basic_financial` |
| `rq_daily_update/update_rq_in_index.py` | `rq_base_index` |
| `rq_daily_update/update_rq_SWL2.py` | `rq_daily_indusSWL2` |
| `rq_daily_update/update_rq_SWL2_price.py` | `rq_daily_indusSWL2_price` |

补跑指定日：`python rq_daily_update/update_rqbaseInfo.py --date 20260515`（亦支持 `2026/05/15`、`2026-05-15`）。建议计划任务安排在 **20:00 之后**，与 T-1 策略一致。

### 申万二级 SWL2（`rq_daily_update/` · `run_swl2_daily.bat`）

- **`run_swl2_daily.bat`**：依次 `update_rq_SWL2.py` → `rq_daily_indusSWL2`，`update_rq_SWL2_price.py` → `rq_daily_indusSWL2_price`。  
- 价量逻辑引用 **`rq_history_backfill/get_SWL2_2DB_price_Main.py`**；历史/区间全量见 **`rq_history_backfill/get_SWL2_2DB_Main.py`**。  
- 与 A 无硬依赖，建议 **在 A 之后**执行；交易日判断与 A 相同（`economic.trade_dates`）。

---

## 四、B 链路：bench / 季报 / 年报（`updatebench_quarterly_yearly.bat`）

来源：原工程 `v3_1_M_18_MainRunWuZhi1\MasterData\data_rq\updatebench_quarterly_yearly.bat` 及对应 Python，已收入子目录 **`bench_quarterly_yearly`**，批处理已改为使用 **`%~dp0bench_quarterly_yearly`** 为迷你根目录，不再依赖 `D:\EthanPython\v3_1_...`。

### 4.1 与 A 链路的关系

- **必须先跑通 A**（至少保证目标交易日 **`rq_base_info` 已更新**），再跑 B。  
- B 中季报/年报逻辑会读 `basic_rq.rq_base_info` 做按票补齐。

### 4.2 执行内容（与原版一致）

1. `update_daily_bench.py` → `rq_bench`  
2. `update_daily_rq_quarterly.py` → 当日 `rq_quarterly` 米筐更新 + 历史区间 **backfill**（默认自 `2020-01-02` 至本次 `pre_trade_day`）  
3. `update_daily_rq_yearly.py` → 当日 `rq_yearly` + 同上 backfill  

默认参数：`--mongo-alias local`、`--mongo-db basic_rq`、`--auto-mode previous_trade`（T-1，与 A/SWL2 日更一致）。  
修改方式：编辑 **`updatebench_quarterly_yearly.bat`** 顶部的 `MONGO_*`、`AUTO_MODE`、`PYTHON_EXE`。

### 4.3 重要前置：`economic.trade_dates`

三条 `update_daily_*.py` 均通过 **`get_client_U`** 连接 Mongo，并查询 **`economic.trade_dates`**（字段 **`trade_date`**，建议为 `YYYY-MM-DD` 字符串）推导「是否交易日 / 上一交易日 / 下一交易日」。

若库中**没有**该集合或为空，脚本会报错或无法推导日期。**请先从主数据工程同步交易日表，或自行导入**，再跑 B 链路。

### 4.4 迷你工程结构（仅供排错）

```
bench_quarterly_yearly/
  DataBase/db_client.py
  MasterData/data_rq/update_daily_bench.py
  MasterData/data_rq/update_daily_rq_quarterly.py
  MasterData/data_rq/update_daily_rq_yearly.py
  MasterData/data_rq/update_rq_bench.py
  MasterData/data_rq/update_rq_quarterly_yearly_bench.py
  MasterData/data_rq/ffill_rq_quarterly_yearly_missing.py
  MasterData/data_rq/ffill_rq_quarterly_yearly_to_universe.py
  Utils/utils_datetime.py
  Utils/my_errors.py
```

脚本内 `sys.path` 以「`MasterData/data_rq` 上溯两级」为假项目根，**必须与上表层级一致**。

### 4.5 单日 / 调试

可在「假项目根」下直接调用（示例）：

```bat
cd /d D:\路径\basic_rq_daily\bench_quarterly_yearly
python -u MasterData\data_rq\update_daily_bench.py --dry-run
```

更多参数见各文件顶部 docstring（`--pre-day`、`--no-backfill`、`--auto-mode previous_trade` 等）。

---

## 五、推荐的一日调度顺序

1. **`run_basic_rq_daily.bat`**（A 链路）  
2. **`run_swl2_daily.bat`**（申万二级；若暂不维护行业可跳过）  
3. **`updatebench_quarterly_yearly.bat`**（B 链路）  

B 耗时会明显长于 A（含逐票 backfill）。若只想日更、暂不扫历史，可在 Python 命令中增加 `--no-backfill`（需改 bat 内三行 `python` 调用，或命令行单独跑）。

---

## 六、环境与安装

- **Python** 3.10+ 推荐。  
- **MongoDB**：本目录 `mongo_connect.py` 中 `local` 默认 `192.168.110.199:27018`（可用 `MONGO_HOST` / `MONGO_PORT` 覆盖）；日更写库多用 `wonderwz27018_rw`。  
- **安装依赖**：

```bat
pip install -r requirements-basic_rq_daily.txt
```

---

## 七、历史数据补齐（一次性，按顺序）

先改各脚本底部日期/Mongo 常量，再在本目录执行。写库默认别名 **`wonderwz27018_rw`**（见 `mongo_connect.py`）。

| 顺序 | 脚本 | 目标集合 | 改什么 |
|------|------|----------|--------|
| 1 | `rq_history_backfill/load_rqbaseInfofastmain.py` | `rq_base_info` | `--start` / `--end` |
| 2 | `rq_history_backfill/load_rq_basic_financialmain.py` | `rq_basic_financial` | `--start` / `--end`；单日 `--date` |
| 3 | `rq_history_backfill/get_SWL2_2DB_Main.py` | `rq_daily_indusSWL2` | `--start` / `--end` |
| 4 | `rq_history_backfill/get_SWL2_2DB_price_Main.py` | `rq_daily_indusSWL2_price` | `--start` / `--end`（建议步骤 3 之后） |
| 5 | `rq_history_backfill/get_rq_in_index.py` | `rq_base_index` | `--start` / `--end`（须对应区间已在 `rq_base_info`） |

示例：

```bat
cd /d 本目录
python rq_history_backfill/load_rqbaseInfofastmain.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/load_rq_basic_financialmain.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/get_SWL2_2DB_Main.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/get_SWL2_2DB_price_Main.py --start 2026-03-16 --end 2026-03-18
python rq_history_backfill/get_rq_in_index.py --start 2026-03-16 --end 2026-03-18
```

区间指数成分亦可只改 **`rq_history_backfill/get_rq_in_index.py`** 后单独执行（见上表第 5 行）。

---

## 八、任务计划程序（Windows）

- A：`...\basic_rq_daily\run_basic_rq_daily.bat`（注释末尾 `pause`）  
- SWL2：`...\basic_rq_daily\run_swl2_daily.bat`（按需；计划任务可注释 `pause`）  
- B：`...\basic_rq_daily\updatebench_quarterly_yearly.bat`（无 `pause`，可直接挂计划任务）  
- 时间：**20:00 后**跑 A/SWL2（T-1 日更）；B 建议再晚一段，且需 `economic.trade_dates` 已同步。

---

## 九、维护者同步

- **`UpdataDaily` 根**：`rq_daily_update/`、`rq_history_backfill/`、`rq_paths.py`（`sync_from_repo.bat` 第 1 段）。  
- **`bench_quarterly_yearly`**：由同脚本第 2 段从 **`V3=D:\EthanPython\v3_1_M_18_MainRunWuZhi1`** 覆盖；若本机 v3 路径不同，请编辑再执行。

---

## 十、常见问题

| 现象 | 可能原因 |
|------|----------|
| `无法根据 run-date 解析 pre_trade_day` | `economic.trade_dates` 缺失或与 `run-date` 范围不匹配。 |
| `rq_base_info 中无数据` | 未先跑 A 链路。 |
| `不是交易日，跳过` | A 中财务脚本正常行为。 |
| T-1 / 是否交易日报错 | 检查 Mongo `economic.trade_dates` 及 `--mongo-alias`（可用环境变量 `MONGO_TRADE_ALIAS`）。 |
| `ModuleNotFoundError: dateutil` | `pip install python-dateutil` 或重装 requirements。 |

---

## 十一、不包含的内容

**RQ 1 分钟线**在 **`packforcolleague/rq_minute_daily/`**。  
MC400、全市场日线价量等仍在主仓库其它脚本；可从 **`UpdataDaily`** 或 **`v3_1`** 工程另行获取。

---

## 十二、RQ 1 分钟线（独立分包）

秒级流量大，已拆至子目录 **`rq_minute_daily/`**（单日 `update_rqMinPrice`、区间 `rq_getRangeMinPrice`）；Mongo 配置共用本目录 **`mongo_connect.py`**，打 zip 时请与 `rq_minute_daily` 一并包含。
