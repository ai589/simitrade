@echo off
rem Unattended publish - called at the end of refresh_silent.bat / refresh_market.bat
rem once screener.py has succeeded. Same steps as publish.bat, but never pauses
rem (a scheduled task would hang on it) and logs everything to logs\publish_log.txt.
cd /d "%~dp0"
set WORK=%LOCALAPPDATA%\simitrade-publish
echo ===== %date% %time% (publish) ===== >> logs\publish_log.txt

rem Bump the dashboard version stamp (header "vYYYY.MM.DD-HHMM (SGT)") to now.
python -c "import re,datetime,io;p=r'%~dp0dashboard.html';s=io.open(p,encoding='utf-8').read();s=re.sub(r'v\d{4}\.\d{2}\.\d{2}-\d{4} \(SGT\)',datetime.datetime.now().strftime('v%%Y.%%m.%%d-%%H%%M (SGT)'),s,count=1);io.open(p,'w',encoding='utf-8',newline='').write(s)" >> logs\publish_log.txt 2>&1
if errorlevel 1 (
  echo ERROR: version stamp bump failed - nothing published. >> logs\publish_log.txt
  exit /b 1
)

robocopy "%~dp0." "%WORK%" /MIR /XD .git .vercel __pycache__ .claude logs /XF px5y_cache.json px10y_cache.json px10y_cache.meta.json earnings_cache.json universe.json tiger_config.json orders_sent.json refresh_log.txt publish_log.txt .env.local CLAUDE.md /NFL /NDL /NJH >> logs\publish_log.txt 2>&1
if errorlevel 8 (
  echo ERROR: file copy failed - nothing published. >> logs\publish_log.txt
  exit /b 1
)

git -C "%WORK%" add -A >> logs\publish_log.txt 2>&1
git -C "%WORK%" diff --cached --quiet && echo Nothing new to publish. >> logs\publish_log.txt && goto :done
git -C "%WORK%" commit -m "Update dashboard and data" >> logs\publish_log.txt 2>&1
git -C "%WORK%" push origin main >> logs\publish_log.txt 2>&1
if errorlevel 1 (
  echo ERROR: push failed - check internet / GitHub sign-in. Vercel deploy skipped. >> logs\publish_log.txt
  exit /b 1
)
echo Published to https://github.com/ai589/simitrade >> logs\publish_log.txt

call vercel deploy --prod --yes --cwd "%WORK%" >> logs\publish_log.txt 2>&1
if errorlevel 1 (
  echo WARNING: Vercel deploy failed - GitHub push succeeded. Run publish.bat manually. >> logs\publish_log.txt
  exit /b 1
)
echo Deployed to https://simitrade.vercel.app >> logs\publish_log.txt
:done
exit /b 0
