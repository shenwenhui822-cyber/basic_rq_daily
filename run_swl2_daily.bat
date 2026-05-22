@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"

echo =======================================
echo 申万二级 SWL2：rq_daily_indusSWL2 + rq_daily_indusSWL2_price
echo 时间：%date% %time%
echo =======================================

"%PYTHON%" "%~dp0rq_daily_update\update_rq_SWL2.py"
if errorlevel 1 ( pause & exit /b 1 )

echo.
"%PYTHON%" "%~dp0rq_daily_update\update_rq_SWL2_price.py"
if errorlevel 1 ( pause & exit /b 1 )

echo.
echo SWL2 完成 %date% %time%
pause
