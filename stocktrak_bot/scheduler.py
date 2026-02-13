"""
Task Scheduler for StockTrak Bot

Handles scheduling of:
- Daily data collection (pre-market)
- Daily execution (morning hours, configurable via EXECUTION_TIME)
- Weekly counter reset (Fridays 4:15 PM ET)
- Hourly health checks

CRITICAL: The schedule library uses LOCAL machine time, not timezone-aware times.
This module converts ET times to local machine time for correct scheduling.

IMPORTANT: All times are now configurable via config.py for consistency.
"""

import logging
import time
from datetime import datetime, timedelta
import schedule
import pytz

from daily_routine import execute_daily_routine, health_check
from state_manager import StateManager
from utils import is_trading_day, is_market_hours
from config import EXECUTION_TIME, DATA_COLLECTION_TIME, EXECUTION_WINDOW_START, EXECUTION_WINDOW_END

logger = logging.getLogger('stocktrak_bot.scheduler')

ET = pytz.timezone('US/Eastern')


def _parse_time_string(time_str: str) -> tuple:
    """
    Parse a time string in HH:MM format to (hour, minute) tuple.

    Args:
        time_str: Time string like "09:45" or "15:30"

    Returns:
        Tuple of (hour, minute) as integers
    """
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def et_to_local_time(et_hour: int, et_minute: int) -> str:
    """
    Convert an ET time to local machine time.

    The schedule library uses LOCAL time, not timezone-aware times.
    This function calculates what local time corresponds to a given ET time.

    Args:
        et_hour: Hour in Eastern Time (0-23)
        et_minute: Minute (0-59)

    Returns:
        String in HH:MM format for local time
    """
    # Get current date in ET
    now_et = datetime.now(ET)

    # Create a datetime for the target ET time today
    target_et = now_et.replace(hour=et_hour, minute=et_minute, second=0, microsecond=0)

    # Convert to local time (naive datetime in local timezone)
    target_local = target_et.astimezone().replace(tzinfo=None)

    # Extract the local time
    local_time_str = target_local.strftime("%H:%M")

    logger.debug(f"ET {et_hour:02d}:{et_minute:02d} -> Local {local_time_str}")
    return local_time_str


def run_scheduler():
    """
    Main scheduling loop.

    Schedules:
    - DATA_COLLECTION_TIME ET: Prepare for execution (weekdays)
    - EXECUTION_TIME ET: Execute daily routine (weekdays) - morning hours
    - 4:15 PM ET Friday: Weekly reset
    - Every hour: Health check

    IMPORTANT: All times are converted from ET to local machine time.
    Times are configurable via config.py for consistency across modules.
    """
    logger.info("=" * 60)
    logger.info("STOCKTRAK BOT SCHEDULER STARTING")
    logger.info(f"Current time (ET): {datetime.now(ET)}")
    logger.info(f"Current time (Local): {datetime.now()}")
    logger.info(f"Configured execution time: {EXECUTION_TIME} ET")
    logger.info(f"Configured execution window: {EXECUTION_WINDOW_START} - {EXECUTION_WINDOW_END} ET")
    logger.info("=" * 60)

    # Clear any existing jobs
    schedule.clear()

    # Parse and convert EXECUTION_TIME from config to local time
    et_hour, et_minute = _parse_time_string(EXECUTION_TIME)
    execution_time = et_to_local_time(et_hour, et_minute)
    logger.info(f"Execution time: {EXECUTION_TIME} ET = {execution_time} local")

    # Daily execution at EXECUTION_TIME ET (market days) - morning hours
    # Time is configurable via config.py EXECUTION_TIME
    schedule.every().monday.at(execution_time).do(safe_execute)
    schedule.every().tuesday.at(execution_time).do(safe_execute)
    schedule.every().wednesday.at(execution_time).do(safe_execute)
    schedule.every().thursday.at(execution_time).do(safe_execute)
    schedule.every().friday.at(execution_time).do(safe_execute)

    # Weekly reset on Fridays at 4:15 PM ET
    weekly_reset_time = et_to_local_time(16, 15)
    schedule.every().friday.at(weekly_reset_time).do(weekly_reset)

    # Health check every hour
    schedule.every().hour.do(safe_health_check)

    # Keep-alive ping every 5 minutes
    schedule.every(5).minutes.do(keep_alive)

    logger.info("Scheduled jobs:")
    for job in schedule.get_jobs():
        logger.info(f"  - {job}")

    logger.info("\nBot is running. Press Ctrl+C to stop.")
    logger.info("Next run times:")
    log_next_runs()

    # Main loop with adaptive check interval
    while True:
        try:
            schedule.run_pending()

            # Adaptive sleep: shorter during execution window
            check_interval = _get_check_interval()
            time.sleep(check_interval)

        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            break

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            time.sleep(30)  # Brief wait before retrying


