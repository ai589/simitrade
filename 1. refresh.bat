@echo off
rem Open the dashboard instantly, then refresh data in the background.
rem The page auto-reloads itself when the new data lands (~30-60s).
cd /d "%~dp0"
start "" dashboard.html
echo Dashboard opened with existing data.
echo Pulling fresh prices now - the page will reload itself when done...
python src\screener.py
if errorlevel 1 (
  echo.
  echo ERROR: refresh failed. The dashboard is still showing the previous data.
  pause
  exit /b 1
)
echo Done. You can close this window.
timeout /t 5 >nul
