@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

REM =============================================================================
REM 更新 basic_rq：rq_bench + rq_quarterly + rq_yearly（摘自 v3_1 MasterData/data_rq）
REM 迷你工程根目录 = 本脚本同级的 bench_quarterly_yearly
REM =============================================================================

set "MINI_ROOT=%~dp0bench_quarterly_yearly"
cd /d "%MINI_ROOT%"
if errorlevel 1 (
  echo [ERROR] 无法进入目录: %MINI_ROOT%
  exit /b 100
)

REM 按本机修改 Python（原 v3 项目使用 pyenv 的写法已改为与本分包其它 bat 一致）
set "PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"

set "MONGO_ALIAS=local"
set "MONGO_DB=basic_rq"
set "AUTO_MODE=today_if_trade"

set "SCRIPT_BENCH=%MINI_ROOT%\MasterData\data_rq\update_daily_bench.py"
set "SCRIPT_QUARTERLY=%MINI_ROOT%\MasterData\data_rq\update_daily_rq_quarterly.py"
set "SCRIPT_YEARLY=%MINI_ROOT%\MasterData\data_rq\update_daily_rq_yearly.py"

echo [START] %date% %time%
echo Mini root: %MINI_ROOT%
echo Python   : %PYTHON_EXE%
echo.

echo [1/3] update_daily_bench.py ...
"%PYTHON_EXE%" -u "%SCRIPT_BENCH%" --mongo-alias "%MONGO_ALIAS%" --mongo-db "%MONGO_DB%" --auto-mode "%AUTO_MODE%"
if errorlevel 1 (
  echo [FAIL] bench 更新失败，终止任务
  exit /b 11
)
echo [OK] bench 完成
echo.

echo [2/3] update_daily_rq_quarterly.py ...
"%PYTHON_EXE%" -u "%SCRIPT_QUARTERLY%" --mongo-alias "%MONGO_ALIAS%" --mongo-db "%MONGO_DB%" --auto-mode "%AUTO_MODE%"
if errorlevel 1 (
  echo [FAIL] quarterly 更新失败，终止任务
  exit /b 12
)
echo [OK] quarterly 完成
echo.

echo [3/3] update_daily_rq_yearly.py ...
"%PYTHON_EXE%" -u "%SCRIPT_YEARLY%" --mongo-alias "%MONGO_ALIAS%" --mongo-db "%MONGO_DB%" --auto-mode "%AUTO_MODE%"
if errorlevel 1 (
  echo [FAIL] yearly 更新失败，终止任务
  exit /b 13
)
echo [OK] yearly 完成
echo.

echo [DONE] 全部任务执行成功 %date% %time%
exit /b 0
