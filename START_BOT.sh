#!/bin/bash
# ============================================================
# StockTrak Trading Bot - One-Click Launcher (Mac/Linux)
# Team 9 - Morgan Stanley Competition 2026
# ============================================================

echo ""
echo "============================================================"
echo "  StockTrak Trading Bot - Team 9"
echo "  Morgan Stanley UWT Milgard Competition 2026"
echo "============================================================"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/stocktrak_bot"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed"
    echo ""
    echo "Please install Python 3.8+:"
    echo "  Mac: brew install python3"
    echo "  Linux: sudo apt install python3 python3-venv python3-pip"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PYTHON_VERSION"

# Create virtual environment if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create virtual environment"
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

# Activate virtual environment
source venv/bin/activate

# Run launcher
python launcher.py

# Deactivate when done
deactivate

echo ""
echo "Bot has stopped."
read -p "Press Enter to exit..."
