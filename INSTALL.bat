@echo off
REM ============================================================
REM StockTrak Trading Bot - Installer (Windows)
REM Team 9 - Morgan Stanley Competition 2026
REM ============================================================

title StockTrak Bot Installer

echo.
echo ============================================================
echo   StockTrak Trading Bot - INSTALLER
echo   Team 9 - Morgan Stanley Competition 2026
echo ============================================================
echo.

REM Change to script directory
cd /d "%~dp0stocktrak_bot"

REM Check Python
echo Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python is not installed!
    echo.
    echo Please download and install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Check "Add Python to PATH" during installation!
    echo.
    pause
    exit /b 1
)

python --version
echo [OK] Python found
echo.

REM Create virtual environment
echo Creating virtual environment...
if exist "venv" (
    echo Virtual environment already exists, recreating...
    rmdir /s /q venv
)
python -m venv venv
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment created
echo.

REM Activate
call venv\Scripts\activate.bat

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [OK] pip upgraded
echo.

REM Install requirements
echo Installing dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM Install Playwright browser
echo Installing Playwright Chromium browser...
echo (This may take a few minutes on first run)
playwright install chromium
if %errorlevel% neq 0 (
    echo ERROR: Failed to install Playwright browser
    pause
    exit /b 1
)
echo [OK] Playwright browser installed
echo.

REM Create logs directory
if not exist "logs" mkdir logs
echo [OK] Logs directory created
echo.

REM Create desktop shortcut (optional)
echo.
set /p CREATE_SHORTCUT="Create desktop shortcut? (y/n): "
if /i "%CREATE_SHORTCUT%"=="y" (
    echo Creating desktop shortcut...
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\StockTrak Bot.lnk'); $s.TargetPath = '%~dp0START_BOT.bat'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'StockTrak Trading Bot - Team 9'; $s.Save()"
    echo [OK] Desktop shortcut created
)

REM Deactivate
call venv\Scripts\deactivate.bat

echo.
echo ============================================================
echo   INSTALLATION COMPLETE!
echo ============================================================
echo.
echo To start the bot:
echo   1. Double-click START_BOT.bat
echo   2. Or double-click the desktop shortcut
echo.
echo First time setup:
echo   1. Click LOGIN to connect to StockTrak
echo   2. Click VERIFY LOGIN to confirm access
echo   3. Click TEST TRADE to validate trading
echo   4. Click START to begin automated trading
echo.
pause
