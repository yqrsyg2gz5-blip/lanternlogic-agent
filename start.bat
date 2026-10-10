@echo off
REM ============================================================
REM  LanternLogic Agent - one-click start (Phase 1 (2))
REM
REM  This file stays ASCII-only on purpose: zh-CN cmd defaults to
REM  GBK, and a UTF-8 .bat shows mojibake. All Chinese output comes
REM  from scripts\preflight.py (after chcp 65001), so the user reads
REM  real Chinese instead of pinyin.
REM
REM  Flow: preflight -> backend (which also serves the built UI at
REM  frontend\dist) -> open browser. No Node needed at runtime; if the
REM  UI was never built, preflight says exactly how to build it.
REM
REM  !! 2026-10-10 INCIDENT - the ASCII-only rule above is not cosmetic:
REM  this file once carried Chinese echo text; zh-CN cmd reads .bat as GBK,
REM  the UTF-8 bytes shifted its line bookkeeping, and a double-click printed
REM  a screen of  'ight.py' is not recognized ...  instead of starting -
REM  the one-click entry was dead on this machine, on GitHub and on Gitee
REM  alike. Guard: backend\tests\test_bat_encoding.py - pure ASCII + CRLF.
REM ============================================================
title LanternLogic Agent
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM ---- pick a python: project venv first, then PATH -------------------
set "PY=%ROOT%backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM ---- preflight (Chinese report; exit code 1 = blocking issue) --------
"%PY%" "%ROOT%scripts\preflight.py"
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)

REM ---- UI url (the backend serves the built interface itself) ---------
set "URL="
for /f "usebackq delims=" %%u in (`"%PY%" "%ROOT%scripts\preflight.py" --print-url`) do set "URL=%%u"
if "%URL%"=="" set "URL=http://127.0.0.1:8642/"

REM ---- backend (skip if something is already listening) --------------
echo  [1/1] Starting the backend - it also serves the UI ...
curl -s -m 2 -o nul "%URL%api/v1/auth/check"
if not errorlevel 1 goto backend_up

cd /d "%ROOT%backend"
start "AgentShell-Backend" /min cmd /c ""%PY%" -m app.main >> "%ROOT%backend_restart.log" 2>&1"

set /a tries=0
:wait_backend
timeout /t 2 /nobreak >nul
curl -s -m 2 -o nul "%URL%api/v1/auth/check"
if not errorlevel 1 goto backend_up
set /a tries+=1
if %tries% lss 15 goto wait_backend
echo.
echo  [X] Backend did not answer within 30 seconds. See the tail of this log:
echo      %ROOT%backend_restart.log
echo      Common causes: port taken by another program / deps missing, run
echo      install.bat again / antivirus blocking it
echo.
pause
exit /b 1

:backend_up
echo  [OK] Backend is ready
start "" "%URL%"
echo.
echo  LanternLogic Agent is running at %URL%  -  the browser opens automatically
echo.
echo  First time? In the UI open Settings - Model, pick a provider, paste your API Key.
echo  The Key is written to an environment variable only, never into the config file.
echo.
echo  You can minimize this window. To stop the backend, end python.exe in Task Manager,
echo  or run restart-backend.ps1 again - it carries the saved Key.
echo.
pause
endlocal
