@echo off
REM ============================================================
REM StockTrak Trading Bot - One-Click Launcher (Windows)
REM Team 9 - Morgan Stanley Competition 2026
REM ============================================================

title StockTrak Trading Bot - Team 9

echo.
echo ============================================================
echo   StockTrak Trading Bot - Team 9
echo   Morgan Stanley UWT Milgard Competition 2026
echo ============================================================
echo.

REM Change to script directory
cd /d "%~dp0stocktrak_bot"

REM Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM Check if virtual environment exists, create if not
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Run launcher
python launcher.py

REM If GUI closes, deactivate and pause
call venv\Scripts\deactivate.bat

echo.
echo Bot has stopped.
pause
