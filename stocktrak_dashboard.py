# stocktrak_dashboard.py
# One-file Streamlit UI for a StockTrak bot (Windows-friendly)
# - Shows state (bot_state.json)
# - Tails logs
# - Shows latest screenshots
# - Buttons to run bot commands safely (test/dry/manual/day1 w/ guardrails)
#
# Run:
#   streamlit run stocktrak_dashboard.py
#
# Recommended: install optional auto-refresh helper:
#   pip install streamlit-autorefresh

from __future__ import annotations

import json
import os
import sys
import time
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

import streamlit as st

# Optional: better auto-refresh without blocking
try:
    from streamlit_autorefresh import st_autorefresh  # pip install streamlit-autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False


# ----------------------------
# Helpers: find bot directory
# ----------------------------
def find_bot_dir(start: Path) -> Optional[Path]:
    """
    Walk up from 'start' and try to find a folder named stocktrak_bot containing main.py.
    """
    start = start.resolve()
    for parent in [start] + list(start.parents):
        candidate = parent / "stocktrak_bot"
        if (candidate / "main.py").exists():
            return candidate
    return None


def safe_read_text(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def tail_lines(text: str, n: int = 200) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def list_pngs(folder: Path) -> List[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)


def fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def is_process_running(proc: Optional[subprocess.Popen]) -> bool:
    return proc is not None and proc.poll() is None


def terminate_process(proc: subprocess.Popen) -> None:
    """
    Best-effort terminate. On Windows, terminate() is usually enough.
    """
    try:
        proc.terminate()
    except Exception:
        pass


@dataclass
class RunSpec:
    label: str
    script: str
    args: List[str]
    is_live_trading: bool


def build_run_specs() -> List[RunSpec]:
    """
    Adjust these if your repo uses different scripts/flags.
    """
    return [
        RunSpec("Test Login (safe)", "main.py", ["--test"], False),
        RunSpec("Scores / Rankings (safe)", "main.py", ["--scores"], False),
        RunSpec("Dry Run (safe - no trades)", "main.py", ["--dry-run"], False),
        RunSpec("Manual Run (LIVE trades)", "main.py", ["--manual"], True),
        RunSpec("Day-1 Build (LIVE trades)", "main.py", ["--day1"], True),
        # Optional diagnostic if you have it:
        RunSpec("Capital/KPI Test (safe)", "daily_routine.py", ["--capital-test"], False),
    ]


# ----------------------------
# Streamlit Page Setup
# ----------------------------
st.set_page_config(
    page_title="StockTrak Bot Dashboard",
    layout="wide",
)

st.title("StockTrak Bot Dashboard (Team-Friendly)")

# ----------------------------
# Sidebar: Paths & Refresh
# ----------------------------
HERE = Path(__file__).resolve().parent
AUTO_BOT_DIR = find_bot_dir(HERE)

with st.sidebar:
    st.header("Settings")

    default_bot_dir = str(AUTO_BOT_DIR) if AUTO_BOT_DIR else str(HERE / "stocktrak_bot")
    bot_dir_str = st.text_input("Bot folder path", value=default_bot_dir)
    bot_dir = Path(bot_dir_str).expanduser()

    python_exe_default = sys.executable  # streamlit's python; best if streamlit installed in venv
    python_exe = st.text_input("Python interpreter", value=python_exe_default)

    st.divider()

    # Files/folders
    state_path = bot_dir / "state" / "dashboard_state.json"
    logs_dir = bot_dir / "logs"
    screenshots_dir_guess = bot_dir / "screenshots"
    # many of your screenshots are in logs/ as well
    screenshot_dir_choice = st.selectbox(
        "Screenshot folder",
        options=[str(logs_dir), str(screenshots_dir_guess)],
        index=0,
        help="Choose where your bot saves screenshots (.png). Many bots save to logs/.",
    )
    screenshots_dir = Path(screenshot_dir_choice)

    bot_log_path = logs_dir / "stocktrak_bot.log"
    ui_log_path = logs_dir / "ui_runner.log"

    st.divider()

    st.subheader("Refresh")
    auto_refresh = st.toggle("Auto-refresh dashboard", value=True)
    refresh_ms = st.slider("Refresh interval (ms)", 500, 5000, 1500, step=250)

    if auto_refresh:
        if HAS_AUTOREFRESH:
            st_autorefresh(interval=refresh_ms, key="ui_autorefresh")
        else:
            st.caption("Tip: install smoother auto-refresh: `pip install streamlit-autorefresh`")

    st.divider()
    st.caption("Safety: `Day-1 Build` requires ARM + typed confirmation.")

# ----------------------------
# Session State Init
# ----------------------------
if "proc" not in st.session_state:
    st.session_state.proc = None
if "proc_label" not in st.session_state:
    st.session_state.proc_label = ""
if "proc_started_at" not in st.session_state:
    st.session_state.proc_started_at = None
if "last_exit_code" not in st.session_state:
    st.session_state.last_exit_code = None
if "last_run_msg" not in st.session_state:
    st.session_state.last_run_msg = ""
if "arm_day1" not in st.session_state:
    st.session_state.arm_day1 = False


# ----------------------------
# Validate bot directory
# ----------------------------
missing = []
if not bot_dir.exists():
    missing.append(f"Bot folder not found: {bot_dir}")
if not (bot_dir / "main.py").exists():
    missing.append("main.py not found in bot folder.")
if missing:
    st.error("Bot path issue:\n- " + "\n- ".join(missing))
    st.stop()


# ----------------------------
# Top Status Row
# ----------------------------
colA, colB, colC, colD = st.columns([1.2, 1.2, 1.2, 1.4], gap="large")

# Process status
proc = st.session_state.proc
running = is_process_running(proc)

with colA:
    st.subheader("Bot Process")
    if running:
        st.success(f"RUNNING: {st.session_state.proc_label}")
        started = st.session_state.proc_started_at
        if started:
            st.write(f"Started: `{started}`")
        st.write(f"PID: `{proc.pid}`")
    else:
        st.info("STOPPED")
        if st.session_state.last_exit_code is not None:
            st.write(f"Last exit code: `{st.session_state.last_exit_code}`")

with colB:
    st.subheader("Bot State")
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            # Show a few important fields if present
            st.write(f"Mode: `{state.get('mode','?')}`")
            st.write(f"Last result: `{state.get('last_result','?')}`")
            st.write(f"Step: `{state.get('step','?')}`")
            err = state.get("error")
            if err:
                st.error(f"Error: {err}")
            trades_used = state.get("trades_used")
            if trades_used is not None:
                st.metric("Trades Used", trades_used)
        except Exception as e:
            st.warning("Could not parse dashboard_state.json")
            st.code(str(e))
    else:
        st.warning("No dashboard_state.json found yet.")
        st.caption("The bot writes state/dashboard_state.json each step.")

with colC:
    st.subheader("Trade Budget")
    # If state has trades_used, show trades remaining (hard cap 80)
    trades_used = None
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            trades_used = state.get("trades_used")
        except Exception:
            trades_used = None

    if trades_used is None:
        st.info("Trades Used unknown (state file missing/doesn't include it).")
    else:
        remaining = 80 - int(trades_used)
        st.metric("Trades Remaining", remaining)
        if trades_used >= 70:
            st.warning("Discretionary STOP threshold hit (>=70).")
        if trades_used >= 80:
            st.error("Trade cap reached (80).")

with colD:
    st.subheader("Quick Actions")
    stop_clicked = st.button("STOP current run", type="primary", disabled=not running)
    if stop_clicked and running:
        terminate_process(proc)
        st.session_state.last_run_msg = "Stop requested."
        st.session_state.last_exit_code = None

    st.write("")
    st.caption("Do not click around in the bot-controlled Chromium window while it runs.")


# ----------------------------
# Controls: Run Buttons
# ----------------------------
st.divider()
st.header("Run Controls")

run_specs = build_run_specs()

# Safety UI for Day-1
left, right = st.columns([1.2, 1.8], gap="large")

with left:
    st.subheader("Live Trading Safety")
    st.session_state.arm_day1 = st.checkbox("ARM live-trading actions", value=st.session_state.arm_day1)
    day1_phrase = st.text_input("Type BUILD to enable Day-1 Build", value="", help="Prevents accidental Day-1 order placement.")
    day1_allowed = st.session_state.arm_day1 and (day1_phrase.strip().upper() == "BUILD")

    st.caption("Manual Run is also LIVE. Consider keeping ARM off unless you intend to trade.")

with right:
    st.subheader("Start a Run")
    st.write("Buttons start the bot as a separate subprocess (so the dashboard stays responsive).")

    cols = st.columns(3)
    btn_map = {}

    for i, spec in enumerate(run_specs):
        col = cols[i % 3]
        disabled = running
        # Extra guard for day1
        if "--day1" in spec.args:
            disabled = disabled or not day1_allowed

        btn = col.button(spec.label, disabled=disabled)
        btn_map[spec.label] = btn

    st.caption("If a run fails, check the latest screenshot + log tail below.")


def start_run(spec: RunSpec) -> None:
    """
    Start a subprocess for the bot command.
    Writes combined stdout/stderr to logs/ui_runner.log.
    """
    global proc

    # Prevent running multiple
    if is_process_running(st.session_state.proc):
        st.session_state.last_run_msg = "A run is already in progress."
        return

    script_path = bot_dir / spec.script
    if not script_path.exists():
        st.session_state.last_run_msg = f"Missing script: {script_path}"
        return

    logs_dir.mkdir(parents=True, exist_ok=True)
    ui_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Append run header to UI log
    with open(ui_log_path, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 80 + "\n")
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] START {spec.label}\n")
        f.write(f"CMD: {python_exe} {spec.script} {' '.join(spec.args)}\n")

    # Launch process
    out = open(ui_log_path, "a", encoding="utf-8")

    creationflags = 0
    # On Windows, helps isolate process group
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    p = subprocess.Popen(
        [python_exe, spec.script] + spec.args,
        cwd=str(bot_dir),
        stdout=out,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )

    st.session_state.proc = p
    st.session_state.proc_label = spec.label
    st.session_state.proc_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.last_exit_code = None
    st.session_state.last_run_msg = f"Started: {spec.label} (PID {p.pid})"


