@echo off
rem ClassNest - double-click to install everything and start the app.
rem Options are passed through to run.py, e.g.   start.bat --reload
setlocal
cd /d "%~dp0"
title ClassNest

set "PY="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY call :install_python
if not defined PY (
  echo [ClassNest] Python could not be installed automatically.
  echo [ClassNest] Install Python 3.12+ from https://www.python.org/downloads/ then run start.bat again.
  pause
  exit /b 1
)

%PY% run.py --open %*
set "CODE=%errorlevel%"
if not "%CODE%"=="0" pause
exit /b %CODE%

:install_python
echo [ClassNest] Python was not found - installing Python 3.12 with winget...
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%ProgramFiles%\Python312\python.exe") do (
  if exist %%P set PY=%%P
)
exit /b 0
