@echo off
REM One-time setup: runs run_daily.bat every weekday at 19:00 (after NSE publishes FII/DII data).
REM Uses the local PC clock - set it to IST, or adjust the time below.
schtasks /Create /TN "IndiaMarketsDashboard" /TR "\"%~dp0run_daily.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 19:00 /F
echo Done. To remove later: schtasks /Delete /TN "IndiaMarketsDashboard" /F
pause
