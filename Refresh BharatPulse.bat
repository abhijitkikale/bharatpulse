@echo off
cd /d "%~dp0"
title BharatPulse refresh helper
python tools\refresh_app.py
pause
