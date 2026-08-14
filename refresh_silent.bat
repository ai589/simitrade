@echo off
rem Unattended daily refresh (used by the scheduled task). Logs to logs\refresh_log.txt.
rem On success it publishes to GitHub + Vercel via publish_silent.bat.
cd /d "%~dp0"
echo ===== %date% %time% ===== >> logs\refresh_log.txt
python src\screener.py >> logs\refresh_log.txt 2>&1
if errorlevel 1 (
  echo ERROR: refresh failed - skipping publish. >> logs\refresh_log.txt
  exit /b 1
)
call "%~dp0publish_silent.bat"
