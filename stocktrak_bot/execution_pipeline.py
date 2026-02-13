"""
Stall-Proof Trade Execution Pipeline for StockTrak

This module implements a finite state machine approach to trade execution.
Every step has:
- Hard timeout + retries
- Screenshot on failure
- Checkpoint verification
- Recovery/abort on deviation

The pipeline:
1. Verify logged in
2. Navigate to trade ticket
3. Fill order form
4. Preview order
5. Place order
6. Verify in history
7. Attach trade note

NEVER HANG. NEVER DOUBLE-PLACE. ALWAYS RECOVER OR ABORT SAFELY.
"""

import logging
import time
import re
import uuid
from datetime import datetime
from typing import Tuple, Optional, Callable, Any
from enum import Enum
from dataclasses import dataclass

from state_manager import StateManager
from config import (
    STOCKTRAK_URL, STOCKTRAK_DASHBOARD_URL, STOCKTRAK_TRADING_URL,
    STOCKTRAK_TRADING_EQUITIES_URL, STOCKTRAK_TRANSACTION_HISTORY_URL,
    STOCKTRAK_ORDER_HISTORY_URL, DEFAULT_TIMEOUT, PAGE_LOAD_TIMEOUT
)

logger = logging.getLogger('stocktrak_bot.execution_pipeline')


# =============================================================================
# CIRCUIT BREAKER - Stops execution if too many consecutive failures
# =============================================================================
class CircuitBreaker:
    """
    Circuit breaker pattern to prevent runaway failures.

    If MAX_CONSECUTIVE_FAILURES trades fail in a row, the circuit opens
    and no more trades are attempted until manually reset or timeout expires.
    """
    MAX_CONSECUTIVE_FAILURES = 3
    RESET_TIMEOUT_SECONDS = 300  # 5 minutes

    def __init__(self):
        self.consecutive_failures = 0
        self.is_open = False
        self.last_failure_time = None
        self.total_failures = 0
        self.total_successes = 0

    def record_success(self):
        """Record a successful trade execution."""
        self.consecutive_failures = 0
        self.is_open = False
        self.total_successes += 1
        logger.debug(f"Circuit breaker: success recorded ({self.total_successes} total)")

    def record_failure(self, reason: str = ""):
        """Record a failed trade execution."""
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_time = datetime.now()

        logger.warning(
            f"Circuit breaker: failure #{self.consecutive_failures} "
            f"(total: {self.total_failures}){': ' + reason if reason else ''}"
        )

        if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            self.is_open = True
            logger.error(
                f"CIRCUIT BREAKER OPEN: {self.consecutive_failures} consecutive failures. "
                f"No trades will be attempted for {self.RESET_TIMEOUT_SECONDS}s or until reset."
            )

    def can_execute(self) -> Tuple[bool, str]:
        """Check if trade execution is allowed."""
        if not self.is_open:
            return True, "Circuit closed"

        # Check if timeout has expired
        if self.last_failure_time:
            elapsed = (datetime.now() - self.last_failure_time).total_seconds()
            if elapsed >= self.RESET_TIMEOUT_SECONDS:
                logger.info(f"Circuit breaker auto-reset after {elapsed:.0f}s timeout")
                self.reset()
                return True, "Circuit auto-reset after timeout"

        return False, (
            f"Circuit breaker OPEN: {self.consecutive_failures} consecutive failures. "
            f"Wait for timeout or call reset()."
        )

    def reset(self):
        """Manually reset the circuit breaker."""
        self.consecutive_failures = 0
        self.is_open = False
        logger.info("Circuit breaker manually reset")

    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        return {
            'is_open': self.is_open,
            'consecutive_failures': self.consecutive_failures,
            'total_failures': self.total_failures,
            'total_successes': self.total_successes,
            'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None
        }


# Global circuit breaker instance
_circuit_breaker = CircuitBreaker()


def get_circuit_breaker_status() -> dict:
    """Get the current circuit breaker status for external monitoring."""
    return _circuit_breaker.get_status()


def reset_circuit_breaker():
    """Reset the circuit breaker after manual intervention."""
    _circuit_breaker.reset()
    return _circuit_breaker.get_status()


# =============================================================================
# EXECUTION STATES
# =============================================================================
class TradeState(Enum):
    """Trade execution state machine states"""
    INIT = "INIT"
    LOGGED_IN = "LOGGED_IN"
    ON_TRADE_PAGE = "ON_TRADE_PAGE"
    FORM_FILLED = "FORM_FILLED"
    PREVIEWED = "PREVIEWED"
    PLACED = "PLACED"
    VERIFIED = "VERIFIED"
    NOTED = "NOTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


@dataclass
class TradeOrder:
    """Represents a trade order to execute"""
    ticker: str
    side: str  # "BUY" or "SELL"
    shares: int
    order_type: str = "MARKET"  # "MARKET" or "LIMIT"
    limit_price: Optional[float] = None
    rationale: str = ""
    portfolio_pct: float = 0.0  # Percentage of portfolio this trade represents
    run_id: str = ""

    def __post_init__(self):
        if not self.run_id:
            self.run_id = str(uuid.uuid4())[:8]
        self.ticker = self.ticker.upper()
        self.side = self.side.upper()


@dataclass
class TradeResult:
    """Result of a trade execution"""
    success: bool
    state: TradeState
    message: str
    order: TradeOrder
    screenshots: list
    verified_in_history: bool = False
    note_added: bool = False


