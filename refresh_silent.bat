@echo off
rem Unattended daily refresh (used by the scheduled task). Logs to refresh_log.txt.
cd /d "%~dp0"
echo ===== %date% %time% ===== >> refresh_log.txt
python screener.py >> refresh_log.txt 2>&1
