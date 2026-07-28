@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   FoodPath -- NC FNS Eligibility Guide Setup
echo ============================================
echo.

REM FOODPATH_SETUP_NONINTERACTIVE=1 skips every prompt/pause and the final
REM server launch. Used by CI (.github/workflows/windows-setup.yml) so this
REM exact file is exercised end to end on real Windows, not a re-implementation
REM of its logic -- set it yourself only if you're scripting this file too.

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python was not found on this machine.
        echo Install Python 3.11 or newer from https://www.python.org/downloads/
        echo IMPORTANT: check "Add python.exe to PATH" during install, then
        echo double-click this file again.
        if not defined FOODPATH_SETUP_NONINTERACTIVE pause
        exit /b 1
    )
    set "PY=py"
) else (
    set "PY=python"
)

if not exist ".venv\Scripts\activate.bat" (
    echo Creating a virtual environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        if not defined FOODPATH_SETUP_NONINTERACTIVE pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: failed to activate the virtual environment.
    if not defined FOODPATH_SETUP_NONINTERACTIVE pause
    exit /b 1
)

echo Installing dependencies -- this can take a minute the first time...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency install failed. See the output above and try again.
    if not defined FOODPATH_SETUP_NONINTERACTIVE pause
    exit /b 1
)

if defined FOODPATH_SETUP_NONINTERACTIVE (
    python scripts\setup_env.py --check-only
) else (
    python scripts\setup_env.py
)
if errorlevel 1 (
    echo ERROR: .env setup did not complete.
    if not defined FOODPATH_SETUP_NONINTERACTIVE pause
    exit /b 1
)

echo.
echo Running the test suite to confirm everything works...
python -m pytest -q
if errorlevel 1 (
    echo.
    echo WARNING: some tests failed above. This can happen depending on
    echo which LLM provider you configured -- the app may still run fine.
    if defined FOODPATH_SETUP_NONINTERACTIVE (
        exit /b 1
    )
    echo Close this window now if you'd rather stop and look into it first.
    pause
)

if defined FOODPATH_SETUP_NONINTERACTIVE (
    echo Setup verified successfully ^(non-interactive mode^) -- skipping server launch.
    exit /b 0
)

echo.
echo Starting FoodPath at http://127.0.0.1:8000 ...
echo Press Ctrl+C in this window to stop the server when you're done.
echo.
start "" "http://127.0.0.1:8000"
python -m uvicorn src.web:app --host 127.0.0.1 --port 8000

pause
