@echo off
REM One-time setup: opens "Refresh BharatPulse" every weekday morning, so the Fyers sign-in page is already waiting for you.
REM The time is your PC clock. Your PC is IST minus 2:30, so 06:20 on the PC = 08:50 IST. Change /ST below if your clock differs.
REM The PC must be on and you must be logged in. After the sign-in everything else starts by itself.
schtasks /Create /TN "BharatPulseMorning" /TR "\"%~dp0Refresh BharatPulse.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 06:20 /F
echo Done. To remove later: schtasks /Delete /TN "BharatPulseMorning" /F
pause
