@echo off
rem Weekday 08:05 SGT brief: what to do today, from the overnight US session.
rem Registered as the scheduled task "ECL Morning Brief" by tools\install_tasks.ps1.
rem Writes output\brief-YYYY-MM-DD.txt and logs to logs\brief_log.txt.
cd /d "%~dp0"
if not exist "%~dp0src\morning_brief.py" (
  echo ===== %date% %time% ===== >> "%~dp0logs\brief_log.txt"
  echo FATAL: src\morning_brief.py not found under "%~dp0" - broken checkout or half-moved folder. >> "%~dp0logs\brief_log.txt"
  echo Run tools\install_tasks.ps1 to repoint the scheduled tasks. >> "%~dp0logs\brief_log.txt"
  exit /b 1
)
echo ===== %date% %time% ===== >> logs\brief_log.txt
python src\morning_brief.py --save >> logs\brief_log.txt 2>&1
rem morning_brief.py exits 1 on stale data so the task shows as failed rather than
rem passing quietly with a warning nobody reads.
exit /b %errorlevel%