# Start run if any button clicked
for spec in run_specs:
    if btn_map.get(spec.label):
        # Guard: prevent accidental live trading if not armed (for manual/day1)
        if spec.is_live_trading and not st.session_state.arm_day1:
            st.warning("Live trading is not ARMED. Enable ARM checkbox first.")
        else:
            start_run(spec)

# Poll process and update exit code if it finished
proc = st.session_state.proc
if proc is not None and proc.poll() is not None:
    st.session_state.last_exit_code = proc.returncode
    st.session_state.last_run_msg = f"Finished: {st.session_state.proc_label} (exit {proc.returncode})"
    st.session_state.proc = None
    st.session_state.proc_label = ""
    st.session_state.proc_started_at = None


# Show last run message
if st.session_state.last_run_msg:
    st.info(st.session_state.last_run_msg)


# ----------------------------
# Screenshots Panel
# ----------------------------
st.divider()
st.header("Screenshots")

pngs = list_pngs(screenshots_dir)
if not pngs:
    st.warning(f"No screenshots found in: {screenshots_dir}")
    st.caption("If your bot saves screenshots somewhere else, change the folder in the sidebar.")
else:
    latest = pngs[0]
    st.write(f"Latest: `{latest.name}` (modified {fmt_dt(latest.stat().st_mtime)})")

    # Selector
    options = [p.name for p in pngs[:30]]
    selected = st.selectbox("Select screenshot", options=options, index=0)
    selected_path = screenshots_dir / selected

    if selected_path.exists():
        st.image(str(selected_path), use_container_width=True)


