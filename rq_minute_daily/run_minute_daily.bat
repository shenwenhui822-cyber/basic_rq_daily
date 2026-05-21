@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"

echo =======================================
echo RQ 1 分钟线日更（需先 rq_base_info）
echo 时间：%date% %time%
echo =======================================

echo [1/2] update_rqbaseInfo.py
"%PYTHON%" "%~dp0update_rqbaseInfo.py"
if errorlevel 1 ( echo FAILED update_rqbaseInfo & pause & exit /b 1 )

echo.
echo [2/2] update_rqMinPrice.py
"%PYTHON%" "%~dp0update_rqMinPrice.py"
if errorlevel 1 ( echo FAILED update_rqMinPrice & pause & exit /b 1 )

echo.
echo =======================================
echo 分钟线日更完成 %date% %time%
echo =======================================
pause
