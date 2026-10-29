@echo off
rem 유튜브 댓글 에이전트 원클릭 실행기 - 더블클릭하면 준비부터 대시보드까지 연다. 명세: docs/systems/launcher.md
rem 글자판을 UTF-8로 먼저 바꾼다. 그래야 아래 한글 문구가 깨지지 않는다.
chcp 65001 > nul
title 유튜브 댓글 에이전트 - 이 창을 닫으면 종료돼요
cd /d "%~dp0"

rem 1. 크롬이 있는지 먼저 본다 - 부품 설치를 기다린 뒤에야 알게 되지 않게.
set "HAS_CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "HAS_CHROME=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "HAS_CHROME=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "HAS_CHROME=1"
if not defined HAS_CHROME goto no_chrome

rem 2. uv - 파이썬 실행 도구. 없으면 공식 설치 스크립트로 설치한다.
where uv > nul 2>&1
if not errorlevel 1 goto have_uv
if exist "%USERPROFILE%\.local\bin\uv.exe" goto add_uv_path
echo 처음 실행이라 파이썬 실행 도구 uv를 설치하는 중이에요...
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
:add_uv_path
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv > nul 2>&1
if errorlevel 1 goto no_uv
:have_uv

rem 3. 필요한 부품 설치 - 처음만 오래 걸린다. 파이썬도 uv가 알아서 받는다.
echo 필요한 부품을 준비하는 중이에요. 처음에는 1~2분 걸려요...
uv sync --quiet
if errorlevel 1 goto sync_failed

rem 4. 나머지 - 크롬 확인, .env, 서버, 대시보드 열기 - 는 파이썬 쪽이 한다.
uv run --quiet python -m agent.launcher
if errorlevel 1 goto stopped
exit /b 0

:no_chrome
echo [!] 구글 크롬이 필요해요. https://www.google.com/chrome 에서 설치한 뒤 다시 실행해 주세요.
pause
exit /b 1

:no_uv
echo [!] uv를 설치하지 못했어요. 인터넷 연결을 확인하고 다시 실행해 주세요.
pause
exit /b 1

:sync_failed
echo [!] 부품을 설치하지 못했어요. 위의 메시지를 확인하고 다시 실행해 주세요.
pause
exit /b 1

:stopped
echo.
echo 위의 이유로 멈췄어요. 아무 키나 누르면 창이 닫혀요.
pause
exit /b 1
