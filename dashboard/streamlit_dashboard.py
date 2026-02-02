"""
StockTrak Bot Dashboard - Streamlit Version

Simple, Python-only dashboard for non-technical teammates.
Run with: streamlit run dashboard/streamlit_dashboard.py

Features:
- Real-time bot status
- Big safe buttons for controls
- Live log viewer
- Screenshot viewer on errors
- Trade history for finals evidence
"""

import streamlit as st
import subprocess
import json
import os
import time
from pathlib import Path
from datetime import datetime
import threading

# ============================================================================
# Configuration
# ============================================================================
ROOT = Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / "stocktrak_bot"
STATE_FILE = BOT_DIR / "state" / "dashboard_state.json"
LOG_FILE = BOT_DIR / "logs" / "stocktrak_bot.log"
SCREENSHOTS_DIR = BOT_DIR / "logs"

# Ensure directories exist
(BOT_DIR / "state").mkdir(exist_ok=True)
(BOT_DIR / "logs").mkdir(exist_ok=True)

# ============================================================================
# Page Configuration
# ============================================================================
st.set_page_config(
    page_title="StockTrak Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for big buttons and status indicators
st.markdown("""
<style>
    .big-button {
        font-size: 20px !important;
        padding: 20px 40px !important;
    }
    .status-running {
        background-color: #28a745;
        color: white;
        padding: 10px 20px;
        border-radius: 5px;
        font-weight: bold;
    }
    .status-stopped {
        background-color: #6c757d;
        color: white;
        padding: 10px 20px;
        border-radius: 5px;
        font-weight: bold;
    }
    .status-error {
        background-color: #dc3545;
        color: white;
        padding: 10px 20px;
        border-radius: 5px;
        font-weight: bold;
    }
    .metric-card {
        background-color: #f8f9fa;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# Helper Functions
# ============================================================================
def load_state() -> dict:
    """Load dashboard state from file"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {
        "running": False,
        "mode": "IDLE",
        "step": None,
        "trades_used": 0,
        "trades_remaining": 80,
        "regime": "UNKNOWN",
        "vix": None,
        "positions_count": 0,
        "positions": [],
        "recent_trades": [],
        "last_screenshot": None,
        "error": None,
    }


def load_log_tail(lines: int = 100) -> str:
    """Load last N lines of log file"""
    if LOG_FILE.exists():
        try:
            content = LOG_FILE.read_text(errors='ignore')
            log_lines = content.split('\n')
            return '\n'.join(log_lines[-lines:])
        except:
            pass
    return "No logs yet..."


def get_screenshots() -> list:
    """Get list of recent screenshots"""
    if SCREENSHOTS_DIR.exists():
        pngs = sorted(
            SCREENSHOTS_DIR.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return pngs[:20]
    return []


def run_bot_command(args: list) -> tuple:
    """Run bot command and return (success, output)"""
    try:
        result = subprocess.run(
            ["python", "main.py"] + args,
            cwd=str(BOT_DIR),
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 5 minutes"
    except Exception as e:
        return False, str(e)


def run_bot_async(args: list, status_placeholder):
    """Run bot in background thread with status updates"""
    def run():
        try:
            process = subprocess.Popen(
                ["python", "main.py"] + args,
                cwd=str(BOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            output = []
            for line in iter(process.stdout.readline, ''):
                output.append(line)
                # Update would happen here but Streamlit doesn't support this well

            process.wait()
            return process.returncode == 0
        except Exception as e:
            return False

    return run()


# ============================================================================
# Sidebar - Controls
# ============================================================================
with st.sidebar:
    st.title("🎛️ Controls")
    st.markdown("---")

    # Armed mode toggle (safety switch)
    armed = st.toggle("🔓 ARM Live Trading", value=False,
                      help="Enable this to allow live trading commands")

    if armed:
        st.warning("⚠️ ARMED - Live trading enabled!")

    st.markdown("---")

    # Safe buttons (always available)
    st.subheader("🛡️ Safe Operations")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔑 Test Login", use_container_width=True):
            with st.spinner("Testing login..."):
                success, output = run_bot_command(["--test"])
                if success:
                    st.success("Login successful!")
                else:
                    st.error("Login failed!")
                st.code(output[-2000:] if len(output) > 2000 else output)

    with col2:
        if st.button("💰 Check Capital", use_container_width=True):
            with st.spinner("Reading capital..."):
                success, output = run_bot_command(["--capital-test"])
                if success:
                    st.success("Capital read!")
                else:
                    st.error("Failed!")
                st.code(output[-2000:] if len(output) > 2000 else output)

    if st.button("🧪 Dry Run (No Trades)", use_container_width=True):
        with st.spinner("Running dry run..."):
            success, output = run_bot_command(["--dry-run"])
            if success:
                st.success("Dry run complete!")
            else:
                st.error("Dry run failed!")
            st.code(output[-2000:] if len(output) > 2000 else output)

    st.markdown("---")

    # Live trading buttons (require armed)
    st.subheader("🚀 Live Operations")

    if st.button("▶️ Manual Run (LIVE)", use_container_width=True,
                 disabled=not armed, type="primary" if armed else "secondary"):
        if armed:
            confirm = st.text_input("Type 'EXECUTE' to confirm:")
            if confirm == "EXECUTE":
                with st.spinner("Executing live trades..."):
                    success, output = run_bot_command(["--manual"])
                    if success:
                        st.success("Execution complete!")
                    else:
                        st.error("Execution failed!")
                    st.code(output[-3000:] if len(output) > 3000 else output)

    if st.button("🏗️ Day-1 Build (LIVE)", use_container_width=True,
                 disabled=not armed, type="primary" if armed else "secondary"):
        if armed:
            st.warning("This will execute the full Day-1 portfolio build!")
            confirm = st.text_input("Type 'BUILD DAY1' to confirm:", key="day1_confirm")
            if confirm == "BUILD DAY1":
                with st.spinner("Building Day-1 portfolio..."):
                    success, output = run_bot_command(["--day1"])
                    if success:
                        st.success("Day-1 build complete!")
                    else:
                        st.error("Day-1 build failed!")
                    st.code(output[-3000:] if len(output) > 3000 else output)

    st.markdown("---")
    st.caption("Dashboard auto-refreshes every 5 seconds")


# ============================================================================
# Main Content
# ============================================================================
state = load_state()

# Header with status
st.title("📈 StockTrak Bot Dashboard")

# Status row
col1, col2, col3, col4 = st.columns(4)

with col1:
    status = "🟢 RUNNING" if state.get("running") else "⚪ STOPPED"
    status_color = "green" if state.get("running") else "gray"
    st.metric("Bot Status", status)

with col2:
    mode = state.get("mode", "IDLE")
    st.metric("Mode", mode)

with col3:
    trades_used = state.get("trades_used", 0)
    trades_remaining = state.get("trades_remaining", 80)
    st.metric("Trades Used", f"{trades_used}/80", delta=f"{trades_remaining} remaining")

with col4:
    regime = state.get("regime", "UNKNOWN")
    vix = state.get("vix")
    vix_str = f" (VIX: {vix:.1f})" if vix else ""
    st.metric("Regime", f"{regime}{vix_str}")

# Error banner
if state.get("error"):
    st.error(f"❌ Last Error: {state.get('error')}")

# Current step
if state.get("step"):
    st.info(f"📍 Current Step: {state.get('step')}")

st.markdown("---")

# Main content tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Positions", "📜 Logs", "📷 Screenshots", "📋 Trade History"])

with tab1:
    st.subheader("Current Positions")

    positions = state.get("positions", [])
    if positions:
        # Create a nice table
        import pandas as pd
        df = pd.DataFrame(positions)
        if not df.empty:
            st.dataframe(df, use_container_width=True)
    else:
        st.info("No positions currently held")

    # Last update time
    last_update = state.get("last_update")
    if last_update:
        st.caption(f"Last updated: {last_update}")

with tab2:
    st.subheader("Live Logs")

    log_lines = st.slider("Lines to show", 50, 500, 100)
    logs = load_log_tail(log_lines)

    # Auto-refresh checkbox
    auto_refresh = st.checkbox("Auto-refresh logs", value=True)

    st.code(logs, language="text")

    if auto_refresh:
        time.sleep(0.1)  # Small delay
        st.rerun()

with tab3:
    st.subheader("Recent Screenshots")

    screenshots = get_screenshots()
    if screenshots:
        # Show latest screenshot prominently
        latest = screenshots[0]
        st.image(str(latest), caption=f"Latest: {latest.name}")

        # Show thumbnails of others
        if len(screenshots) > 1:
            st.markdown("### Previous Screenshots")
            cols = st.columns(4)
            for i, ss in enumerate(screenshots[1:9]):
                with cols[i % 4]:
                    st.image(str(ss), caption=ss.name, width=200)
    else:
        st.info("No screenshots yet")

with tab4:
    st.subheader("Trade History (Evidence for Finals)")

    trades = state.get("recent_trades", [])
    if trades:
        import pandas as pd
        df = pd.DataFrame(trades)

        # Reorder columns
        cols_order = ['timestamp', 'action', 'ticker', 'shares', 'price', 'reason', 'trade_number']
        cols_present = [c for c in cols_order if c in df.columns]
        df = df[cols_present]

        st.dataframe(df, use_container_width=True)

        # Export button
        csv = df.to_csv(index=False)
        st.download_button(
            "📥 Export to CSV",
            csv,
            "trade_history.csv",
            "text/csv",
            use_container_width=True
        )
    else:
        st.info("No trades recorded yet")

# Footer
st.markdown("---")
st.caption(f"StockTrak Bot Dashboard | Last state update: {state.get('last_update', 'Never')}")

# Auto-refresh every 5 seconds (only if not in logs tab with auto-refresh)
if 'auto_refresh' not in st.session_state:
    st.session_state.auto_refresh = True

# Add refresh button
if st.button("🔄 Refresh Now"):
    st.rerun()
