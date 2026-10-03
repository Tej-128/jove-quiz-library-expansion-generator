@echo off
setlocal
cd /d "%~dp0"

set "JOVE_LOCAL_MODE=1"
set "VENV_DIR=%USERPROFILE%\joveq_venv"

echo ============================================================
echo JoVE Quiz Library Expansion Generator - Local Production
echo ============================================================
echo.
echo Repository:
echo %CD%
echo.
echo Python environment:
echo %VENV_DIR%
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "BASEPY=py -3"
) else (
    set "BASEPY=python"
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating short-path Python environment...
    %BASEPY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create Python environment at:
        echo %VENV_DIR%
        pause
        exit /b 1
    )
)

echo Updating pip...
"%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 (
    echo.
    echo ERROR: Could not update pip.
    pause
    exit /b 1
)

echo Installing or updating required packages...
"%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt
if errorlevel 1 (
    echo.
    echo First install attempt failed. Rebuilding the short-path environment once...
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"

    %BASEPY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo ERROR: Could not rebuild Python environment.
        pause
        exit /b 1
    )

    "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
    if errorlevel 1 (
        echo.
        echo ERROR: Could not update pip after rebuilding environment.
        pause
        exit /b 1
    )

    "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Dependency installation failed even after rebuilding the short-path environment.
        echo Please send the full error shown above.
        pause
        exit /b 1
    )
)

echo.
echo Starting JoVE local production app...
echo Keep this command window open while using the Streamlit UI.
echo Completed lesson files are written directly to your chosen output folder.
echo.
"%VENV_DIR%\Scripts\python.exe" -m streamlit run local_app.py --server.address 127.0.0.1 --server.port 8501
