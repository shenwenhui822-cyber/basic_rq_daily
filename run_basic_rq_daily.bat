@echo off
chcp 65001 >nul

REM =============================================================================
REM basic_rq 每日更新总控（顺序不可颠倒）
REM 与本 bat 同目录须有所列 .py 与 trade_dates_all.csv（自包含分包）
REM =============================================================================

cd /d "%~dp0"

REM 按本机环境修改 Python 路径；若已加入 PATH，可改为：py -3
set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"

echo =======================================
echo basic_rq 每日更新链
echo 工作目录：%CD%
echo 运行时间：%date% %time%
echo =======================================

echo.
echo [1/3] update_rqbaseInfo.py -^> basic_rq.rq_base_info
"%PYTHON%" "%~dp0update_rqbaseInfo.py"
if errorlevel 1 (
    echo.
    echo [1/3] 失败，停止。
    pause
    exit /b 1
)

echo.
echo [2/3] update_rq_basic_financail.py -^> basic_rq.rq_basic_financial
"%PYTHON%" "%~dp0update_rq_basic_financail.py"
if errorlevel 1 (
    echo.
    echo [2/3] 失败，停止。
    pause
    exit /b 1
)

echo.
echo [3/3] update_rq_in_index.py -^> basic_rq.rq_base_index
"%PYTHON%" "%~dp0update_rq_in_index.py"
if errorlevel 1 (
    echo.
    echo [3/3] 失败，停止。
    pause
    exit /b 1
)

echo.
echo =======================================
echo basic_rq 每日链执行完成
echo 完成时间：%date% %time%
echo =======================================

REM 任务计划程序中请注释 pause
pause
