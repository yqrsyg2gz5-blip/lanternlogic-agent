@echo off
title LanternLogic Agent - Zhong Wen Yu Yin Bao
echo ============================================================
echo   LanternLogic Agent Zhong Wen Yu Yin Sheng Ji (Gong Ying Lian Jia Gu Ban)
echo   MeloTTS [MIT, ke Shang Yong, ~100MB]
echo   Ding Si Ban Ben + SHA256 Jiao Yan, Jiao Yan Bu Guo Ju Jue An Zhuang
echo ============================================================
echo.

REM K5: all real logic lives in install_melotts.py - pinned commit + hash, two gates.
REM Bu Zai Jing Di San Fang Dai Li La Dai Ma; Jing Xiang Ke Yong AGENT_SHELL_TTS_MIRROR Pei Zhi (Nei Rong Xun Zhi, Ha Xi Bu Bian).
set "PY=python"
if exist "%~dp0..\backend\.venv\Scripts\python.exe" set "PY=%~dp0..\backend\.venv\Scripts\python.exe"

"%PY%" "%~dp0install_melotts.py" --python "%PY%"
if errorlevel 1 (
  echo.
  echo [X] An Zhuang Bei Ju Jue - Qing Kan Shang Mian Ju Ti Yuan Yin
  echo     (Ha Xi Bu Fu = Yuan Ke Neng Bei Tou Du, Qie Wu Tiao Guo Jiao Yan)
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   Wan Cheng! Chong Qi LanternLogic Agent Hou, TTS backend=melotts
echo   [GPT-SoVITS sheng yin ke long: qing zi xing an zhuang, MIT]
echo ============================================================
pause
