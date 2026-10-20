@echo off
setlocal
cd /d "%~dp0"
docker version >nul 2>&1
if errorlevel 1 (
  echo Open Docker Desktop and wait until it is running, then try again.
  pause
  exit /b 1
)
docker compose -f compose.paper.yml up -d --build
if errorlevel 1 (
  echo The paper worker did not start. Keep it stopped and check the error above.
  pause
  exit /b 1
)
docker compose -f compose.paper.yml exec paper python workbench.py menu
echo Closing this menu does not stop the background paper worker. Pause it in the menu if needed.
pause
