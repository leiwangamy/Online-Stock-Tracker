@echo off
REM LeiBot IBKR Phase 2 - Paper TWS smoke test (local only, no prod, no orders)
cd /d "%~dp0.."
python scripts\ibkr_paper_smoke_test.py %*
exit /b %ERRORLEVEL%
