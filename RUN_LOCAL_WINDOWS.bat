@echo off
setlocal
cd /d "%~dp0"

set "JOVE_LOCAL_MODE=1"

echo ============================================================
echo JoVE Quiz Library Expansion Generator - Local Production
echo ============================================================
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "BASEPY=py -3"
) else (
    set "BASEPY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python environment...
    %BASEPY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create Python environment.
        pause
        exit /b 1
    )
)

echo Installing or updating required packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Starting JoVE local production app...
echo Keep this command window open while using the Streamlit UI.
echo Completed lesson files are written directly to your chosen output folder.
echo.
".venv\Scripts\python.exe" -m streamlit run local_app.py --server.address 127.0.0.1 --server.port 8501
