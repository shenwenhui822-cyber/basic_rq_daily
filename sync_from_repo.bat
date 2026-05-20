@echo off
chcp 65001 >nul
REM =============================================================================
REM 维护用：同步本分包
REM  (1) UpdataDaily 根：A 链路 + SWL2 + csv
REM  (2) v3_1 工程：bench / quarterly / yearly 迷你工程（若路径不同请改 V3）
REM =============================================================================

set "ROOT=%~dp0..\.."
set "V3=D:\EthanPython\v3_1_M_18_MainRunWuZhi1"
set "MINI=%~dp0bench_quarterly_yearly"

echo --- [1/3] 从 UpdataDaily 根目录复制 ---
echo 源：%ROOT%
copy /Y "%ROOT%\update_rqbaseInfo.py" "%~dp0"
copy /Y "%ROOT%\update_rq_basic_financail.py" "%~dp0"
copy /Y "%ROOT%\update_rq_in_index.py" "%~dp0"
copy /Y "%ROOT%\get_rq_in_index.py" "%~dp0"
copy /Y "%ROOT%\update_rq_SWL2.py" "%~dp0"
copy /Y "%ROOT%\update_rq_SWL2_price.py" "%~dp0"
copy /Y "%ROOT%\get_SWL2_2DB_price_Main.py" "%~dp0"
copy /Y "%ROOT%\get_SWL2_2DB_Main.py" "%~dp0"
copy /Y "%ROOT%\usedbdef.py" "%~dp0"
copy /Y "%ROOT%\trade_dates_all.csv" "%~dp0"

echo.
echo --- [2/3] 从 v3_1 复制 bench_quarterly_yearly 子树 ---
if not exist "%V3%\MasterData\data_rq\update_daily_bench.py" (
  echo [SKIP] 未找到 v3 路径：%V3%
  goto done_sync_v3
)
echo 源：%V3%
copy /Y "%V3%\DataBase\db_client.py" "%MINI%\DataBase\"
copy /Y "%V3%\MasterData\data_rq\update_daily_bench.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\MasterData\data_rq\update_daily_rq_quarterly.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\MasterData\data_rq\update_daily_rq_yearly.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\MasterData\data_rq\update_rq_bench.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\MasterData\data_rq\update_rq_quarterly_yearly_bench.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\MasterData\data_rq\ffill_rq_quarterly_yearly_missing.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\MasterData\data_rq\ffill_rq_quarterly_yearly_to_universe.py" "%MINI%\MasterData\data_rq\"
copy /Y "%V3%\Utils\utils_datetime.py" "%MINI%\Utils\"
copy /Y "%V3%\Utils\my_errors.py" "%MINI%\Utils\"

:done_sync_v3

echo --- [3/3] 全部完成 ---
echo.
echo 已完成。输出目录：%~dp0
pause
