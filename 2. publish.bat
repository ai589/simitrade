@echo off
rem Publish the latest version of this project to github.com/ai589/simitrade
rem and deploy to Vercel (https://simitrade.vercel.app).
rem Mirrors the folder (minus caches/logs) into a standalone repo and pushes.
set WORK=%LOCALAPPDATA%\simitrade-publish

rem Bump the dashboard version stamp (header "vYYYY.MM.DD-HHMM (SGT)") to now.
python -c "import re,datetime,io;p=r'%~dp0dashboard.html';s=io.open(p,encoding='utf-8').read();s=re.sub(r'v\d{4}\.\d{2}\.\d{2}-\d{4} \(SGT\)',datetime.datetime.now().strftime('v%%Y.%%m.%%d-%%H%%M (SGT)'),s,count=1);io.open(p,'w',encoding='utf-8',newline='').write(s)"

robocopy "%~dp0." "%WORK%" /MIR /XD .git .vercel __pycache__ .claude logs /XF px5y_cache.json px10y_cache.json px10y_cache.meta.json earnings_cache.json universe.json refresh_log.txt .env.local CLAUDE.md /NFL /NDL /NJH
if errorlevel 8 (
  echo ERROR: file copy failed.
  pause
  exit /b 1
)

git -C "%WORK%" add -A
git -C "%WORK%" diff --cached --quiet && echo Nothing new to publish. && goto :done
git -C "%WORK%" commit -m "Update dashboard and data"
git -C "%WORK%" push origin main
if errorlevel 1 (
  echo ERROR: push failed. Check your internet connection / GitHub sign-in.
  pause
  exit /b 1
)
echo Published to https://github.com/ai589/simitrade

call vercel deploy --prod --yes --cwd "%WORK%"
if errorlevel 1 (
  echo WARNING: Vercel deploy failed - GitHub push succeeded. Run publish.bat again or "vercel deploy --prod" manually.
  pause
  exit /b 1
)
echo Deployed to https://simitrade.vercel.app
:done
timeout /t 5 >nul
