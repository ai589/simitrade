@echo off
rem Dry-run of this week's bracket orders (no broker contact). Pass --paper / --live to send.
cd /d "%~dp0"
python src\push_orders.py %*
pause
