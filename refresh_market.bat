@echo off
rem Unattended refresh at US market open/close (used by the scheduled task
rem "US Market Open-Close Refresh"). Fires at 21:40, 22:40, 04:15, 05:15 SGT;
rem market_guard.py exits 1 unless it is just after the NY open or close
rem (handles US daylight saving), so only the right two fire each day.
cd /d "%~dp0"
if not exist "%~dp0src\screener.py" (
  echo ===== %date% %time% ===== >> "%~dp0logs\refresh_log.txt"
  echo FATAL: src\screener.py not found under "%~dp0" - broken checkout or half-moved folder. >> "%~dp0logs\refresh_log.txt"
  echo Run tools\install_tasks.ps1 to repoint the scheduled tasks. >> "%~dp0logs\refresh_log.txt"
  exit /b 1
)
python src\market_guard.py >> logs\refresh_log.txt 2>&1
if errorlevel 1 exit /b 0
echo ===== %date% %time% (US open/close refresh) ===== >> logs\refresh_log.txt
python src\screener.py >> logs\refresh_log.txt 2>&1
if errorlevel 1 (
  echo ERROR: refresh failed - skipping publish. >> logs\refresh_log.txt
  exit /b 1
)
call "%~dp0publish_silent.bat"
