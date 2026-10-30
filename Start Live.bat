@echo off
rem Starts the BharatPulse live loop (updates the dashboard every 30 minutes during market hours).
rem Sign in to Fyers first each morning (Refresh BharatPulse.bat). Keep this window open until the close.
cd /d "%~dp0"
python tools\live_loop.py
pause
