@echo off
rem Unattended refresh at US market open/close (used by the scheduled task
rem "US Market Open-Close Refresh"). Fires at 21:40, 22:40, 04:15, 05:15 SGT;
rem market_guard.py exits 1 unless it is just after the NY open or close
rem (handles US daylight saving), so only the right two fire each day.
cd /d "%~dp0"
python market_guard.py >> refresh_log.txt 2>&1
if errorlevel 1 exit /b 0
echo ===== %date% %time% (US open/close refresh) ===== >> refresh_log.txt
python screener.py >> refresh_log.txt 2>&1
