@echo off
REM LeiBot manual refresh: all pool prices + Watchlist (visible progress window)
title LeiBot - Updating market data
setlocal
set "ROOT=C:\Users\Admin\Documents\Online-Stock-Tracker"
set "PY=%ROOT%\venv\Scripts\python.exe"
cd /d "%ROOT%"

echo ========================================
echo  LeiBot manual data refresh
echo  - All index pools (dashboard cache)
echo  - Watchlist (incl. MANUAL tickers)
echo  Log: data\logs\update_jobs.log
echo ========================================
echo.

if not exist "%PY%" (
  echo ERROR: venv python not found at %PY%
  pause
  exit /b 1
)

"%PY%" update_jobs.py --prices
set "EC=%ERRORLEVEL%"
echo.
if "%EC%"=="0" (
  echo DONE. Refresh the browser to see new prices / 1Y Target.
) else (
  echo FINISHED WITH ERRORS. See data\logs\update_jobs.log
)
echo.
pause
exit /b %EC%
