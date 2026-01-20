#!/bin/bash
# ============================================================
# StockTrak Trading Bot - Installer (Mac/Linux)
# Team 9 - Morgan Stanley Competition 2026
# ============================================================

echo ""
echo "============================================================"
echo "  StockTrak Trading Bot - INSTALLER"
echo "  Team 9 - Morgan Stanley Competition 2026"
echo "============================================================"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/stocktrak_bot"

# Check Python
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    echo ""
    echo "ERROR: Python 3 is not installed!"
    echo ""
    echo "Please install Python 3.8+:"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  brew install python3"
    else
        echo "  sudo apt update && sudo apt install python3 python3-venv python3-pip"
    fi
    echo ""
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "[OK] $PYTHON_VERSION"
echo ""

# Create virtual environment
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo "Removing existing virtual environment..."
    rm -rf venv
fi

python3 -m venv venv
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to create virtual environment"
    echo "Try: sudo apt install python3-venv"
    exit 1
fi
echo "[OK] Virtual environment created"
echo ""

# Activate
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip --quiet
echo "[OK] pip upgraded"
echo ""

# Install requirements
echo "Installing dependencies..."
pip install -r requirements.txt --quiet
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to install dependencies"
    exit 1
fi
echo "[OK] Dependencies installed"
echo ""

# Install Playwright browser
echo "Installing Playwright Chromium browser..."
echo "(This may take a few minutes on first run)"
playwright install chromium
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to install Playwright browser"
    echo "Try: playwright install-deps chromium"
    exit 1
fi
echo "[OK] Playwright browser installed"
echo ""

# Create logs directory
mkdir -p logs
echo "[OK] Logs directory created"
echo ""

# Make scripts executable
chmod +x "$SCRIPT_DIR/START_BOT.sh"
chmod +x launcher.py
echo "[OK] Scripts made executable"
echo ""

# Create desktop shortcut (Linux only)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    read -p "Create desktop shortcut? (y/n): " CREATE_SHORTCUT
    if [[ "$CREATE_SHORTCUT" == "y" || "$CREATE_SHORTCUT" == "Y" ]]; then
        DESKTOP_FILE="$HOME/Desktop/StockTrak-Bot.desktop"
        cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=StockTrak Bot
Comment=StockTrak Trading Bot - Team 9
Exec=$SCRIPT_DIR/START_BOT.sh
Icon=utilities-terminal
Terminal=true
Categories=Finance;
EOF
        chmod +x "$DESKTOP_FILE"
        echo "[OK] Desktop shortcut created"
    fi
fi

# Deactivate
deactivate

echo ""
echo "============================================================"
echo "  INSTALLATION COMPLETE!"
echo "============================================================"
echo ""
echo "To start the bot:"
echo "  ./START_BOT.sh"
echo ""
echo "First time setup:"
echo "  1. Click LOGIN to connect to StockTrak"
echo "  2. Click VERIFY LOGIN to confirm access"
echo "  3. Click TEST TRADE to validate trading"
echo "  4. Click START to begin automated trading"
echo ""
