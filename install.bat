@echo off
title LanternLogic Agent - Yi Jian An Zhuang
setlocal EnableExtensions
REM K5: pip index configurable default Tsinghua mirror for CN speed;
REM switch to official: set PIP_INDEX_URL=https://pypi.org/simple
if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

echo.
echo  ============================================================
echo    LanternLogic Agent Yi Jian An Zhuang
echo    Ben Di Zhi Neng Ti / 19 Gong Ju / Ren Yi Mo Xing / Shu Ju Bu Chu Ben Ji
echo  ============================================================
echo.

echo [1/5] Check Python ...
REM ---- pick a *complete* interpreter 2026-10-04 clean-machine drill ----
REM A bare `python` can be a broken leftover python.exe + DLLs but no Lib\\:
REM it prints --version fine, then `python -m venv` dies with
REM "Failed to import encodings module" -- cryptic for a normal user.
REM Prefer AGENT_SHELL_PYTHON, then `py -3`, then `python`; require encodings.
set "PYEXE="
if defined AGENT_SHELL_PYTHON set "PYEXE=%AGENT_SHELL_PYTHON%"
if not defined PYEXE py -3 -c "import encodings" >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE python -c "import encodings" >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE (
  echo   [X] PATH li de python bu shi yi ge wan zheng de Python ^(zhuang huan jing hui shi bai^).
  echo       Qing zhuang yi ge wan zheng de Python 3.11+ ^(gou Add to PATH^):
  echo         https://www.python.org/downloads/
  echo       Huo zhe zhi ding yi ge:  set AGENT_SHELL_PYTHON=C:\path\to\python.exe
  pause
  exit /b 1
)
echo   [OK] using %PYEXE%
if errorlevel 1 (
  echo   [X] Python wei zhao dao. Qing xian an zhuang Python 3.11+:
  echo       https://www.python.org/downloads/  - Gou Xuan Add to PATH
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   [OK] Python %%v

echo [2/5] Create backend venv + install deps - Tsinghua mirror ...
if not exist "%~dp0backend\.venv\Scripts\python.exe" (
  echo   creating backend\.venv ...
  %PYEXE% -m venv "%~dp0backend\.venv"
  if errorlevel 1 (
    echo   [X] venv creation failed - Python 3.11+ required
    pause
    exit /b 1
  )
)
"%~dp0backend\.venv\Scripts\python.exe" -m pip install -r "%~dp0backend/requirements.txt" -i "%PIP_INDEX_URL%"
if errorlevel 1 (
  echo   [X] deps install failed - check network and retry
  pause
  exit /b 1
)
echo   [OK] backend deps

echo [3/5] Install frontend deps ...
cd /d "%~dp0frontend"
if not exist node_modules (
  REM K5: npm ci - strictly per package-lock.json, whose integrity hashes guard
  REM against registry poisoning/drift. Registry overridable via NPM_CONFIG_REGISTRY
  call npm ci
  REM Judge by RESULT, not by errorlevel: in the 2026-10-04 clean-machine drill
  REM npm ci really did install 201 packages, yet the errorlevel check called that
  REM a failure, the script exited, and config.json was never generated / the UI
  REM was never built. Look for the key package instead.
  REM NOTE: keep parentheses out of this block. A "" inside a REM line still
  REM closes the enclosing "if ..." in batch and silently truncates the block -
  REM that is exactly how the first version of this check broke the installer.
  if not exist "node_modules\vite\package.json" (
    echo   [X] npm ci failed - Node.js 18+ required or lockfile broken
    pause
    exit /b 1
  )
)
echo   [OK] frontend deps
cd /d "%~dp0"

echo [4/5] Generate config ...
REM NOTE: parentheses inside an echo that sits INSIDE an if-block close the block.
REM The first version printed "(default mock brain...)" here and cmd truncated the
REM block: BOTH branches echoed and the copy below never ran, so a fresh install
REM ended up with no config.json while the log claimed it was created.
if not exist config.json (
  copy "%~dp0contracts\config.example.json" "%~dp0config.json" >nul
  echo   [OK] config.json created - default mock brain, switch it in Settings
) else (
  echo   [OK] config.json kept
)
if not exist "%~dp0frontend\.env" (
  echo VITE_API_MODE=http> "%~dp0frontend\.env"
  echo   [OK] frontend/.env created - UI talks to the real backend
) else (
  echo   [OK] frontend/.env kept
)

echo [4b/5] Build the UI - the backend serves frontend\dist by itself ...
cd /d "%~dp0frontend"
call npm run build
cd /d "%~dp0"
if not exist "%~dp0frontend\dist\index.html" (
  echo   [X] UI build failed - run it by hand:  cd frontend  then  npm run build
  pause
  exit /b 1
)
echo   [OK] UI built

echo [5/5] Install TTS (optional) ...
"%~dp0backend\.venv\Scripts\python.exe" -m pip install edge-tts pyttsx3 -i "%PIP_INDEX_URL%" >nul 2>&1
echo   [OK] TTS ready - MeloTTS upgrade script: see the scripts folder

echo.
echo  ============================================================
echo    An Zhuang Wan Cheng! Shuang Ji start.bat Qi Dong
echo    Shou Ci Qi Dong: Ce Bian Lan Settings - Xuan Mo Xing - Tian Key
echo  ============================================================
pause

