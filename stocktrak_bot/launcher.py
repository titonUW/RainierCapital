#!/usr/bin/env python3
"""
StockTrak Bot Launcher

One-click launcher that:
1. Checks/installs dependencies
2. Installs Playwright browser
3. Launches the GUI application
"""

import subprocess
import sys
import os
import platform

# Get script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

def print_banner():
    """Print startup banner."""
    print("=" * 60)
    print("  StockTrak Trading Bot - Team 9")
    print("  Morgan Stanley UWT Milgard Competition 2026")
    print("=" * 60)
    print()

def check_python_version():
    """Ensure Python 3.8+."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"ERROR: Python 3.8+ required, you have {version.major}.{version.minor}")
        sys.exit(1)
    print(f"[OK] Python {version.major}.{version.minor}.{version.micro}")

def install_dependencies():
    """Install required packages."""
    print("\nChecking dependencies...")

    requirements = [
        'playwright',
        'yfinance',
        'pandas',
        'schedule',
        'pytz',
        'requests',
    ]

    missing = []
    for pkg in requirements:
        try:
            __import__(pkg.replace('-', '_'))
            print(f"  [OK] {pkg}")
        except ImportError:
            missing.append(pkg)
            print(f"  [MISSING] {pkg}")

    if missing:
        print(f"\nInstalling missing packages: {', '.join(missing)}")
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install', '--quiet'
        ] + missing)
        print("[OK] Dependencies installed")
    else:
        print("[OK] All dependencies installed")

def install_playwright_browser():
    """Install Playwright Chromium browser."""
    print("\nChecking Playwright browser...")

    try:
        from playwright.sync_api import sync_playwright

        # Try to launch browser to check if installed
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(headless=True)
            browser.close()
            print("[OK] Playwright Chromium browser ready")
        except Exception:
            print("Installing Playwright Chromium browser...")
            subprocess.check_call([sys.executable, '-m', 'playwright', 'install', 'chromium'])
            print("[OK] Playwright Chromium installed")
        finally:
            pw.stop()

    except Exception as e:
        print(f"Installing Playwright browser: {e}")
        subprocess.check_call([sys.executable, '-m', 'playwright', 'install', 'chromium'])

def create_logs_directory():
    """Ensure logs directory exists."""
    logs_dir = os.path.join(SCRIPT_DIR, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    print(f"[OK] Logs directory: {logs_dir}")

def launch_gui():
    """Launch the GUI application."""
    print("\n" + "=" * 60)
    print("  Launching StockTrak Bot GUI...")
    print("=" * 60 + "\n")

    # Import and run GUI
    from gui import main
    main()

def main():
    """Main launcher entry point."""
    print_banner()

    try:
        check_python_version()
        install_dependencies()
        install_playwright_browser()
        create_logs_directory()
        launch_gui()

    except KeyboardInterrupt:
        print("\nLauncher cancelled by user.")
        sys.exit(0)

    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nTroubleshooting:")
        print("1. Ensure you have Python 3.8+ installed")
        print("2. Try running: pip install -r requirements.txt")
        print("3. Try running: playwright install chromium")
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    main()
