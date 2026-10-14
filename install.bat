@echo off
title LanternLogic Agent - Yi Jian An Zhuang
setlocal EnableExtensions
REM ---- 2026-10-11: put the system dirs FIRST ------------------------------
REM Some machines have no System32 in PATH at all (user's box: machine-level
REM PATH starts with a self-referencing %Path% and lists no System32) -> a
REM fresh cmd cannot find chcp / curl / timeout. This script barely needs
REM them, but preflight and the steps after it do.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%"
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
REM ---- 2026-10-11 (stage 1): the release zip ships runtime\\python.exe ------
REM The published package carries a portable CPython (official embeddable
REM build) with the backend deps already installed. If it is there we use it
REM and the user needs NOTHING installed - not even Python. Source checkouts
REM have no runtime/ folder and keep the original behaviour below.
set "PYEXE="
set "SHIPPED="
if exist "%~dp0runtime\python.exe" set "PYEXE=%~dp0runtime\python.exe"
if defined PYEXE set "SHIPPED=1"
if not defined PYEXE if defined AGENT_SHELL_PYTHON set "PYEXE=%AGENT_SHELL_PYTHON%"
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
if defined SHIPPED echo   [OK] using the Python shipped inside this package - nothing to install
if not defined SHIPPED echo   [OK] using %PYEXE%
if errorlevel 1 (
  echo   [X] Python wei zhao dao. Qing xian an zhuang Python 3.11+:
  echo       https://www.python.org/downloads/  - Gou Xuan Add to PATH
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   [OK] Python %%v

echo [2/5] Backend dependencies ...
REM ---- 2026-10-11 (stage 1): shipped runtime path --------------------------
REM The package's runtime/ already has the deps installed. Probe first: if they
REM import, there is nothing to do - no venv, no network, no pip run at all.
REM If they are missing (someone deleted them), reinstall OFFLINE from the
REM wheels/ folder that ships alongside. Source checkouts keep the old path.
if defined SHIPPED goto :deps_shipped
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
goto :deps_done

:deps_shipped
"%PYEXE%" -c "import fastapi, uvicorn, httpx" >nul 2>&1
if not errorlevel 1 (
  echo   [OK] backend deps already bundled with this package - nothing to install
  goto :deps_done
)
echo   deps missing - reinstalling from the wheels shipped in this package, offline ...
"%PYEXE%" -m pip install --no-index --find-links "%~dp0wheels" -r "%~dp0backend/requirements.txt" --no-warn-script-location
if errorlevel 1 (
  echo   [X] bundled deps install failed. Try by hand:
  echo       runtime\python.exe -m pip install -r backend\requirements.txt
  pause
  exit /b 1
)
echo   [OK] backend deps

:deps_done

echo [3/5] Install frontend deps ...
REM ---- 2026-10-11: release packages already carry a built UI --------------
REM The published zip ships frontend\dist (built). Running npm ci + npm run
REM build here would make Node.js 18+ a hard requirement for people who only
REM want to RUN the app - and Node is NOT needed at runtime (start.bat serves
REM frontend\dist directly). Source checkouts have no dist/ (gitignored), so
REM their behaviour is unchanged: they still get deps installed and UI built.
REM NOTE: plain goto instead of an if-block on purpose - parentheses in echo
REM text inside an if-block silently truncate the block in cmd.
if exist "%~dp0frontend\dist\index.html" (
  echo   [OK] UI shipped with this package - npm ci skipped, Node.js not required
  goto :ui_ready
)
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
:ui_ready

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
if exist "%~dp0frontend\dist\index.html" (
  echo   [OK] UI shipped with this package - npm run build skipped
  echo        Node.js not required. To rebuild by hand: delete frontend\dist
  echo        then run this installer again.
  goto :ui_done
)
cd /d "%~dp0frontend"
call npm run build
cd /d "%~dp0"
if not exist "%~dp0frontend\dist\index.html" (
  echo   [X] UI build failed - run it by hand:  cd frontend  then  npm run build
  pause
  exit /b 1
)
echo   [OK] UI built
:ui_done

REM ============================================================================
REM [4c/5] Renderer deps - the beat-synced video skill needs playwright-core
REM         to render frames to MP4.
REM
REM FIX 2026-10-14 (a real shipping bug caught by the release acceptance run):
REM   this step did NOT exist before.
REM   - The published zip carries these deps (export_public.py FORCE_INCLUDE_DIRS)
REM   - But a SOURCE CHECKOUT has neither dist/ nor node_modules/ (both
REM     gitignored) => cloning the repo, running install.bat and then making a
REM     beat-synced video failed with:
REM       ERR_MODULE_NOT_FOUND: Cannot find package 'playwright-core'
REM     i.e. the same bug as the zip had, on the source path.
REM   - Install once here; skipped when the deps are already present.
REM ============================================================================
echo [4c/5] Install the video renderer's deps (playwright-core) ...
REM NOTE: this .bat must stay pure ASCII, so we never hardcode the skill folder
REM name (it is Chinese). We cd into skills\ and let `for /d` hand us each skill
REM folder by name - that works with non-ASCII names, unlike wildcards mid-path.
set "RENDER_FOUND="
pushd "%~dp0backend\skills" 2>nul
if errorlevel 1 goto :render_scan_done
for /d %%D in (*) do (
  if exist "%%D\tools\render\package.json" set "RENDER_FOUND=%CD%\%%D\tools\render"
)
popd
:render_scan_done
if not defined RENDER_FOUND (
  echo   [!] video renderer not found - skipping
  goto :render_done
)
if exist "%RENDER_FOUND%\node_modules\playwright-core\package.json" (
  echo   [OK] renderer deps already present - skipped
  goto :render_done
)
pushd "%RENDER_FOUND%"
call npm install --no-audit --no-fund
popd
if not exist "%RENDER_FOUND%\node_modules\playwright-core\package.json" (
  echo   [X] renderer deps failed - the video skill cannot render without them.
  echo       Fix by hand: go to backend\skills\*\tools\render and run: npm install
  echo       ^(Node.js 18+ required; the published zip already ships these deps^)
  pause
  exit /b 1
)
echo   [OK] renderer deps installed - the video skill can render
:render_done

echo [5/5] Install TTS (optional) ...
"%~dp0backend\.venv\Scripts\python.exe" -m pip install edge-tts pyttsx3 -i "%PIP_INDEX_URL%" >nul 2>&1
echo   [OK] TTS ready - MeloTTS upgrade script: see the scripts folder

echo.
echo  ============================================================
echo    An Zhuang Wan Cheng! Shuang Ji start.bat Qi Dong
echo    Shou Ci Qi Dong: Ce Bian Lan Settings - Xuan Mo Xing - Tian Key
echo  ============================================================
pause

