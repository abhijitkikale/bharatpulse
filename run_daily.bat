@echo off
REM Refreshes the India Markets dashboard (fetch -> compute -> build dashboard.html).
cd /d "%~dp0pipeline"
python run.py > "%~dp0data\last_run.log" 2>&1
