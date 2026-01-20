"""
StockTrak Trading Bot - GUI Application
Morgan Stanley UWT Milgard Competition 2026 - Team 9

A graphical interface for controlling the trading bot with:
- LOGIN: Connect to StockTrak
- VERIFY LOGIN: Confirm access and fetch portfolio
- TEST TRADE: Execute a test (paper) validation
- START: Begin automated trading
- PAUSE: Temporarily halt trading
- END: Stop the bot completely
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import queue
import logging
import sys
import os
from datetime import datetime
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    STOCKTRAK_USERNAME, COMPETITION_START, COMPETITION_END,
    MAX_TRADES_TOTAL, STARTING_CAPITAL
)


class QueueHandler(logging.Handler):
    """Custom logging handler that puts logs into a queue for the GUI."""

    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


class StockTrakBotGUI:
    """Main GUI Application for StockTrak Trading Bot."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("StockTrak Trading Bot - Team 9")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)

        # Set icon if available
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
            if os.path.exists(icon_path):
                self.root.iconphoto(True, tk.PhotoImage(file=icon_path))
        except:
            pass

        # State variables
        self.bot = None
        self.scheduler_thread = None
        self.is_running = False
        self.is_paused = False
        self.is_logged_in = False
        self.stop_event = threading.Event()

        # Log queue for thread-safe logging
        self.log_queue = queue.Queue()

        # Setup logging
        self.setup_logging()

        # Build GUI
        self.create_widgets()

        # Start log consumer
        self.consume_logs()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Initial status update
        self.update_status("Ready - Click LOGIN to connect to StockTrak")

    def setup_logging(self):
        """Configure logging to send to GUI."""
        # Create logs directory
        os.makedirs('logs', exist_ok=True)

        # Root logger
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        # Clear existing handlers
        logger.handlers = []

        # Queue handler for GUI
        queue_handler = QueueHandler(self.log_queue)
        queue_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(queue_handler)

        # File handler
        file_handler = logging.FileHandler('logs/trading_bot.log')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)

    def create_widgets(self):
        """Create all GUI widgets."""
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # === HEADER ===
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        title_label = ttk.Label(
            header_frame,
            text="StockTrak Trading Bot",
            font=('Helvetica', 24, 'bold')
        )
        title_label.pack()

        subtitle_label = ttk.Label(
            header_frame,
            text="Morgan Stanley UWT Milgard Competition 2026 - Team 9",
            font=('Helvetica', 12)
        )
        subtitle_label.pack()

        # === STATUS BAR ===
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="10")
        status_frame.pack(fill=tk.X, pady=(0, 10))

        # Status indicator
        self.status_indicator = tk.Canvas(status_frame, width=20, height=20)
        self.status_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.status_circle = self.status_indicator.create_oval(2, 2, 18, 18, fill='gray')

        self.status_label = ttk.Label(status_frame, text="Initializing...", font=('Helvetica', 11))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # === INFO PANEL ===
        info_frame = ttk.LabelFrame(main_frame, text="Portfolio Info", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))

        info_grid = ttk.Frame(info_frame)
        info_grid.pack(fill=tk.X)

        # Row 1
        ttk.Label(info_grid, text="Account:", font=('Helvetica', 10, 'bold')).grid(row=0, column=0, sticky='w', padx=5)
        self.account_label = ttk.Label(info_grid, text=STOCKTRAK_USERNAME)
        self.account_label.grid(row=0, column=1, sticky='w', padx=5)

        ttk.Label(info_grid, text="Portfolio Value:", font=('Helvetica', 10, 'bold')).grid(row=0, column=2, sticky='w', padx=5)
        self.portfolio_label = ttk.Label(info_grid, text="$--")
        self.portfolio_label.grid(row=0, column=3, sticky='w', padx=5)

        # Row 2
        ttk.Label(info_grid, text="Trades Used:", font=('Helvetica', 10, 'bold')).grid(row=1, column=0, sticky='w', padx=5)
        self.trades_label = ttk.Label(info_grid, text=f"--/{MAX_TRADES_TOTAL}")
        self.trades_label.grid(row=1, column=1, sticky='w', padx=5)

        ttk.Label(info_grid, text="Holdings:", font=('Helvetica', 10, 'bold')).grid(row=1, column=2, sticky='w', padx=5)
        self.holdings_label = ttk.Label(info_grid, text="--")
        self.holdings_label.grid(row=1, column=3, sticky='w', padx=5)

        # Row 3
        ttk.Label(info_grid, text="Competition:", font=('Helvetica', 10, 'bold')).grid(row=2, column=0, sticky='w', padx=5)
        self.competition_label = ttk.Label(info_grid, text=f"{COMPETITION_START} to {COMPETITION_END}")
        self.competition_label.grid(row=2, column=1, columnspan=3, sticky='w', padx=5)

        # === CONTROL BUTTONS ===
        button_frame = ttk.LabelFrame(main_frame, text="Controls", padding="10")
        button_frame.pack(fill=tk.X, pady=(0, 10))

        button_grid = ttk.Frame(button_frame)
        button_grid.pack()

        # Button style
        style = ttk.Style()
        style.configure('Action.TButton', font=('Helvetica', 11), padding=10)
        style.configure('Start.TButton', font=('Helvetica', 11, 'bold'), padding=10)
        style.configure('Stop.TButton', font=('Helvetica', 11, 'bold'), padding=10)

        # LOGIN button
        self.login_btn = ttk.Button(
            button_grid,
            text="LOGIN",
            style='Action.TButton',
            command=self.on_login,
            width=15
        )
        self.login_btn.grid(row=0, column=0, padx=5, pady=5)

        # VERIFY LOGIN button
        self.verify_btn = ttk.Button(
            button_grid,
            text="VERIFY LOGIN",
            style='Action.TButton',
            command=self.on_verify,
            width=15,
            state='disabled'
        )
        self.verify_btn.grid(row=0, column=1, padx=5, pady=5)

        # TEST TRADE button
        self.test_btn = ttk.Button(
            button_grid,
            text="TEST TRADE",
            style='Action.TButton',
            command=self.on_test_trade,
            width=15,
            state='disabled'
        )
        self.test_btn.grid(row=0, column=2, padx=5, pady=5)

        # Separator
        ttk.Separator(button_grid, orient='vertical').grid(row=0, column=3, sticky='ns', padx=10)

        # START button
        self.start_btn = ttk.Button(
            button_grid,
            text="START",
            style='Start.TButton',
            command=self.on_start,
            width=15,
            state='disabled'
        )
        self.start_btn.grid(row=0, column=4, padx=5, pady=5)

        # PAUSE button
        self.pause_btn = ttk.Button(
            button_grid,
            text="PAUSE",
            style='Action.TButton',
            command=self.on_pause,
            width=15,
            state='disabled'
        )
        self.pause_btn.grid(row=0, column=5, padx=5, pady=5)

        # END button
        self.end_btn = ttk.Button(
            button_grid,
            text="END",
            style='Stop.TButton',
            command=self.on_end,
            width=15,
            state='disabled'
        )
        self.end_btn.grid(row=0, column=6, padx=5, pady=5)

        # === PROGRESS BAR ===
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(0, 10))

        # === LOG DISPLAY ===
        log_frame = ttk.LabelFrame(main_frame, text="Activity Log", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=15,
            font=('Consolas', 9),
            state='disabled',
            wrap=tk.WORD
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Configure log text colors
        self.log_text.tag_configure('INFO', foreground='black')
        self.log_text.tag_configure('WARNING', foreground='orange')
        self.log_text.tag_configure('ERROR', foreground='red')
        self.log_text.tag_configure('SUCCESS', foreground='green')

        # === FOOTER ===
        footer_frame = ttk.Frame(main_frame)
        footer_frame.pack(fill=tk.X, pady=(10, 0))

        self.time_label = ttk.Label(footer_frame, text="")
        self.time_label.pack(side=tk.LEFT)

        version_label = ttk.Label(footer_frame, text="v1.0.0 - Team 9")
        version_label.pack(side=tk.RIGHT)

        # Update time
        self.update_time()

    def update_time(self):
        """Update the time display."""
        now = datetime.now()
        self.time_label.config(text=now.strftime("%Y-%m-%d %H:%M:%S"))
        self.root.after(1000, self.update_time)

    def update_status(self, message, status='info'):
        """Update status bar with message and color."""
        self.status_label.config(text=message)

        colors = {
            'info': 'gray',
            'success': 'green',
            'warning': 'orange',
            'error': 'red',
            'running': 'green',
            'paused': 'yellow'
        }

        self.status_indicator.itemconfig(self.status_circle, fill=colors.get(status, 'gray'))

    def log(self, message, level='INFO'):
        """Add message to log display."""
        self.log_text.config(state='normal')

        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"

        self.log_text.insert(tk.END, formatted, level)
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def consume_logs(self):
        """Consume logs from queue and display them."""
        try:
            while True:
                message = self.log_queue.get_nowait()
                # Determine level from message
                if 'ERROR' in message or 'CRITICAL' in message:
                    level = 'ERROR'
                elif 'WARNING' in message:
                    level = 'WARNING'
                else:
                    level = 'INFO'

                self.log_text.config(state='normal')
                self.log_text.insert(tk.END, message + '\n', level)
                self.log_text.see(tk.END)
                self.log_text.config(state='disabled')
        except queue.Empty:
            pass

        # Schedule next check
        self.root.after(100, self.consume_logs)

    def show_progress(self, show=True):
        """Show or hide progress bar animation."""
        if show:
            self.progress.start(10)
        else:
            self.progress.stop()

    def on_login(self):
        """Handle LOGIN button click."""
        self.log("Initiating login to StockTrak...", 'INFO')
        self.update_status("Connecting to StockTrak...", 'info')
        self.show_progress(True)
        self.login_btn.config(state='disabled')

        # Run login in background thread
        thread = threading.Thread(target=self._do_login, daemon=True)
        thread.start()

    def _do_login(self):
        """Perform login in background thread."""
        try:
            from stocktrak_bot import StockTrakBot

            self.bot = StockTrakBot(headless=False)
            self.bot.start_browser()

            if self.bot.login():
                self.is_logged_in = True
                self.root.after(0, lambda: self._login_success())
            else:
                self.root.after(0, lambda: self._login_failed("Login failed - check credentials"))

        except Exception as e:
            self.root.after(0, lambda: self._login_failed(str(e)))

    def _login_success(self):
        """Handle successful login (called on main thread)."""
        self.show_progress(False)
        self.update_status("Logged in successfully!", 'success')
        self.log("LOGIN SUCCESSFUL - Connected to StockTrak", 'SUCCESS')

        # Enable next buttons
        self.verify_btn.config(state='normal')
        self.test_btn.config(state='normal')
        self.start_btn.config(state='normal')
        self.login_btn.config(text="RECONNECT", state='normal')

    def _login_failed(self, error):
        """Handle failed login (called on main thread)."""
        self.show_progress(False)
        self.update_status(f"Login failed: {error}", 'error')
        self.log(f"LOGIN FAILED: {error}", 'ERROR')
        self.login_btn.config(state='normal')

        if self.bot:
            try:
                self.bot.close()
            except:
                pass
            self.bot = None

    def on_verify(self):
        """Handle VERIFY LOGIN button click."""
        if not self.bot or not self.is_logged_in:
            messagebox.showwarning("Not Logged In", "Please login first.")
            return

        self.log("Verifying StockTrak access...", 'INFO')
        self.update_status("Verifying access...", 'info')
        self.show_progress(True)
        self.verify_btn.config(state='disabled')

        thread = threading.Thread(target=self._do_verify, daemon=True)
        thread.start()

    def _do_verify(self):
        """Perform verification in background thread."""
        try:
            # Get portfolio value
            portfolio_value = self.bot.get_portfolio_value()

            # Get holdings
            holdings = self.bot.get_current_holdings()

            # Get trade count
            trade_count = self.bot.get_transaction_count()

            # Get cash
            cash = self.bot.get_cash_balance()

            self.root.after(0, lambda: self._verify_success(
                portfolio_value, holdings, trade_count, cash
            ))

        except Exception as e:
            self.root.after(0, lambda: self._verify_failed(str(e)))

    def _verify_success(self, portfolio_value, holdings, trade_count, cash):
        """Handle successful verification."""
        self.show_progress(False)
        self.verify_btn.config(state='normal')

        # Update displays
        if portfolio_value:
            self.portfolio_label.config(text=f"${portfolio_value:,.2f}")

        self.trades_label.config(text=f"{trade_count}/{MAX_TRADES_TOTAL}")
        self.holdings_label.config(text=f"{len(holdings)} positions")

        self.update_status("Verification complete - All systems operational", 'success')
        self.log(f"VERIFIED: Portfolio=${portfolio_value:,.2f}, Holdings={len(holdings)}, Trades={trade_count}", 'SUCCESS')

        if holdings:
            self.log(f"Current positions: {', '.join(holdings.keys())}", 'INFO')

    def _verify_failed(self, error):
        """Handle failed verification."""
        self.show_progress(False)
        self.verify_btn.config(state='normal')
        self.update_status(f"Verification failed: {error}", 'error')
        self.log(f"VERIFICATION FAILED: {error}", 'ERROR')

    def on_test_trade(self):
        """Handle TEST TRADE button click."""
        if not self.bot or not self.is_logged_in:
            messagebox.showwarning("Not Logged In", "Please login first.")
            return

        # Confirm with user
        if not messagebox.askyesno(
            "Test Trade",
            "This will validate trading capability without executing a real trade.\n\n"
            "The bot will:\n"
            "1. Navigate to the trading page\n"
            "2. Verify form fields are accessible\n"
            "3. Take screenshots for verification\n\n"
            "No actual trade will be placed.\n\nContinue?"
        ):
            return

        self.log("Starting test trade validation...", 'INFO')
        self.update_status("Testing trade capability...", 'info')
        self.show_progress(True)
        self.test_btn.config(state='disabled')

        thread = threading.Thread(target=self._do_test_trade, daemon=True)
        thread.start()

    def _do_test_trade(self):
        """Perform test trade validation in background."""
        try:
            from market_data import MarketDataCollector

            # Test market data access
            collector = MarketDataCollector()
            data = collector.get_ticker_data('VOO')

            if data:
                self.root.after(0, lambda: self.log(
                    f"Market data OK: VOO=${data['price']:.2f}, SMA50=${data['sma50']:.2f}",
                    'INFO'
                ))

            # Navigate to trade page and verify fields
            self.bot.page.goto(f"{self.bot.base_url}/trading/stocks")
            self.bot.page.wait_for_load_state('networkidle')
            time.sleep(2)

            # Take screenshot
            self.bot._screenshot('test_trade_page')

            # Check if key elements exist
            page_content = self.bot.page.content().lower()

            checks = {
                'symbol_field': any(x in page_content for x in ['symbol', 'ticker']),
                'quantity_field': any(x in page_content for x in ['quantity', 'shares']),
                'order_type': any(x in page_content for x in ['limit', 'market']),
                'submit_button': any(x in page_content for x in ['submit', 'place order', 'preview']),
            }

            all_passed = all(checks.values())

            self.root.after(0, lambda: self._test_trade_complete(checks, all_passed))

        except Exception as e:
            self.root.after(0, lambda: self._test_trade_failed(str(e)))

    def _test_trade_complete(self, checks, all_passed):
        """Handle test trade completion."""
        self.show_progress(False)
        self.test_btn.config(state='normal')

        if all_passed:
            self.update_status("Test trade validation PASSED", 'success')
            self.log("TEST TRADE PASSED - All trading elements verified", 'SUCCESS')
        else:
            failed = [k for k, v in checks.items() if not v]
            self.update_status(f"Test trade validation partial: missing {failed}", 'warning')
            self.log(f"TEST TRADE WARNING - Missing elements: {failed}", 'WARNING')

        for check, passed in checks.items():
            status = "OK" if passed else "MISSING"
            self.log(f"  {check}: {status}", 'INFO' if passed else 'WARNING')

    def _test_trade_failed(self, error):
        """Handle test trade failure."""
        self.show_progress(False)
        self.test_btn.config(state='normal')
        self.update_status(f"Test trade failed: {error}", 'error')
        self.log(f"TEST TRADE FAILED: {error}", 'ERROR')

    def on_start(self):
        """Handle START button click."""
        if not self.is_logged_in:
            messagebox.showwarning("Not Logged In", "Please login and verify first.")
            return

        if self.is_running and not self.is_paused:
            messagebox.showinfo("Already Running", "Bot is already running.")
            return

        if self.is_paused:
            # Resume from pause
            self.is_paused = False
            self.stop_event.clear()
            self.update_status("Bot RESUMED - Trading active", 'running')
            self.log("Bot RESUMED", 'SUCCESS')
            self.start_btn.config(text="START", state='disabled')
            self.pause_btn.config(state='normal')
            return

        # Confirm start
        if not messagebox.askyesno(
            "Start Bot",
            "Start the automated trading bot?\n\n"
            f"The bot will run until {COMPETITION_END} executing:\n"
            "- Daily trades at 3:55 PM ET\n"
            "- Portfolio monitoring\n"
            "- Automatic rebalancing\n\n"
            "You can PAUSE or END at any time.\n\nStart now?"
        ):
            return

        self.log("STARTING automated trading bot...", 'SUCCESS')
        self.update_status("Bot RUNNING - Automated trading active", 'running')

        self.is_running = True
        self.is_paused = False
        self.stop_event.clear()

        # Update buttons
        self.start_btn.config(state='disabled')
        self.pause_btn.config(state='normal')
        self.end_btn.config(state='normal')
        self.login_btn.config(state='disabled')
        self.verify_btn.config(state='disabled')
        self.test_btn.config(state='disabled')

        # Start scheduler thread
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()

    def _run_scheduler(self):
        """Run the trading scheduler in background."""
        import pytz
        from datetime import datetime

        ET = pytz.timezone('US/Eastern')

        self.root.after(0, lambda: self.log("Scheduler started - waiting for execution window", 'INFO'))

        last_execution_date = None

        while not self.stop_event.is_set():
            try:
                now = datetime.now(ET)
                today = now.date()

                # Check if we should execute (3:55 PM ET on weekdays)
                is_weekday = now.weekday() < 5
                is_execution_time = now.hour == 15 and 55 <= now.minute <= 59
                not_executed_today = last_execution_date != today

                if is_weekday and is_execution_time and not_executed_today and not self.is_paused:
                    self.root.after(0, lambda: self.log("EXECUTION WINDOW - Starting daily routine", 'SUCCESS'))
                    self.root.after(0, lambda: self.update_status("Executing daily trades...", 'running'))

                    try:
                        from daily_routine import execute_daily_routine
                        execute_daily_routine()
                        last_execution_date = today

                        self.root.after(0, lambda: self.log("Daily routine completed successfully", 'SUCCESS'))
                        self.root.after(0, lambda: self._refresh_portfolio_info())

                    except Exception as e:
                        self.root.after(0, lambda: self.log(f"Execution error: {e}", 'ERROR'))

                # Check competition end date
                if today >= datetime.strptime(COMPETITION_END, '%Y-%m-%d').date():
                    self.root.after(0, lambda: self.log("COMPETITION ENDED - Stopping bot", 'SUCCESS'))
                    self.root.after(0, lambda: self.on_end())
                    break

                # Status update every minute
                if now.second == 0:
                    status = "PAUSED" if self.is_paused else "RUNNING"
                    next_exec = "3:55 PM ET" if not_executed_today else "Tomorrow 3:55 PM ET"
                    self.root.after(0, lambda: self.update_status(
                        f"Bot {status} - Next execution: {next_exec}",
                        'paused' if self.is_paused else 'running'
                    ))

                # Sleep for 30 seconds
                for _ in range(30):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                self.root.after(0, lambda: self.log(f"Scheduler error: {e}", 'ERROR'))
                time.sleep(60)

        self.root.after(0, lambda: self.log("Scheduler stopped", 'INFO'))

    def _refresh_portfolio_info(self):
        """Refresh portfolio information display."""
        try:
            if self.bot and self.is_logged_in:
                portfolio_value = self.bot.get_portfolio_value()
                holdings = self.bot.get_current_holdings()
                trade_count = self.bot.get_transaction_count()

                if portfolio_value:
                    self.portfolio_label.config(text=f"${portfolio_value:,.2f}")
                self.trades_label.config(text=f"{trade_count}/{MAX_TRADES_TOTAL}")
                self.holdings_label.config(text=f"{len(holdings)} positions")
        except:
            pass

    def on_pause(self):
        """Handle PAUSE button click."""
        if not self.is_running:
            return

        self.is_paused = True
        self.update_status("Bot PAUSED - Click START to resume", 'paused')
        self.log("Bot PAUSED by user", 'WARNING')

        self.start_btn.config(text="RESUME", state='normal')
        self.pause_btn.config(state='disabled')

    def on_end(self):
        """Handle END button click."""
        if self.is_running:
            if not messagebox.askyesno(
                "End Bot",
                "Are you sure you want to stop the bot?\n\n"
                "The bot will stop monitoring and trading.\n"
                "You can restart it later if needed."
            ):
                return

        self.log("STOPPING bot...", 'WARNING')
        self.update_status("Bot STOPPED", 'info')

        # Signal stop
        self.stop_event.set()
        self.is_running = False
        self.is_paused = False

        # Wait for thread
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=5)

        # Reset buttons
        self.start_btn.config(text="START", state='normal' if self.is_logged_in else 'disabled')
        self.pause_btn.config(state='disabled')
        self.end_btn.config(state='disabled')
        self.login_btn.config(state='normal')
        self.verify_btn.config(state='normal' if self.is_logged_in else 'disabled')
        self.test_btn.config(state='normal' if self.is_logged_in else 'disabled')

        self.log("Bot STOPPED", 'INFO')

    def on_closing(self):
        """Handle window close."""
        if self.is_running:
            if not messagebox.askyesno(
                "Quit",
                "The bot is still running!\n\n"
                "Are you sure you want to quit?\n"
                "This will stop all trading activity."
            ):
                return

        # Stop everything
        self.stop_event.set()

        # Close browser
        if self.bot:
            try:
                self.bot.close()
            except:
                pass

        self.root.destroy()

    def run(self):
        """Start the GUI application."""
        self.root.mainloop()


def main():
    """Main entry point for GUI application."""
    # Change to script directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Create and run app
    app = StockTrakBotGUI()
    app.run()


if __name__ == "__main__":
    main()
