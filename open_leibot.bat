@echo off
REM LeiBot — start local server (if needed) and open Watchlist in browser.
setlocal
set "ROOT=C:\Users\Admin\Documents\Online-Stock-Tracker"
set "PY=%ROOT%\venv\Scripts\python.exe"
set "URL=http://127.0.0.1:3000/watchlist?tab=mine"

cd /d "%ROOT%"

powershell -NoProfile -Command ^
  "$c = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if (-not $c) {" ^
  "  Start-Process -FilePath '%PY%' -ArgumentList 'app.py' -WorkingDirectory '%ROOT%' -WindowStyle Minimized;" ^
  "  $ok = $false;" ^
  "  for ($i=0; $i -lt 40; $i++) {" ^
  "    Start-Sleep -Milliseconds 500;" ^
  "    try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ge 200) { $ok = $true; break } } catch {}" ^
  "  }" ^
  "  if (-not $ok) { Write-Host 'Server starting slowly — opening browser anyway...' }" ^
  "}"

start "" "%URL%"
endlocal
