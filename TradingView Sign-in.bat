@echo off
cd /d "%~dp0"
echo Sign in to TradingView in the window that opens, wait for your chart, then close the window.
python tools\tv_live_paste.py --login
pause
