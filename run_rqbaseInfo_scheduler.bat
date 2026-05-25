@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 在下方或系统环境变量中配置 ALPHA_NOTIFY_*（勿将 .env 提交到 Git）
REM 可复制 .env.example 为 .env 后填写

set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
set "RQBASE_SCHEDULE_PORT=7331"

echo =======================================
echo rq_base_info 定时服务（交易日 09:20 写 T-1）
echo 端口：%RQBASE_SCHEDULE_PORT%
echo 健康检查：http://127.0.0.1:%RQBASE_SCHEDULE_PORT%/health
echo 手动触发：http://127.0.0.1:%RQBASE_SCHEDULE_PORT%/run
echo =======================================

REM 已迁移至 scheduled_jobs；请优先使用 run_scheduled_jobs.bat
"%PYTHON%" "%~dp0scheduled_jobs\run_server.py" --port %RQBASE_SCHEDULE_PORT%
pause