def _get_check_interval() -> int:
    """
    Get adaptive check interval based on time of day.

    Returns shorter interval during critical execution window.
    Window is configured via EXECUTION_WINDOW_START and EXECUTION_WINDOW_END.
    """
    now = datetime.now(ET)
    hour = now.hour
    minute = now.minute
    current_time_str = f"{hour:02d}:{minute:02d}"

    # Critical window: EXECUTION_WINDOW with buffer
    # Parse window times
    start_h, start_m = _parse_time_string(EXECUTION_WINDOW_START)
    end_h, end_m = _parse_time_string(EXECUTION_WINDOW_END)

    # Add 10-minute buffer on each side
    buffer_start = start_h * 60 + start_m - 10
    buffer_end = end_h * 60 + end_m + 10
    current_minutes = hour * 60 + minute

    if buffer_start <= current_minutes <= buffer_end:
        return 5  # Check every 5 seconds during execution window

    # Market hours: 9:30 AM - 4:00 PM ET
    if 9 <= hour < 16:
        return 15  # Check every 15 seconds

    # Off hours
    return 60  # Check every minute


def safe_execute():
    """
    Wrapper for execute_daily_routine with error handling.
    """
    now = datetime.now(ET)
    logger.info(f"Scheduled execution triggered at {now}")

    # Check if trading day
    if not is_trading_day():
        logger.info("Not a trading day - skipping")
        return

    try:
        execute_daily_routine()
    except Exception as e:
        logger.critical(f"Execution failed: {e}")
        import traceback
        logger.critical(traceback.format_exc())

        # Log error to state
        try:
            state = StateManager()
            state.log_error(str(e))
        except Exception as state_err:
            logger.error(f"Could not log error to state: {state_err}")


def safe_health_check():
    """
    Wrapper for health_check with error handling.
    """
    try:
        health_check()
    except Exception as e:
        logger.error(f"Health check failed: {e}")


def weekly_reset():
    """
    Reset weekly counters.
    Called every Friday at 4:15 PM ET.
    """
    logger.info("Weekly reset triggered")
    try:
        state = StateManager()
        state.reset_weekly_counters()
        logger.info("Weekly counters reset successfully")
    except Exception as e:
        logger.error(f"Weekly reset failed: {e}")


def keep_alive():
    """
    Keep-alive ping to prevent system sleep.
    Also logs current status periodically.
    """
    now = datetime.now(ET)

    # Only log during market hours
    if is_market_hours():
        logger.debug(f"Keep-alive ping at {now.strftime('%H:%M:%S')} ET")


def log_next_runs():
    """Log the next scheduled run times."""
    jobs = schedule.get_jobs()
    if jobs:
        for job in jobs[:5]:  # Show first 5
            next_run = job.next_run
            if next_run:
                logger.info(f"  Next: {next_run}")


def run_once_now():
    """
    Run the daily routine immediately (for testing).
    """
    logger.info("Manual execution triggered")

    if not is_trading_day():
        logger.warning("Not a trading day, but continuing anyway...")

    execute_daily_routine()


def run_with_auto_restart():
    """
    Run scheduler with automatic restart on crash.
    """
    from config import AUTO_RESTART_ON_CRASH, MAX_RESTART_ATTEMPTS, RESTART_DELAY_SECONDS

    restart_count = 0

    while True:
        try:
            run_scheduler()
            break  # Normal exit

        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break

        except Exception as e:
            restart_count += 1
            logger.critical(f"Scheduler crashed (attempt {restart_count}): {e}")

            if not AUTO_RESTART_ON_CRASH:
                raise

            if restart_count >= MAX_RESTART_ATTEMPTS:
                logger.critical(f"Max restart attempts ({MAX_RESTART_ATTEMPTS}) reached. Stopping.")
                raise

            logger.info(f"Restarting in {RESTART_DELAY_SECONDS} seconds...")
            time.sleep(RESTART_DELAY_SECONDS)


class KeepAwake:
    """
    Utility to prevent system from sleeping during bot operation.
    """

    def __init__(self):
        self.active = False

    def start(self):
        """Start keep-awake (platform-specific)."""
        import platform
        system = platform.system()

        if system == 'Windows':
            self._start_windows()
        elif system == 'Darwin':  # macOS
            self._start_macos()
        else:  # Linux
            self._start_linux()

        self.active = True
        logger.info("Keep-awake started")

    def _start_windows(self):
        """Windows: Set thread execution state."""
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002

            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
        except Exception as e:
            logger.warning(f"Could not set Windows keep-awake: {e}")

    def _start_macos(self):
        """macOS: Use caffeinate (background process)."""
        try:
            import subprocess
            self.caffeinate_proc = subprocess.Popen(
                ['caffeinate', '-i'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.warning(f"Could not start macOS caffeinate: {e}")

    def _start_linux(self):
        """Linux: Various methods depending on desktop environment."""
        # Most headless Linux servers don't sleep anyway
        pass

    def stop(self):
        """Stop keep-awake."""
        if not self.active:
            return

        import platform
        system = platform.system()

        if system == 'Windows':
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS only
            except Exception:
                pass  # Non-critical: keep-awake cleanup failure

        elif system == 'Darwin':
            if hasattr(self, 'caffeinate_proc'):
                self.caffeinate_proc.terminate()

        self.active = False
        logger.info("Keep-awake stopped")


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/scheduler.log'),
            logging.StreamHandler()
        ]
    )

    # Create logs directory
    import os
    os.makedirs('logs', exist_ok=True)

    if len(sys.argv) > 1:
        if sys.argv[1] == '--now':
            run_once_now()
        elif sys.argv[1] == '--auto-restart':
            run_with_auto_restart()
        else:
            print("Usage: python scheduler.py [--now|--auto-restart]")
    else:
        run_scheduler()
