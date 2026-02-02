"""
StockTrak Bot Dashboard - FastAPI Backend

Production-ready API backend for the dashboard.
Run with: uvicorn dashboard.backend.app:app --reload --port 8000

Endpoints:
    GET  /api/status       - Get current bot state
    GET  /api/logs         - Get recent log lines
    GET  /api/screenshots  - List recent screenshots
    GET  /api/screenshots/{name} - Serve screenshot image
    POST /api/run/test     - Run login test
    POST /api/run/dry-run  - Run dry run
    POST /api/run/manual   - Run manual execution (requires armed token)
    POST /api/run/day1     - Run Day-1 build (requires armed token)
    WS   /ws/events        - WebSocket for real-time updates
"""

from fastapi import FastAPI, WebSocket, HTTPException, Query, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
import asyncio
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Optional
import os

# ============================================================================
# Configuration
# ============================================================================
ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = ROOT / "stocktrak_bot"
STATE_FILE = BOT_DIR / "state" / "dashboard_state.json"
LOG_FILE = BOT_DIR / "logs" / "stocktrak_bot.log"
SCREENSHOTS_DIR = BOT_DIR / "logs"

# Secret token for armed operations (in production, use env var)
ARMED_TOKEN = os.environ.get("BOT_ARMED_TOKEN", "ARMED_SECRET_TOKEN_CHANGE_ME")

# ============================================================================
# App Setup
# ============================================================================
app = FastAPI(
    title="StockTrak Bot API",
    description="Backend API for StockTrak trading bot dashboard",
    version="1.0.0"
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track running processes
running_processes = {}


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
    return {"running": False, "mode": "IDLE", "error": "No state file yet"}


def run_bot_sync(args: list) -> tuple:
    """Run bot command synchronously, return (exit_code, output)"""
    try:
        result = subprocess.run(
            ["python", "main.py"] + args,
            cwd=str(BOT_DIR),
            capture_output=True,
            text=True,
            timeout=300
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, "Command timed out"
    except Exception as e:
        return -1, str(e)


async def run_bot_async(args: list, run_id: str):
    """Run bot command asynchronously"""
    def run():
        process = subprocess.Popen(
            ["python", "main.py"] + args,
            cwd=str(BOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        running_processes[run_id] = process
        output = process.communicate()[0]
        del running_processes[run_id]
        return process.returncode, output

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run)


# ============================================================================
# REST Endpoints
# ============================================================================
@app.get("/")
def root():
    """Health check"""
    return {"status": "ok", "service": "stocktrak-bot-api"}


@app.get("/api/status")
def get_status():
    """Get current bot state"""
    state = load_state()

    # Add active process info
    state["active_runs"] = len(running_processes)

    return state


@app.get("/api/logs")
def get_logs(tail: int = Query(default=200, ge=1, le=5000)):
    """Get recent log lines"""
    if not LOG_FILE.exists():
        return {"lines": [], "message": "No log file yet"}

    try:
        content = LOG_FILE.read_text(errors='ignore')
        lines = content.split('\n')
        return {"lines": lines[-tail:], "total_lines": len(lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/screenshots")
def list_screenshots(limit: int = Query(default=20, ge=1, le=100)):
    """List recent screenshots"""
    if not SCREENSHOTS_DIR.exists():
        return {"files": []}

    pngs = sorted(
        SCREENSHOTS_DIR.glob("*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return {
        "files": [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat()
            }
            for p in pngs[:limit]
        ]
    }


@app.get("/api/screenshots/{name}")
def get_screenshot(name: str):
    """Serve a screenshot image"""
    path = SCREENSHOTS_DIR / name
    if not path.exists() or not path.suffix == '.png':
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(path), media_type="image/png")


@app.post("/api/run/test")
async def run_test():
    """Run login test"""
    run_id = str(uuid.uuid4())[:8]
    code, output = await run_bot_async(["--test"], run_id)
    return {
        "ok": code == 0,
        "exit_code": code,
        "run_id": run_id,
        "output": output[-5000:] if len(output) > 5000 else output
    }


@app.post("/api/run/capital-test")
async def run_capital_test():
    """Run capital reading test"""
    run_id = str(uuid.uuid4())[:8]
    code, output = await run_bot_async(["--capital-test"], run_id)
    return {
        "ok": code == 0,
        "exit_code": code,
        "run_id": run_id,
        "output": output[-5000:] if len(output) > 5000 else output
    }


@app.post("/api/run/dry-run")
async def run_dry_run():
    """Run dry run (no actual trades)"""
    run_id = str(uuid.uuid4())[:8]
    code, output = await run_bot_async(["--dry-run"], run_id)
    return {
        "ok": code == 0,
        "exit_code": code,
        "run_id": run_id,
        "output": output[-5000:] if len(output) > 5000 else output
    }


@app.post("/api/run/manual")
async def run_manual(x_armed_token: Optional[str] = Header(default=None)):
    """Run manual execution (LIVE TRADING - requires armed token)"""
    if x_armed_token != ARMED_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Armed token required for live trading. Pass X-Armed-Token header."
        )

    run_id = str(uuid.uuid4())[:8]
    code, output = await run_bot_async(["--manual"], run_id)
    return {
        "ok": code == 0,
        "exit_code": code,
        "run_id": run_id,
        "output": output[-5000:] if len(output) > 5000 else output
    }


@app.post("/api/run/day1")
async def run_day1(x_armed_token: Optional[str] = Header(default=None)):
    """Run Day-1 build (LIVE TRADING - requires armed token)"""
    if x_armed_token != ARMED_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Armed token required for Day-1 build. Pass X-Armed-Token header."
        )

    run_id = str(uuid.uuid4())[:8]
    code, output = await run_bot_async(["--day1"], run_id)
    return {
        "ok": code == 0,
        "exit_code": code,
        "run_id": run_id,
        "output": output[-5000:] if len(output) > 5000 else output
    }


@app.post("/api/bot/stop")
def stop_bot():
    """Stop any running bot processes"""
    stopped = []
    for run_id, process in list(running_processes.items()):
        try:
            process.terminate()
            stopped.append(run_id)
        except:
            pass
    return {"stopped": stopped, "count": len(stopped)}


# ============================================================================
# WebSocket for Real-time Updates
# ============================================================================
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket endpoint for real-time log/state updates"""
    await websocket.accept()

    last_log_size = 0
    last_state_mtime = 0

    try:
        while True:
            events = []

            # Check for new log lines
            if LOG_FILE.exists():
                current_size = LOG_FILE.stat().st_size
                if current_size > last_log_size:
                    content = LOG_FILE.read_text(errors='ignore')
                    new_content = content[last_log_size:]
                    events.append({
                        "type": "log",
                        "data": new_content[-2000:]  # Last 2000 chars of new content
                    })
                    last_log_size = current_size

            # Check for state updates
            if STATE_FILE.exists():
                current_mtime = STATE_FILE.stat().st_mtime
                if current_mtime > last_state_mtime:
                    state = load_state()
                    events.append({
                        "type": "state",
                        "data": state
                    })
                    last_state_mtime = current_mtime

            # Send events if any
            for event in events:
                await websocket.send_json(event)

            await asyncio.sleep(0.5)

    except Exception:
        pass  # Client disconnected


# ============================================================================
# Run Server
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