# ----------------------------
# Logs Panel
# ----------------------------
st.divider()
st.header("Logs (Live Tail)")

log_cols = st.columns([1, 1], gap="large")

with log_cols[0]:
    st.subheader("Bot Log: stocktrak_bot.log")
    if bot_log_path.exists():
        bot_text = safe_read_text(bot_log_path)
        st.text_area(
            "Tail",
            tail_lines(bot_text, n=250),
            height=400,
        )
    else:
        st.warning(f"Missing: {bot_log_path}")
        st.caption("If your bot logs to a different file, update the sidebar paths.")

with log_cols[1]:
    st.subheader("UI Runner Log: ui_runner.log")
    if ui_log_path.exists():
        ui_text = safe_read_text(ui_log_path)
        st.text_area(
            "Tail",
            tail_lines(ui_text, n=250),
            height=400,
        )
    else:
        st.info("No UI runner log yet. Start a run to create it.")


# ----------------------------
# Operator Notes
# ----------------------------
st.divider()
st.header("Operator Rules (non-negotiable)")

st.markdown(
    """
- **Do not click around** in the bot-controlled Chromium window while it runs.
- Use **Test Login** and **Dry Run** first.
- Only enable **ARM** when you truly intend to place trades.
- **Day-1 Build** requires typing `BUILD` so it can't be triggered by accident.
- If anything fails:
  1) Look at the **Latest Screenshot**,
  2) Read the **Bot Log tail**,
  3) Stop the run if it's stuck, then re-run **Test Login**.
"""
)
