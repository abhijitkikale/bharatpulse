@echo off
REM One-time setup of the two weekday morning tasks. The times are your PC clock (your PC is IST minus 2:30).
REM   1. 06:35 IST (04:05 on the PC): opens "Refresh BharatPulse" so the Fyers sign-in page is waiting for you. Sign in AFTER 06:30 IST.
REM   2. 09:20 IST (06:50 on the PC): checks that the helper runs and the sign-in is valid; warns you on Telegram if not.
REM The PC must be on and you must be logged in to Windows. Change the /ST times below if your clock differs.
for /f "delims=" %%P in ('where python') do (set PY=%%P& goto :found)
echo Python was not found. Install it or add it to PATH, then run this again.
pause
exit /b 1
:found
schtasks /Create /TN "BharatPulseMorning" /TR "\"%~dp0Refresh BharatPulse.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 04:05 /F
schtasks /Create /TN "BharatPulseCheck" /TR "\"%PY%\" \"%~dp0tools\morning_check.py\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 06:50 /F
echo Done. To remove later: schtasks /Delete /TN "BharatPulseMorning" /F   and   schtasks /Delete /TN "BharatPulseCheck" /F
pause
