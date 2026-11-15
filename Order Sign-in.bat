@echo off
cd /d "%~dp0"
echo Fyers sign-in for the ORDER app (needed only when data\orders_config.json says "live").
python tools\fyers_order_login.py
pause
