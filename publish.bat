@echo off
rem Publish the latest version of this project to github.com/ai589/simitrade
rem Mirrors the folder (minus caches/logs) into a standalone repo and pushes.
set WORK=%LOCALAPPDATA%\simitrade-publish

robocopy "%~dp0." "%WORK%" /MIR /XD .git __pycache__ .claude /XF px5y_cache.json px10y_cache.json earnings_cache.json refresh_log.txt /NFL /NDL /NJH
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
:done
timeout /t 5 >nul