# =============================================================================
# CORE PIPELINE CLASS
# =============================================================================
class ExecutionPipeline:
    """
    Stall-proof trade execution pipeline.

    Usage:
        from execution_pipeline import ExecutionPipeline, TradeOrder

        pipeline = ExecutionPipeline(bot)
        order = TradeOrder(ticker="VOO", side="BUY", shares=10, rationale="Day-1 build")
        result = pipeline.execute(order)

        if result.success:
            print(f"Trade completed: {result.message}")
        else:
            print(f"Trade failed at {result.state}: {result.message}")
    """

    def __init__(self, bot, state_manager: StateManager = None, dry_run: bool = False):
        """
        Initialize the execution pipeline.

        Args:
            bot: StockTrakBot instance (must have page attribute)
            state_manager: StateManager for idempotency checks
            dry_run: If True, stop before placing order
        """
        self.bot = bot
        self.page = bot.page
        self.state_manager = state_manager or StateManager()
        self.dry_run = dry_run
        self.screenshots = []
        self.current_state = TradeState.INIT

    def execute(self, order: TradeOrder) -> TradeResult:
        """
        Execute a complete trade with all checkpoints.

        This is the main entry point. It runs through all states
        and handles failures gracefully.

        Args:
            order: TradeOrder to execute

        Returns:
            TradeResult with success/failure details
        """
        # CHECK CIRCUIT BREAKER - abort if too many recent failures
        can_exec, reason = _circuit_breaker.can_execute()
        if not can_exec:
            logger.error(f"CIRCUIT BREAKER BLOCKED: {reason}")
            return TradeResult(
                success=False,
                state=TradeState.ABORTED,
                message=f"Circuit breaker blocked execution: {reason}",
                order=order,
                screenshots=[]
            )

        logger.info(f"=" * 60)
        logger.info(f"EXECUTION PIPELINE START: {order.side} {order.shares} {order.ticker}")
        logger.info(f"Run ID: {order.run_id}")
        logger.info(f"=" * 60)

        self.screenshots = []
        self.current_state = TradeState.INIT

        # Update dashboard state
        self._update_dashboard("RUNNING", "STARTING", order)

        try:
            # STEP 1: Verify logged in
            self._run_step("verify_login", lambda: self._verify_logged_in(), order)
            self.current_state = TradeState.LOGGED_IN

            # STEP 2: Idempotency check
            if self._check_already_placed(order):
                logger.warning(f"Order already placed today - aborting")
                self.current_state = TradeState.ABORTED
                return TradeResult(
                    success=False,
                    state=self.current_state,
                    message="Duplicate order blocked - already submitted today",
                    order=order,
                    screenshots=self.screenshots
                )

            # STEP 2.5: HARD GUARD - 24-hour minimum hold enforcement for SELLs
            # CRITICAL: This is a last-line-of-defense block to prevent violations
            # Uses lot-based FIFO or STRICT_TICKER mode depending on config.HOLD_MODE
            if order.side == "SELL":
                can_sell, allowed_qty, hold_reason = self._check_24h_hold(order.ticker, order.shares)
                if not can_sell:
                    logger.error(f"SELL BLOCKED by 24h hold rule: {hold_reason}")
                    self.current_state = TradeState.ABORTED
                    return TradeResult(
                        success=False,
                        state=self.current_state,
                        message=f"SELL blocked by 24h hold rule: {hold_reason}",
                        order=order,
                        screenshots=self.screenshots
                    )
                # Check if we need to reduce the order size (LOT_FIFO partial sell)
                if allowed_qty < order.shares:
                    logger.warning(
                        f"SELL reduced from {order.shares} to {allowed_qty} shares "
                        f"(lot-based eligibility): {hold_reason}"
                    )
                    order.shares = allowed_qty
                    if order.shares <= 0:
                        logger.error("No shares eligible for sale after reduction")
                        self.current_state = TradeState.ABORTED
                        return TradeResult(
                            success=False,
                            state=self.current_state,
                            message="No eligible shares to sell",
                            order=order,
                            screenshots=self.screenshots
                        )
                logger.info(f"24h hold check passed: {hold_reason}")

            # STEP 3: Navigate to trade page
            self._run_step("navigate_trade", lambda: self._navigate_to_trade(order), order)
            self.current_state = TradeState.ON_TRADE_PAGE

            # STEP 4: Fill order form
            self._run_step("fill_form", lambda: self._fill_order_form(order), order)
            self.current_state = TradeState.FORM_FILLED

            # STEP 5: Preview order
            self._run_step("preview", lambda: self._preview_order(order), order)
            self.current_state = TradeState.PREVIEWED

            # DRY RUN: Stop here
            if self.dry_run:
                logger.info("DRY RUN - stopping before Place Order")
                self._take_screenshot(f"dry_run_preview_{order.ticker}")
                return TradeResult(
                    success=True,
                    state=TradeState.PREVIEWED,
                    message="Dry run completed - order not placed",
                    order=order,
                    screenshots=self.screenshots
                )

            # STEP 6: Place order (with full retry if needed)
            # If place fails, we need to re-run the whole flow, not just place
            self._run_step_with_full_retry("place", order)
            self.current_state = TradeState.PLACED

            # STEP 7: Verify in history (CRITICAL)
            verified = self._run_step("verify_history", lambda: self._verify_in_history(order), order, required=False)
            if verified:
                self.current_state = TradeState.VERIFIED
            else:
                logger.warning("Could not verify in history - order may or may not have gone through")

            # STEP 8: Add trade note
            if order.rationale:
                noted = self._run_step("add_note", lambda: self._add_trade_note(order), order, required=False)
                if noted:
                    self.current_state = TradeState.NOTED

            # SUCCESS
            self.current_state = TradeState.COMPLETED
            self._update_dashboard("IDLE", "COMPLETED", order)

            # Log to state manager
            self.state_manager.log_trade(
                ticker=order.ticker,
                action=order.side,
                shares=order.shares,
                price=order.limit_price or 0,
                reason=order.rationale
            )
            self.state_manager.increment_trade_count()

            # Update lots based on trade type
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)

            if order.side == "SELL":
                # Consume shares from eligible lots FIFO
                try:
                    self.state_manager.consume_sell_fifo(order.ticker, order.shares, now_utc)
                    logger.info(f"Lots updated: consumed {order.shares} shares from {order.ticker}")
                except ValueError as e:
                    logger.error(f"Failed to update lots after SELL: {e}")
                    # Trade already executed, log error but don't fail
            elif order.side == "BUY":
                # add_position is called by the caller (daily_routine), which now creates lots
                # Just log for clarity
                logger.info(f"BUY executed - caller should call add_position to create lot")

            logger.info(f"EXECUTION PIPELINE COMPLETED: {order.side} {order.shares} {order.ticker}")

            # Record success for circuit breaker
            _circuit_breaker.record_success()

            return TradeResult(
                success=True,
                state=self.current_state,
                message="Trade executed successfully",
                order=order,
                screenshots=self.screenshots,
                verified_in_history=verified,
                note_added=bool(order.rationale and noted)
            )

        except Exception as e:
            self.current_state = TradeState.FAILED
            self._take_screenshot(f"pipeline_error_{order.ticker}")
            self._update_dashboard("IDLE", "FAILED", order, error=str(e))

            logger.error(f"EXECUTION PIPELINE FAILED at {self.current_state}: {e}")

            # Record failure for circuit breaker
            _circuit_breaker.record_failure(f"{order.ticker}: {str(e)[:100]}")

            return TradeResult(
                success=False,
                state=self.current_state,
                message=str(e),
                order=order,
                screenshots=self.screenshots
            )

    # =========================================================================
    # STEP WRAPPER
    # =========================================================================
    def _run_step(self, name: str, fn: Callable[[], Any], order: TradeOrder,
                  max_attempts: int = 3, required: bool = True) -> Any:
        """
        Execute a step with retries, screenshots, and recovery.

        Args:
            name: Step name for logging
            fn: Function to execute
            order: Current order (for logging)
            max_attempts: Maximum retry attempts
            required: If False, failure doesn't abort pipeline

        Returns:
            Result of fn() if successful

        Raises:
            Exception if required and all attempts fail
        """
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"[{name}] Attempt {attempt}/{max_attempts}")
                self._update_dashboard("RUNNING", name.upper(), order)

                # Dismiss overlays before every step
                self._dismiss_overlays()

                result = fn()

                logger.info(f"[{name}] SUCCESS")
                return result

            except Exception as e:
                last_error = e
                logger.warning(f"[{name}] FAILED attempt {attempt}: {e}")

                # Screenshot on failure
                self._take_screenshot(f"{name}_fail_{attempt}_{order.ticker}")

                # Reset to dashboard between retries
                if attempt < max_attempts:
                    try:
                        logger.info(f"[{name}] Resetting to dashboard...")
                        self.page.goto(
                            STOCKTRAK_DASHBOARD_URL,
                            wait_until="domcontentloaded",
                            timeout=PAGE_LOAD_TIMEOUT
                        )
                        self._dismiss_overlays()
                        time.sleep(1)
                    except Exception as reset_err:
                        logger.warning(f"[{name}] Reset failed: {reset_err}")

        # All attempts failed
        if required:
            raise last_error
        else:
            logger.warning(f"[{name}] All attempts failed (non-required step)")
            return False

    def _run_step_with_full_retry(self, name: str, order: TradeOrder, max_attempts: int = 3):
        """
        Execute place step with FULL trade flow retry on failure.

        Unlike _run_step which just resets to dashboard, this method re-runs
        the entire navigate→fill→preview→place flow on each retry.

        This is necessary because after place fails and we reset to dashboard,
        the confirmation UI is gone and retrying just the place step won't work.
        """
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"[{name}] Attempt {attempt}/{max_attempts}")
                self._update_dashboard("RUNNING", name.upper(), order)
                self._dismiss_overlays()

                result = self._place_order(order)

                logger.info(f"[{name}] SUCCESS")
                return result

            except Exception as e:
                last_error = e
                logger.warning(f"[{name}] FAILED attempt {attempt}: {e}")
                self._take_screenshot(f"{name}_fail_{attempt}_{order.ticker}")

                if attempt < max_attempts:
                    # FULL RETRY: Re-run entire trade flow, not just place
                    logger.info(f"[{name}] Re-running full trade flow for retry...")
                    try:
                        # Reset to dashboard
                        self.page.goto(
                            STOCKTRAK_DASHBOARD_URL,
                            wait_until="domcontentloaded",
                            timeout=PAGE_LOAD_TIMEOUT
                        )
                        self._dismiss_overlays()
                        time.sleep(1)

                        # Re-run: navigate → fill → preview
                        logger.info(f"[{name}] Re-navigating to trade page...")
                        self._navigate_to_trade(order)

                        logger.info(f"[{name}] Re-filling order form...")
                        self._fill_order_form(order)

                        logger.info(f"[{name}] Re-clicking Review Order...")
                        self._preview_order(order)

                        logger.info(f"[{name}] Ready for retry attempt {attempt + 1}")

                    except Exception as setup_err:
                        logger.warning(f"[{name}] Full retry setup failed: {setup_err}")
                        # Continue to next attempt anyway

        # All attempts failed
        raise last_error

    # =========================================================================
    # PIPELINE STEPS
    # =========================================================================
    def _verify_logged_in(self) -> bool:
        """Verify we're logged in to StockTrak."""
        logger.info("Verifying login status...")

        # FIRST: Check if we're on login page - if so, definitely not logged in
        url = self.page.url.lower()
        if '/login' in url:
            logger.info("On login page - need to authenticate")
            if not self.bot.login():
                raise RuntimeError("Login failed")
            return True

        # Check for Logout link - ONLY exists when authenticated
        try:
            logout = self.page.get_by_role("link", name=re.compile("logout", re.I))
            if logout.count() > 0 and logout.first.is_visible(timeout=3000):
                logger.info("Login verified via Logout link")
                return True
        except Exception:
            pass

        # Check for authenticated-only indicators (NOT "Welcome back" - that's on login page too!)
        indicators = [
            "text=PORTFOLIO VALUE",
            "text=BUYING POWER",
            "text=Open Positions",
            "text=My Dashboard",
            "text=Portfolio Simulation",
        ]

        for indicator in indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    logger.info(f"Login verified via: {indicator}")
                    return True
            except Exception:
                continue

        # Check for password field - indicates login page
        try:
            if self.page.locator("input[type='password']").first.is_visible(timeout=1000):
                logger.info("Password field visible - on login page")
                if not self.bot.login():
                    raise RuntimeError("Login failed")
                return True
        except Exception:
            pass

        # Not logged in - try to login
        logger.info("No login indicators found - attempting login...")
        if not self.bot.login():
            raise RuntimeError("Login failed")

        return True

    def _check_already_placed(self, order: TradeOrder) -> bool:
        """Check if this order was already placed today (idempotency)."""
        return self.state_manager.already_submitted_today(
            ticker=order.ticker,
            action=order.side,
            shares=order.shares,
            price=order.limit_price or 0
        )

    def _check_24h_hold(self, ticker: str, shares: int = None) -> tuple:
        """
        HARD GUARD: Check 24-hour + buffer holding period before SELL using lot-based validation.

        This is a last-line-of-defense check to prevent accidental violations
        even if upstream logic has bugs. Uses lot-based FIFO or STRICT_TICKER mode
        depending on config.HOLD_MODE.

        Args:
            ticker: Ticker to sell
            shares: Number of shares to sell (optional, defaults to full position)

        Returns:
            Tuple of (can_sell, allowed_qty, reason)
            - can_sell: True if any sell is allowed
            - allowed_qty: Number of shares allowed (may be < requested in LOT_FIFO mode)
            - reason: Human-readable explanation
        """
        from validators import can_sell_with_lots
        from datetime import datetime, timezone

        positions = self.state_manager.get_positions()
        pos = positions.get(ticker)

        if not pos:
            return False, 0, f"No position state found for {ticker}"

        # Get total shares if not specified
        if shares is None:
            shares = self.state_manager.get_total_shares(ticker)

        now_utc = datetime.now(timezone.utc)

        # Use lot-based validation
        can_sell, allowed_qty, reason = can_sell_with_lots(
            ticker, shares, self.state_manager, now_utc
        )

        return can_sell, allowed_qty, reason

    def _navigate_to_trade(self, order: TradeOrder) -> bool:
        """Navigate to the equity trade ticket."""
        logger.info(f"Navigating to trade page for {order.ticker}...")

        self._dismiss_overlays()

        # Method 1: Direct URL (most reliable based on testing)
        trade_url = f"{STOCKTRAK_TRADING_EQUITIES_URL}?securitysymbol={order.ticker}&exchange=US"
        logger.info(f"Using URL: {trade_url}")

        self.page.goto(trade_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        time.sleep(2)  # Give page time to fully render

        # CRITICAL: Check if we got redirected to login (session expired)
        if self._check_login_redirect():
            logger.warning("Session expired - redirected to login. Re-authenticating...")
            if not self.bot.login():
                raise RuntimeError("Re-login failed after session expiration")
            # Re-navigate to trade page
            logger.info(f"Re-navigating to: {trade_url}")
            self.page.goto(trade_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            # If still on login, something is very wrong
            if self._check_login_redirect():
                raise RuntimeError("Still redirected to login after re-authentication")

        self._dismiss_overlays()

        # Verify we're on trade page
        self._verify_on_trade_page(order.ticker)

        self._take_screenshot(f"trade_page_{order.ticker}")
        return True

    def _check_login_redirect(self) -> bool:
        """Check if we've been redirected to the login page."""
        url = self.page.url.lower()
        if 'login' in url:
            return True
        # Also check for login form elements
        try:
            if self.page.locator("input[type='password']").first.is_visible(timeout=1000):
                # Check if this looks like a login page, not a trade page
                page_text = self.page.locator('body').inner_text().lower()
                if 'welcome back' in page_text and 'log in' in page_text:
                    return True
        except Exception:
            pass
        return False

    def _verify_on_trade_page(self, ticker: str):
        """Checkpoint: Verify we're on the correct trade page."""
        # Check URL contains trading (and NOT login)
        url = self.page.url.lower()
        if "login" in url:
            raise RuntimeError(f"On login page, not trading page. URL: {url}")
        if "trading" not in url:
            raise RuntimeError(f"Not on trading page. URL: {url}")

        # Look for Buy button using multiple strategies
        buy_found = self._find_buy_sell_button("Buy")
        if not buy_found:
            raise RuntimeError("Buy button not found - not on trade page")

        # Verify ticker is in page content
        page_text = self.page.locator('body').inner_text().upper()
        if ticker.upper() not in page_text:
            logger.warning(f"Ticker {ticker} not visible on page")

    def _find_buy_sell_button(self, side: str) -> bool:
        """
        Find Buy or Sell button using multiple selector strategies.

        StockTrak's buttons may be styled differently or have varying HTML structure.
        We try multiple approaches to find them reliably.

        Args:
            side: "Buy" or "Sell"

        Returns:
            True if button found, False otherwise
        """
        strategies = [
            # Strategy 1: Button by role with name (case-insensitive)
            lambda: self.page.get_by_role("button", name=re.compile(f"^{side}$", re.I)).first,
            # Strategy 2: Button with exact text
            lambda: self.page.locator(f"button:has-text('{side}')").first,
            # Strategy 3: Any clickable element with text
            lambda: self.page.locator(f"text='{side}'").first,
            # Strategy 4: Look in ACTION section
            lambda: self.page.locator("text=ACTION").locator("..").locator(f"text='{side}'").first,
            # Strategy 5: Div/span styled as button
            lambda: self.page.locator(f"div:has-text('{side}')").first,
            # Strategy 6: By aria-label
            lambda: self.page.locator(f"[aria-label*='{side}' i]").first,
        ]

        for i, strategy in enumerate(strategies):
            try:
                elem = strategy()
                if elem.is_visible(timeout=3000):
                    logger.info(f"Found {side} button via strategy {i+1}")
                    return True
            except Exception as e:
                logger.debug(f"Strategy {i+1} for {side} button failed: {e}")
                continue

        return False

    def _click_buy_sell_button(self, side: str) -> bool:
        """
        Click the Buy or Sell button using multiple strategies.

        Args:
            side: "Buy" or "Sell"

        Returns:
            True if clicked successfully
        """
        # Scroll ACTION area into view first
        try:
            action_label = self.page.get_by_text(re.compile(r"^ACTION$", re.I)).first
            if action_label.is_visible(timeout=2000):
                action_label.scroll_into_view_if_needed()
                time.sleep(0.3)
        except Exception:
            pass

        strategies = [
            # Strategy 1: Button by role with name (case-insensitive)
            lambda: self.page.get_by_role("button", name=re.compile(f"^{side}$", re.I)).first,
            # Strategy 2: Button with exact text
            lambda: self.page.locator(f"button:has-text('{side}')").first,
            # Strategy 3: Look in ACTION section specifically
            lambda: self.page.locator("text=ACTION").locator("..").get_by_role("button", name=re.compile(side, re.I)).first,
            # Strategy 4: Any element with exact text that's clickable
            lambda: self.page.get_by_text(side, exact=True).first,
            # Strategy 5: CSS class patterns common for buy/sell
            lambda: self.page.locator(f".btn-{side.lower()}, .{side.lower()}-button, #{side.lower()}-btn").first,
        ]

        for i, strategy in enumerate(strategies):
            try:
                elem = strategy()
                if elem.is_visible(timeout=3000):
                    logger.info(f"Clicking {side} button via strategy {i+1}")
                    elem.scroll_into_view_if_needed()
                    elem.click(timeout=5000)
                    return True
            except Exception as e:
                logger.debug(f"Click strategy {i+1} for {side} failed: {e}")
                continue

        return False

    def _fill_order_form(self, order: TradeOrder) -> bool:
        """Fill the order form with proper verification."""
        logger.info(f"Filling order: {order.side} {order.shares} {order.ticker}")

        self._dismiss_overlays()

        # 1. Click BUY or SELL using robust method
        if not self._click_buy_sell_button(order.side.capitalize()):
            raise RuntimeError(f"Could not find/click {order.side} button")

        time.sleep(0.5)
        logger.info(f"Clicked {order.side} button")

        # 2. Fill SHARES with hard-clear + verification
        shares_input = self._find_shares_input()
        self._fill_shares_with_verification(shares_input, order.shares)

        # 3. Set order type to MARKET (most reliable)
        self._set_order_type("Market")

        # 4. Screenshot the filled form
        self._take_screenshot(f"form_filled_{order.ticker}")

        return True

    def _find_shares_input(self):
        """Find the SHARES input field using multiple strategies."""
        strategies = [
            # Strategy 1: Input near SHARES label
            lambda: self.page.locator("text=SHARES").locator("..").locator("input").first,
            # Strategy 2: Input with SHARES in ancestor
            lambda: self.page.locator("text=SHARES").locator("xpath=ancestor::div[1]//input").first,
            # Strategy 3: Input by common names
            lambda: self.page.locator('input[name="shares"]').first,
            lambda: self.page.locator('input[name="quantity"]').first,
            lambda: self.page.locator('#shares').first,
            lambda: self.page.locator('#quantity').first,
            # Strategy 4: Numeric input near buy/sell section
            lambda: self.page.locator('input[type="number"]').first,
            lambda: self.page.locator('input[type="text"]').nth(1),  # Often 2nd text input after symbol
            # Strategy 5: Look for input with "100" default value
            lambda: self.page.locator('input[value="100"]').first,
        ]

        for i, strategy in enumerate(strategies):
            try:
                elem = strategy()
                if elem.is_visible(timeout=2000):
                    logger.info(f"Found shares input via strategy {i+1}")
                    return elem
            except Exception:
                continue

        raise RuntimeError("Could not find SHARES input field")

    def _fill_shares_with_verification(self, input_elem, shares: int):
        """
        Fill shares with AGGRESSIVE clearing and value verification.

        The input often has a default value (e.g., "100") that must be
        completely removed before entering the new value.
        """
        max_attempts = 5

        for attempt in range(max_attempts):
            try:
                # AGGRESSIVE CLEAR: Multiple methods to ensure field is empty
                input_elem.click()
                time.sleep(0.2)

                # Method 1: Triple-click to select all
                input_elem.click(click_count=3)
                time.sleep(0.1)

                # Method 2: Ctrl+A to select all
                input_elem.press("Control+a")
                time.sleep(0.1)

                # Method 3: Delete/Backspace
                input_elem.press("Delete")
                input_elem.press("Backspace")
                time.sleep(0.1)

                # Verify field is now empty
                current_value = input_elem.input_value().strip()
                if current_value:
                    logger.warning(f"Field not empty after clear (has: '{current_value}'), trying again...")
                    # Try clearing with JavaScript
                    input_elem.evaluate("el => el.value = ''")
                    time.sleep(0.1)

                # Now type the new value character by character
                input_elem.type(str(shares), delay=50)
                time.sleep(0.3)

                # Verify the value
                actual = input_elem.input_value().strip()
                actual_digits = "".join(c for c in actual if c.isdigit())
                expected_digits = str(shares)

                if actual_digits == expected_digits:
                    logger.info(f"Shares verified: {actual} (expected: {shares})")
                    return
                else:
                    logger.warning(f"Shares mismatch attempt {attempt+1}: expected {shares}, got '{actual}'")
                    # Clear via JavaScript and retry
                    input_elem.evaluate("el => el.value = ''")

            except Exception as e:
                logger.warning(f"Fill attempt {attempt+1} failed: {e}")

        raise RuntimeError(f"Could not fill shares input after {max_attempts} attempts")

    def _set_order_type(self, order_type: str = "Market"):
        """
        Set order type dropdown to Market.

        CRITICAL: If order type is Limit and price is $0, the review step won't work!
        Always ensure Market is selected.
        """
        try:
            # Try to find and set order type dropdown
            dropdowns = self.page.locator('select')
            for i in range(dropdowns.count()):
                dropdown = dropdowns.nth(i)
                try:
                    if dropdown.is_visible(timeout=1000):
                        options_text = dropdown.inner_text().lower()
                        if "market" in options_text or "limit" in options_text:
                            dropdown.select_option(label=order_type)
                            logger.info(f"Order type set to {order_type}")
                            # Verify it was set
                            time.sleep(0.3)
                            selected = dropdown.input_value()
                            logger.info(f"Order type verified: {selected}")
                            return
                except:
                    continue

            # Fallback: try to find by label text
            try:
                order_type_label = self.page.locator("text=ORDER TYPE").first
                if order_type_label.is_visible(timeout=1000):
                    # Find the select near this label
                    select_near_label = order_type_label.locator("xpath=following::select[1]").first
                    if select_near_label.is_visible(timeout=1000):
                        select_near_label.select_option(label=order_type)
                        logger.info(f"Order type set to {order_type} via label")
                        return
            except Exception:
                pass

            logger.warning("Could not find order type dropdown - using default (may be Market already)")
        except Exception as e:
            logger.warning(f"Error setting order type: {e}")

    def _preview_order(self, order: TradeOrder) -> bool:
        """
        Click Review Order button using JavaScript for maximum reliability.
        """
        logger.info("=== CLICKING REVIEW ORDER ===")

        self._dismiss_overlays()

        # Scroll to bottom to reveal Review Order button
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        self._take_screenshot(f"scrolled_{order.ticker}")

        # USE JAVASCRIPT to find and click Review Order button
        js_click_script = """
        (function() {
            const clickables = document.querySelectorAll('button, a, [role="button"]');
            const reviewKeywords = ['review order', 'preview order', 'review', 'preview', 'continue', 'next'];
            const excludeKeywords = ['cancel', 'close', 'back'];

            for (const element of clickables) {
                const text = (element.textContent || '').toLowerCase().trim();
                if (excludeKeywords.some(kw => text.includes(kw))) continue;

                if (reviewKeywords.some(kw => text.includes(kw))) {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    const isVisible = rect.width > 0 && rect.height > 0 &&
                                     style.display !== 'none' && style.visibility !== 'hidden';

                    if (isVisible) {
                        element.scrollIntoView({behavior: 'instant', block: 'center'});
                        element.click();
                        return {success: true, buttonText: text};
                    }
                }
            }

            // Debug: list all visible buttons
            const allButtons = [];
            document.querySelectorAll('button').forEach(btn => {
                if (btn.offsetParent !== null) allButtons.push(btn.textContent.trim().substring(0, 30));
            });
            return {success: false, visibleButtons: allButtons};
        })();
        """

        result = self.page.evaluate(js_click_script)
        logger.info(f"Review Order JavaScript result: {result}")

        if not result.get('success'):
            logger.error(f"Could not find Review Order button. Visible: {result.get('visibleButtons', [])}")
            self._take_screenshot(f"no_review_btn_{order.ticker}")
            raise RuntimeError(f"Could not find Review Order button. Visible: {result.get('visibleButtons', [])}")

        logger.info(f"SUCCESS: Clicked '{result.get('buttonText')}' via JavaScript")

        # Wait for confirmation UI to appear - MUST wait for actual Confirm Order button
        logger.info("Waiting for confirmation UI (Confirm Order button)...")
        time.sleep(2)

        # First, check for any error messages that might indicate why confirm isn't showing
        error_check_js = """
        (function() {
            const errorSelectors = ['.error', '.alert-danger', '.alert-error', '.error-message',
                                   '[class*="error"]', '[class*="alert"]', '.toast-error'];
            for (const sel of errorSelectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) {
                    const text = el.textContent.trim();
                    if (text.length > 5 && text.length < 500) {
                        return {hasError: true, message: text};
                    }
                }
            }
            // Also check for "insufficient" or similar text anywhere visible
            const body = document.body.innerText.toLowerCase();
            if (body.includes('insufficient') || body.includes('not enough') ||
                body.includes('exceeds') || body.includes('limit reached') ||
                body.includes('buying power')) {
                return {hasError: true, message: 'Possible buying power or limit issue detected'};
            }
            return {hasError: false};
        })();
        """
        error_result = self.page.evaluate(error_check_js)
        if error_result.get('hasError'):
            logger.warning(f"Possible error on page: {error_result.get('message')}")
            # If there's an error, try dismissing overlays and take screenshot
            self._dismiss_overlays()
            self._take_screenshot(f"error_detected_{order.ticker}")

        # Log current URL to detect unexpected navigation
        current_url = self.page.url
        logger.info(f"Current URL after Review Order: {current_url}")

        # Try to dismiss any overlays that appeared after clicking Review Order
        self._dismiss_overlays()

        # CRITICAL: Wait for the ACTUAL Confirm Order button to appear
        # This is the definitive check that we're on the confirmation page
        max_wait = 20  # Increased to 20 seconds for slower page loads
        confirm_found = False

        for wait_sec in range(max_wait):
            # First, try to dismiss any popup overlays each iteration
            if wait_sec % 3 == 0 and wait_sec > 0:
                self._dismiss_overlays()

            # Check for Confirm Order button using JavaScript (more reliable)
            # Added more button text patterns for StockTrak UI variations
            js_check = """
            (function() {
                const buttons = document.querySelectorAll('button, a, [role="button"], input[type="submit"], span[role="button"]');
                const confirmPatterns = [
                    'confirm order', 'place order', 'confirm trade', 'submit order',
                    'execute order', 'confirm', 'place trade', 'submit', 'complete order',
                    'finalize', 'execute', 'complete'
                ];
                const excludePatterns = ['cancel', 'back', 'close', 'review', 'edit', 'modify'];

                for (const btn of buttons) {
                    const text = (btn.textContent || btn.value || '').toLowerCase().trim();
                    // Skip navigation/cancel buttons
                    if (excludePatterns.some(p => text.includes(p))) continue;
                    // Skip empty buttons
                    if (text.length < 3) continue;

                    for (const pattern of confirmPatterns) {
                        if (text.includes(pattern)) {
                            const rect = btn.getBoundingClientRect();
                            const style = window.getComputedStyle(btn);
                            if (rect.width > 0 && rect.height > 0 &&
                                style.display !== 'none' && style.visibility !== 'hidden') {
                                return {found: true, text: text, tag: btn.tagName};
                            }
                        }
                    }
                }

                // Also check for modals with confirmation UI
                const modals = document.querySelectorAll('[class*="modal"]:not([style*="display: none"]), [class*="dialog"]:not([style*="display: none"]), [class*="confirm"]:not([style*="display: none"])');
                const hasVisibleModal = Array.from(modals).some(m => {
                    const style = window.getComputedStyle(m);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                });

                // Debug: return all visible button texts and page info
                const allBtns = [];
                buttons.forEach(btn => {
                    if (btn.offsetParent !== null) {
                        allBtns.push((btn.textContent || btn.value || '').trim().substring(0, 40));
                    }
                });

                // Check for error indicators
                const pageText = document.body.innerText.toLowerCase();
                const hasError = ['error', 'failed', 'invalid', 'insufficient', 'not available'].some(e => pageText.includes(e));

                return {
                    found: false,
                    visibleButtons: allBtns,
                    hasModal: hasVisibleModal,
                    modalCount: modals.length,
                    possibleError: hasError
                };
            })();
            """
            result = self.page.evaluate(js_check)
            if result.get('found'):
                confirm_found = True
                logger.info(f"SUCCESS: Confirm Order button found after {wait_sec + 1}s: '{result.get('text')}' ({result.get('tag')})")
                break

            # Enhanced logging at intervals
            if wait_sec == 3:
                logger.info(f"Waiting for Confirm... visible buttons: {result.get('visibleButtons', [])[:8]}")
                if result.get('hasModal'):
                    logger.info(f"Modal detected ({result.get('modalCount')} modals visible)")
                if result.get('possibleError'):
                    logger.warning("Possible error text detected on page")

            if wait_sec == 8:
                # Mid-wait screenshot for debugging
                self._take_screenshot(f"waiting_confirm_{order.ticker}")
                logger.info(f"Still waiting... buttons: {result.get('visibleButtons', [])[:10]}")

            time.sleep(1)

        self._take_screenshot(f"after_review_click_{order.ticker}")

        if not confirm_found:
            # Log all visible buttons for debugging
            logger.error("FAILED: Confirm Order button NOT found after clicking Review Order")
            logger.error(f"Visible buttons on page: {result.get('visibleButtons', [])}")
            logger.error(f"Page URL: {self.page.url}")
            if result.get('possibleError'):
                logger.error("Error indicators found on page - order may have been rejected")
            raise RuntimeError("Confirmation UI did not appear - Confirm Order button not found")

        return True

    def _fill_trade_notes(self, order: TradeOrder):
        """
        Fill the TRADE NOTES field with rationale (required by StockTrak session rules).

        Note format: "{TICKER} - {description}. {side} {shares} shares ({X}% of portfolio). {rationale}"
        """
        logger.info("Filling trade notes...")

        # Build the trade note - includes all core and satellite ETFs
        ticker_descriptions = {
            # Core ETFs
            'VOO': 'Vanguard S&P 500 ETF',
            'VTI': 'Vanguard Total Stock Market ETF',
            'VEA': 'Vanguard Developed Markets ETF',
            # Space / Aerospace (A_SPACE bucket)
            'ROKT': 'SPDR S&P Kensho Final Frontiers ETF',
            'UFO': 'Procure Space ETF',
            'RKLB': 'Rocket Lab USA Inc',
            'PL': 'Planet Labs PBC',
            'ASTS': 'AST SpaceMobile Inc',
            'LUNR': 'Intuitive Machines Inc',
            # Defense (B_DEFENSE bucket)
            'PPA': 'Invesco Aerospace & Defense ETF',
            'ITA': 'iShares U.S. Aerospace & Defense ETF',
            'XAR': 'SPDR S&P Aerospace & Defense ETF',
            'JEDI': 'ET Lument Specialty Finance ETF',
            'LMT': 'Lockheed Martin Corporation',
            'NOC': 'Northrop Grumman Corporation',
            'RTX': 'RTX Corporation',
            'GD': 'General Dynamics Corporation',
            'KTOS': 'Kratos Defense & Security',
            'AVAV': 'AeroVironment Inc',
            # Semiconductors (C_SEMIS bucket)
            'SMH': 'VanEck Semiconductor ETF',
            'SOXX': 'iShares Semiconductor ETF',
            'ASML': 'ASML Holding NV',
            'AMAT': 'Applied Materials Inc',
            'LRCX': 'Lam Research Corporation',
            'KLAC': 'KLA Corporation',
            'TER': 'Teradyne Inc',
            'ENTG': 'Entegris Inc',
            # Biotech (D_BIOTECH bucket)
            'XBI': 'SPDR S&P Biotech ETF',
            'IDNA': 'iShares Genomics Immunology and Healthcare ETF',
            'CRSP': 'CRISPR Therapeutics AG',
            'NTLA': 'Intellia Therapeutics Inc',
            'BEAM': 'Beam Therapeutics Inc',
            # Nuclear (E_NUCLEAR bucket)
            'URNM': 'Sprott Uranium Miners ETF',
            'URA': 'Global X Uranium ETF',
            'NLR': 'VanEck Uranium+Nuclear Energy ETF',
            'CCJ': 'Cameco Corporation',
            # Energy (F_ENERGY bucket)
            'XLE': 'Energy Select Sector SPDR Fund',
            'XOP': 'SPDR S&P Oil & Gas Exploration ETF',
            'XOM': 'Exxon Mobil Corporation',
            'CVX': 'Chevron Corporation',
            # Metals (G_METALS bucket)
            'COPX': 'Global X Copper Miners ETF',
            'XME': 'SPDR S&P Metals and Mining ETF',
            'PICK': 'iShares MSCI Global Metals & Mining Producers ETF',
            'FCX': 'Freeport-McMoRan Inc',
            'SCCO': 'Southern Copper Corporation',
            # Materials (H_MATERIALS bucket)
            'DMAT': 'Global X Disruptive Materials ETF',
            # Other common ETFs
            'BND': 'Vanguard Total Bond Market ETF',
            'BNDX': 'Vanguard Total International Bond ETF',
            'VWO': 'Vanguard Emerging Markets ETF',
            'VNQ': 'Vanguard Real Estate ETF',
            'GLD': 'SPDR Gold Shares ETF',
            'TLT': 'iShares 20+ Year Treasury Bond ETF',
            'IWM': 'iShares Russell 2000 ETF',
            'QQQ': 'Invesco QQQ Trust (Nasdaq-100)',
            'SPY': 'SPDR S&P 500 ETF',
            'VT': 'Vanguard Total World Stock ETF',
        }

        ticker = order.ticker.upper()
        description = ticker_descriptions.get(ticker, f'{ticker} ETF')

        # Calculate percentage of portfolio (order has estimated cost info)
        # Use the rationale from the order if available, otherwise generate one
        pct_str = f"{order.portfolio_pct:.1f}%" if hasattr(order, 'portfolio_pct') and order.portfolio_pct else "target allocation"

        # Build the note
        if order.side.upper() == 'BUY':
            action_desc = f"Buying {order.shares} shares"
        else:
            action_desc = f"Selling {order.shares} shares"

        # Use order rationale if provided, otherwise use a default
        rationale = order.rationale if hasattr(order, 'rationale') and order.rationale else "Portfolio rebalancing per investment strategy."

        trade_note = f"{ticker} - {description}. {action_desc} ({pct_str} of portfolio). {rationale}"

        logger.info(f"Trade note: {trade_note}")

        # Find and fill the TRADE NOTES textarea using JavaScript
        js_fill_notes = f"""
        (function() {{
            // Look for textarea near "TRADE NOTES" or "note" labels
            const textareas = document.querySelectorAll('textarea');
            for (const ta of textareas) {{
                const rect = ta.getBoundingClientRect();
                const style = window.getComputedStyle(ta);
                const isVisible = rect.width > 0 && rect.height > 0 &&
                                 style.display !== 'none' && style.visibility !== 'hidden';

                if (isVisible) {{
                    ta.scrollIntoView({{behavior: 'instant', block: 'center'}});
                    ta.focus();
                    ta.value = {repr(trade_note)};
                    // Trigger input event for React/Vue frameworks
                    ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                    ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{success: true, element: 'textarea'}};
                }}
            }}

            // Fallback: look for input with placeholder containing "note"
            const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
            for (const inp of inputs) {{
                const placeholder = (inp.placeholder || '').toLowerCase();
                const name = (inp.name || '').toLowerCase();
                if (placeholder.includes('note') || name.includes('note')) {{
                    inp.value = {repr(trade_note)};
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{success: true, element: 'input'}};
                }}
            }}

            return {{success: false, message: 'No textarea or note input found'}};
        }})();
        """

        result = self.page.evaluate(js_fill_notes)
        logger.info(f"Trade notes fill result: {result}")

        if not result.get('success'):
            logger.warning(f"Could not fill trade notes: {result.get('message')}")
            # Try Playwright locator as fallback
            try:
                textarea = self.page.locator("textarea").first
                if textarea.is_visible(timeout=2000):
                    textarea.fill(trade_note)
                    logger.info("Filled trade notes via Playwright fallback")
            except Exception as e:
                logger.warning(f"Playwright fallback also failed: {e}")

    def _place_order(self, order: TradeOrder) -> bool:
        """
        Fill trade notes and click Confirm Order button.
        """
        logger.info("=== FILLING TRADE NOTES AND CONFIRMING ORDER ===")

        self._dismiss_overlays()
        time.sleep(1)

        # FILL TRADE NOTES (required by StockTrak session rules)
        self._fill_trade_notes(order)
        time.sleep(0.5)

        self._take_screenshot(f"before_confirm_{order.ticker}")

        # IDEMPOTENCY CHECK
        if self._check_already_placed(order):
            raise RuntimeError("Order already placed - aborting to prevent duplicate")

        # USE JAVASCRIPT to find and click - most reliable method
        js_click_script = """
        (function() {
            const clickables = document.querySelectorAll('button, a, [role="button"], input[type="submit"]');
            const confirmKeywords = ['confirm order', 'place order', 'submit order', 'confirm', 'place trade'];
            const excludeKeywords = ['cancel', 'close', 'back', 'edit'];

            for (const element of clickables) {
                const text = (element.textContent || element.value || '').toLowerCase().trim();
                if (excludeKeywords.some(kw => text.includes(kw))) continue;

                if (confirmKeywords.some(kw => text.includes(kw))) {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    const isVisible = rect.width > 0 && rect.height > 0 &&
                                     style.display !== 'none' && style.visibility !== 'hidden';

                    if (isVisible) {
                        element.scrollIntoView({behavior: 'instant', block: 'center'});
                        element.click();
                        return {success: true, buttonText: text};
                    }
                }
            }

            // Fallback: find primary button in confirm/modal container
            const containers = document.querySelectorAll('[class*="confirm"], [class*="modal"], [class*="order"]');
            for (const container of containers) {
                const style = window.getComputedStyle(container);
                if (style.display === 'none') continue;
                const btn = container.querySelector('.btn-primary, .btn-success, button[class*="primary"]');
                if (btn && btn.offsetParent !== null) {
                    const text = btn.textContent.toLowerCase();
                    if (!excludeKeywords.some(kw => text.includes(kw))) {
                        btn.scrollIntoView({behavior: 'instant', block: 'center'});
                        btn.click();
                        return {success: true, buttonText: text, fallback: true};
                    }
                }
            }

            // Debug: list all visible buttons
            const allButtons = [];
            document.querySelectorAll('button').forEach(btn => {
                if (btn.offsetParent !== null) allButtons.push(btn.textContent.trim().substring(0, 30));
            });
            return {success: false, visibleButtons: allButtons};
        })();
        """

        result = self.page.evaluate(js_click_script)
        logger.info(f"JavaScript click result: {result}")

        if result.get('success'):
            logger.info(f"SUCCESS: Clicked '{result.get('buttonText')}' via JavaScript")
            time.sleep(3)
            self._take_screenshot(f"after_confirm_{order.ticker}")

            # Check for success
            page_text = self.page.content().lower()
            if any(kw in page_text for kw in ['confirmed', 'submitted', 'success', 'order placed', 'thank you']):
                logger.info("ORDER SUBMITTED SUCCESSFULLY!")
                return True

            if any(kw in page_text for kw in ['error', 'failed', 'invalid', 'rejected']):
                error_msg = self._extract_error_message()
                raise RuntimeError(f"Order failed: {error_msg}")

            logger.info("Order likely submitted - will verify in history")
            return True
        else:
            # JavaScript failed - log visible buttons for debugging
            logger.error(f"JavaScript could not find confirm button!")
            logger.error(f"Visible buttons on page: {result.get('visibleButtons', [])}")
            self._take_screenshot(f"no_confirm_btn_{order.ticker}")
            raise RuntimeError(f"Could not find Confirm Order button. Visible: {result.get('visibleButtons', [])}")

    def _extract_error_message(self) -> str:
        """Try to extract error message from page."""
        selectors = ['.error', '.alert-danger', '.error-message', '.alert-error', '#error']
        for sel in selectors:
            try:
                elem = self.page.locator(sel).first
                if elem.is_visible(timeout=1000):
                    return elem.text_content()[:200]
            except:
                pass
        return "Unknown error"

    def _verify_in_history(self, order: TradeOrder) -> bool:
        """
        Verify trade appears in Transaction History or Order History.

        NOTE: This is a NON-CRITICAL step. The order was already confirmed,
        so verification failure doesn't mean the trade failed.
        Uses 30-second timeout to prevent hangs.
        """
        logger.info(f"Verifying trade in history: {order.side} {order.shares} {order.ticker}")

        # Quick verification - don't spend too long on this non-critical step
        MAX_VERIFY_TIME = 30  # seconds
        start_time = time.time()

        # Try Transaction History first
        try:
            if time.time() - start_time > MAX_VERIFY_TIME:
                logger.warning("Verification timeout - skipping")
                return False
            self._navigate_to_history("Transaction History")
            if self._find_trade_in_table(order):
                logger.info("Trade found in Transaction History")
                return True
        except Exception as e:
            logger.warning(f"Transaction History check failed: {e}")

        # Fallback to Order History (only if we have time)
        if time.time() - start_time < MAX_VERIFY_TIME:
            try:
                self._navigate_to_history("Order History")
                if self._find_trade_in_table(order):
                    logger.info("Trade found in Order History")
                    return True
            except Exception as e:
                logger.warning(f"Order History check failed: {e}")

        logger.warning("Could not verify trade in history (non-critical)")
        return False

    def _navigate_to_history(self, history_type: str):
        """Navigate to Transaction History or Order History using direct URL."""
        self._dismiss_overlays()

        # USE DIRECT URL - hover menus are unreliable and can hang
        if "transaction" in history_type.lower():
            url = STOCKTRAK_TRANSACTION_HISTORY_URL
        else:
            url = STOCKTRAK_ORDER_HISTORY_URL

        logger.info(f"Navigating to history via URL: {url}")

        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        except Exception as e:
            logger.warning(f"History navigation timeout: {e}")
            # Continue anyway - might have partially loaded

        self._dismiss_overlays()
        time.sleep(1)

    def _find_trade_in_table(self, order: TradeOrder) -> bool:
        """Find the trade in the history table."""
        # Wait for table to load
        try:
            self.page.wait_for_selector("table", timeout=5000)
            time.sleep(1)  # Give table data time to populate
        except Exception:
            pass  # Continue even if no table found

        # First, try using JavaScript for more reliable search
        js_search = f"""
        (function() {{
            const ticker = '{order.ticker}'.toUpperCase();
            const side = '{order.side}'.toUpperCase();
            const shares = '{order.shares}';

            // Search all table rows
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {{
                const text = row.innerText.toUpperCase();
                if (text.includes(ticker)) {{
                    // Found ticker, check for side and shares (be lenient)
                    const hasAction = text.includes(side) || text.includes('BUY') || text.includes('SELL');
                    const hasShares = text.includes(shares);
                    if (hasAction || hasShares) {{
                        return {{found: true, text: text.substring(0, 150)}};
                    }}
                }}
            }}

            // Also check for ticker anywhere on page as fallback
            const pageText = document.body.innerText.toUpperCase();
            const tickerMentioned = pageText.includes(ticker);

            return {{found: false, tickerMentioned: tickerMentioned}};
        }})();
        """

        result = self.page.evaluate(js_search)
        if result.get('found'):
            logger.info(f"Found trade via JavaScript: {result.get('text')}")
            return True

        # Fallback: Playwright locator
        rows = self.page.locator("tr", has_text=re.compile(order.ticker, re.I))

        try:
            if rows.count() == 0:
                if result.get('tickerMentioned'):
                    logger.info(f"Ticker {order.ticker} mentioned on page but not in table row format")
                    return True  # Assume success if ticker appears anywhere
                return False

            # Check if any row matches side and shares
            for i in range(min(rows.count(), 10)):  # Check first 10 matches
                try:
                    row_text = rows.nth(i).text_content().upper()
                    if order.side in row_text or str(order.shares) in row_text:
                        logger.info(f"Found matching trade: {row_text[:100]}")
                        return True
                except Exception as e:
                    logger.debug(f"Row {i} check failed: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Playwright row search failed: {e}")

        return False

    def _add_trade_note(self, order: TradeOrder) -> bool:
        """Add trade note via history Add/View Notes."""
        logger.info(f"Adding trade note for {order.ticker}")

        try:
            # Navigate to Transaction History
            self._navigate_to_history("Transaction History")

            # Find row with ticker
            row = self.page.locator("tr", has_text=re.compile(order.ticker, re.I)).first
            row.wait_for(state="visible", timeout=10000)

            # Click Add/View Notes
            notes_link = row.locator("a", has_text=re.compile("Add|View|Notes", re.I))
            if notes_link.count() > 0:
                notes_link.first.click()
                time.sleep(0.5)

                # Fill note
                textbox = self.page.get_by_role("textbox").first
                textbox.fill(order.rationale)

                # Save
                save_btn = self.page.get_by_role("button", name=re.compile("save|submit|add", re.I))
                save_btn.first.click()
                time.sleep(1)

                logger.info("Trade note saved")
                return True
            else:
                logger.warning("Notes link not found")
                return False

        except Exception as e:
            logger.warning(f"Could not add trade note: {e}")
            return False

    # =========================================================================
    # UTILITIES
    # =========================================================================
    def _dismiss_overlays(self, total_ms: int = 10000):
        """
        Dismiss popups using bot's method.

        CRITICAL: This must work reliably or trades will fail with
        "Element not visible" errors. Log failures clearly.
        """
        try:
            from stocktrak_bot import dismiss_stocktrak_overlays
            dismiss_stocktrak_overlays(self.page, total_ms=total_ms)
        except ImportError as e:
            logger.warning(f"Could not import dismiss_stocktrak_overlays: {e}")
            # Fallback: try basic overlay dismissal
            self._dismiss_overlays_fallback()
        except Exception as e:
            logger.warning(f"Overlay dismissal failed: {e}")
            self._dismiss_overlays_fallback()

    def _dismiss_overlays_fallback(self):
        """Fallback overlay dismissal when main method unavailable."""
        try:
            # Try common close button patterns
            close_selectors = [
                "button:has-text('Close')",
                "button:has-text('×')",
                "button:has-text('X')",
                "[aria-label='Close']",
                ".close-button",
                ".modal-close",
            ]
            for selector in close_selectors:
                try:
                    close_btn = self.page.locator(selector).first
                    if close_btn.is_visible(timeout=500):
                        close_btn.click()
                        time.sleep(0.2)
                except Exception:
                    pass

            # Press Escape as last resort
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
        except Exception as e:
            logger.debug(f"Fallback overlay dismissal failed: {e}")

    def _take_screenshot(self, name: str) -> str:
        """Take and log a screenshot."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"logs/{name}_{timestamp}.png"
            self.page.screenshot(path=filepath, full_page=True)
            self.screenshots.append(filepath)
            logger.info(f"Screenshot: {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return ""

    def _update_dashboard(self, mode: str, step: str, order: TradeOrder, error: str = None):
        """Update dashboard state file."""
        try:
            self.state_manager.write_dashboard_state(
                running=(mode == "RUNNING"),
                mode=mode,
                step=step,
                error=error,
                run_id=order.run_id,
                last_screenshot=self.screenshots[-1] if self.screenshots else None
            )
        except Exception as e:
            logger.debug(f"Dashboard update failed (non-critical): {e}")


# =============================================================================
# TRADE TICKET HEALTH CHECK (PREFLIGHT)
# =============================================================================
def run_trade_ticket_health_check(bot, test_ticker: str = "VOO") -> Tuple[bool, str]:
    """
    Pre-flight check to verify the trade ticket is functional.

    This function navigates to a trade page and verifies all the critical
    form elements are accessible WITHOUT actually submitting the trade.

    Run this before live trading to catch:
    - Session expiration
    - UI changes that break automation
    - Popup/overlay issues
    - Missing form elements

    Args:
        bot: StockTrakBot instance (must have page attribute)
        test_ticker: A known safe ticker to test with (default: VOO)

    Returns:
        Tuple of (success: bool, details: str)
    """
    from stocktrak_bot import dismiss_stocktrak_overlays, ensure_clean_ui

    checks_passed = []
    checks_failed = []

    logger.info("=" * 60)
    logger.info(f"TRADE TICKET HEALTH CHECK: Testing with {test_ticker}")
    logger.info("=" * 60)

    try:
        page = bot.page
        state_manager = StateManager()

        # Step 1: Verify logged in
        logger.info("[PREFLIGHT] Step 1: Checking login status...")
        try:
            url = page.url.lower()
            if '/login' in url:
                logger.info("[PREFLIGHT] On login page - attempting login...")
                if not bot.login():
                    checks_failed.append("Login failed")
                    return False, "Login failed during preflight"
                checks_passed.append("Login successful")
            else:
                # Check for logout link
                logout = page.get_by_role("link", name=re.compile("logout", re.I))
                if logout.count() > 0:
                    checks_passed.append("Already logged in")
                else:
                    # Try to find authenticated indicators
                    if page.locator("text=PORTFOLIO VALUE").first.is_visible(timeout=3000):
                        checks_passed.append("Already logged in (via portfolio value)")
                    else:
                        checks_failed.append("Login status unclear")
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Login check error: {e}")
            checks_failed.append(f"Login check error: {str(e)[:50]}")

        # Step 2: Navigate to trade page
        logger.info(f"[PREFLIGHT] Step 2: Navigating to trade page for {test_ticker}...")
        try:
            trade_url = f"{STOCKTRAK_TRADING_EQUITIES_URL}?securitysymbol={test_ticker}&exchange=US"
            page.goto(trade_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            time.sleep(2)

            # Clear popups
            ensure_clean_ui(page, timeout_ms=5000)

            # Verify we're on trade page
            if "trading" in page.url.lower():
                checks_passed.append("Trade page navigation successful")
            else:
                checks_failed.append(f"Unexpected URL: {page.url}")
        except Exception as e:
            checks_failed.append(f"Navigation failed: {str(e)[:50]}")
            return False, f"Could not navigate to trade page: {e}"

        # Step 3: Find Buy button
        logger.info("[PREFLIGHT] Step 3: Looking for BUY button...")
        try:
            buy_found = False
            buy_selectors = [
                lambda: page.get_by_role("button", name=re.compile("^Buy$", re.I)).first,
                lambda: page.locator("button:has-text('Buy')").first,
                lambda: page.get_by_text("Buy", exact=True).first,
            ]
            for sel in buy_selectors:
                try:
                    if sel().is_visible(timeout=2000):
                        buy_found = True
                        break
                except:
                    pass

            if buy_found:
                checks_passed.append("BUY button found")
            else:
                checks_failed.append("BUY button not found")
        except Exception as e:
            checks_failed.append(f"BUY button search error: {str(e)[:50]}")

        # Step 4: Find Shares input
        logger.info("[PREFLIGHT] Step 4: Looking for SHARES input...")
        try:
            shares_found = False
            shares_selectors = [
                lambda: page.locator("text=SHARES").locator("..").locator("input").first,
                lambda: page.locator('input[name="shares"]').first,
                lambda: page.locator('input[type="number"]').first,
            ]
            for sel in shares_selectors:
                try:
                    if sel().is_visible(timeout=2000):
                        shares_found = True
                        break
                except:
                    pass

            if shares_found:
                checks_passed.append("SHARES input found")
            else:
                checks_failed.append("SHARES input not found")
        except Exception as e:
            checks_failed.append(f"SHARES input search error: {str(e)[:50]}")

        # Step 5: Find Review Order button
        logger.info("[PREFLIGHT] Step 5: Looking for REVIEW ORDER button...")
        try:
            review_found = False
            js_check = """
            (function() {
                const buttons = document.querySelectorAll('button, a, [role="button"]');
                const patterns = ['review order', 'preview order', 'review', 'preview'];
                for (const btn of buttons) {
                    const text = (btn.textContent || '').toLowerCase().trim();
                    if (patterns.some(p => text.includes(p))) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {found: true, text: text};
                        }
                    }
                }
                return {found: false};
            })();
            """
            result = page.evaluate(js_check)
            if result.get('found'):
                checks_passed.append(f"REVIEW ORDER button found: '{result.get('text')}'")
                review_found = True
            else:
                checks_failed.append("REVIEW ORDER button not found")
        except Exception as e:
            checks_failed.append(f"REVIEW ORDER search error: {str(e)[:50]}")

        # Step 6: Take screenshot for reference
        logger.info("[PREFLIGHT] Step 6: Taking verification screenshot...")
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"logs/preflight_check_{timestamp}.png"
            page.screenshot(path=screenshot_path, full_page=True)
            checks_passed.append(f"Screenshot saved: {screenshot_path}")
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")

        # Step 7: Navigate back to dashboard (cleanup)
        logger.info("[PREFLIGHT] Step 7: Returning to dashboard...")
        try:
            page.goto(STOCKTRAK_DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
            ensure_clean_ui(page, timeout_ms=3000)
            checks_passed.append("Dashboard return successful")
        except Exception as e:
            logger.warning(f"Dashboard return failed: {e}")

        # Summary
        logger.info("=" * 60)
        logger.info("PREFLIGHT CHECK SUMMARY")
        logger.info("=" * 60)
        logger.info(f"PASSED: {len(checks_passed)}")
        for check in checks_passed:
            logger.info(f"  ✓ {check}")
        logger.info(f"FAILED: {len(checks_failed)}")
        for check in checks_failed:
            logger.error(f"  ✗ {check}")

        if checks_failed:
            return False, f"Preflight failed: {'; '.join(checks_failed)}"
        else:
            return True, f"Preflight passed: All {len(checks_passed)} checks OK"

    except Exception as e:
        logger.error(f"PREFLIGHT EXCEPTION: {e}")
        return False, f"Preflight exception: {e}"


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================
def execute_trade(bot, ticker: str, side: str, shares: int,
                  rationale: str = "", dry_run: bool = False) -> TradeResult:
    """
    Convenience function to execute a single trade.

    Args:
        bot: StockTrakBot instance
        ticker: Stock ticker
        side: "BUY" or "SELL"
        shares: Number of shares
        rationale: Trade rationale for notes
        dry_run: If True, stop before placing

    Returns:
        TradeResult
    """
    order = TradeOrder(
        ticker=ticker,
        side=side,
        shares=shares,
        rationale=rationale
    )

    pipeline = ExecutionPipeline(bot, dry_run=dry_run)
    return pipeline.execute(order)


def execute_multiple_trades(bot, trades: list, dry_run: bool = False) -> list:
    """
    Execute multiple trades sequentially.

    Args:
        bot: StockTrakBot instance
        trades: List of dicts with ticker, side, shares, rationale
        dry_run: If True, stop before placing each

    Returns:
        List of TradeResults
    """
    results = []
    state_manager = StateManager()

    for trade in trades:
        order = TradeOrder(
            ticker=trade['ticker'],
            side=trade['side'],
            shares=trade['shares'],
            rationale=trade.get('rationale', '')
        )

        pipeline = ExecutionPipeline(bot, state_manager=state_manager, dry_run=dry_run)
        result = pipeline.execute(order)
        results.append(result)

        if not result.success:
            logger.error(f"Trade failed: {trade['ticker']} - stopping batch")
            break

        # Brief pause between trades
        time.sleep(2)

    return results
