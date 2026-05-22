@echo off
chcp 65001 >nul
set "ROOT=%~dp0..\.."
echo 从 UpdataDaily 根复制到：%~dp0
copy /Y "%ROOT%\update_rqMinPrice.py" "%~dp0"
copy /Y "%ROOT%\rq_getRangeMinPrice.py" "%~dp0"
copy /Y "%ROOT%\rq_getRangeDailyPrice.py" "%~dp0"
copy /Y "%ROOT%\get_Min1Test1.py" "%~dp0"
copy /Y "%ROOT%\get_dayDataTest1.py" "%~dp0"
copy /Y "%ROOT%\usedbdef.py" "%~dp0"
copy /Y "%ROOT%\rq_daily_update\update_rqbaseInfo.py" "%~dp0"
copy /Y "%ROOT%\trade_date_utils.py" "%~dp0"
echo 完成。
pause
