@echo off
rem Unattended daily refresh (used by the scheduled task). Logs to logs\refresh_log.txt.
rem On success it publishes to GitHub + Vercel via publish_silent.bat.
cd /d "%~dp0"
if not exist "%~dp0src\screener.py" (
  echo ===== %date% %time% ===== >> "%~dp0logs\refresh_log.txt"
  echo FATAL: src\screener.py not found under "%~dp0" - broken checkout or half-moved folder. >> "%~dp0logs\refresh_log.txt"
  echo Run tools\install_tasks.ps1 to repoint the scheduled tasks. >> "%~dp0logs\refresh_log.txt"
  exit /b 1
)
echo ===== %date% %time% ===== >> logs\refresh_log.txt
python src\screener.py >> logs\refresh_log.txt 2>&1
if errorlevel 1 (
  echo ERROR: refresh failed - skipping publish. >> logs\refresh_log.txt
  exit /b 1
)
call "%~dp0publish_silent.bat"
