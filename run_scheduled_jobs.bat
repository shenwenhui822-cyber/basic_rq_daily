@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
set "RQBASE_SCHEDULE_PORT=7331"

echo =======================================
echo scheduled_jobs 多任务定时服务
echo 端口：%RQBASE_SCHEDULE_PORT%
echo 健康检查：http://127.0.0.1:%RQBASE_SCHEDULE_PORT%/health
echo 手动触发：http://127.0.0.1:%RQBASE_SCHEDULE_PORT%/run
echo =======================================

"%PYTHON%" "%~dp0scheduled_jobs\run_server.py" --port %RQBASE_SCHEDULE_PORT%
pause
