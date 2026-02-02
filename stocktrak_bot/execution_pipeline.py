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

logger = logging.getLogger('stocktrak_bot.execution_pipeline')


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

            # STEP 6: Place order
            self._run_step("place", lambda: self._place_order(order), order)
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

            logger.info(f"EXECUTION PIPELINE COMPLETED: {order.side} {order.shares} {order.ticker}")

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
                            "https://app.stocktrak.com/dashboard/standard",
                            wait_until="domcontentloaded",
                            timeout=60000
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

    # =========================================================================
    # PIPELINE STEPS
    # =========================================================================
    def _verify_logged_in(self) -> bool:
        """Verify we're logged in to StockTrak."""
        logger.info("Verifying login status...")

        # Check for logged-in indicators
        indicators = [
            "text=Portfolio Simulation",
            "text=My Portfolio",
            "text=Trading",
            "text=Logout",
        ]

        for indicator in indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=5000):
                    logger.info(f"Login verified via: {indicator}")
                    return True
            except Exception:
                # Expected to fail for most indicators - continue checking others
                continue

        # Not logged in - try to login
        logger.info("Not logged in - attempting login...")
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

    def _navigate_to_trade(self, order: TradeOrder) -> bool:
        """Navigate to the equity trade ticket."""
        logger.info(f"Navigating to trade page for {order.ticker}...")

        self._dismiss_overlays()

        # Method 1: Direct URL (most reliable based on testing)
        trade_url = f"https://app.stocktrak.com/trading/equities?securitysymbol={order.ticker}&exchange=US"
        logger.info(f"Using URL: {trade_url}")

        self.page.goto(trade_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)  # Give page time to fully render
        self._dismiss_overlays()

        # Verify we're on trade page
        self._verify_on_trade_page(order.ticker)

        self._take_screenshot(f"trade_page_{order.ticker}")
        return True

    def _verify_on_trade_page(self, ticker: str):
        """Checkpoint: Verify we're on the correct trade page."""
        # Check URL contains trading
        url = self.page.url.lower()
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
        """Set order type dropdown."""
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
                            return
                except:
                    continue
            logger.warning("Could not find order type dropdown - using default")
        except Exception as e:
            logger.warning(f"Error setting order type: {e}")

    def _preview_order(self, order: TradeOrder) -> bool:
        """Click Preview Order and verify preview page loads."""
        logger.info("Clicking Preview Order...")

        self._dismiss_overlays()

        # Find and click Preview Order button
        preview_btn = self.page.locator("button:has-text('Review Order'), button:has-text('Preview Order'), button:has-text('Preview')")
        preview_btn.first.wait_for(state="visible", timeout=15000)
        preview_btn.first.click()

        # Wait for preview to load
        self.page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(2)
        self._dismiss_overlays()

        # Verify preview page shows order details
        page_text = self.page.content().lower()
        if order.ticker.lower() not in page_text:
            logger.warning("Ticker not visible in preview")

        # Must see Place Order button
        try:
            self.page.wait_for_selector(
                "button:has-text('Place Order'), button:has-text('Submit'), button:has-text('Confirm')",
                timeout=15000
            )
        except:
            raise RuntimeError("Place Order button not found after preview")

        self._take_screenshot(f"preview_{order.ticker}")
        logger.info("Preview verified - Place Order button visible")
        return True

    def _place_order(self, order: TradeOrder) -> bool:
        """Click Place Order and verify submission."""
        logger.info("Clicking Place Order...")

        self._dismiss_overlays()

        # IDEMPOTENCY: Check one more time before placing
        if self._check_already_placed(order):
            raise RuntimeError("Order already placed - aborting to prevent duplicate")

        # Find and click Place Order
        place_btn = self.page.locator("button:has-text('Place Order'), button:has-text('Submit Order'), button:has-text('Confirm')")
        place_btn.first.wait_for(state="visible", timeout=15000)
        place_btn.first.click()

        # Wait for submission
        self.page.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(3)
        self._dismiss_overlays()

        self._take_screenshot(f"after_place_{order.ticker}")

        # Check for confirmation keywords
        page_text = self.page.content().lower()
        success_keywords = ['confirmed', 'submitted', 'success', 'order placed', 'order received', 'thank you']
        error_keywords = ['error', 'failed', 'invalid', 'rejected', 'insufficient', 'cannot']

        has_success = any(kw in page_text for kw in success_keywords)
        has_error = any(kw in page_text for kw in error_keywords)

        if has_error and not has_success:
            # Try to extract error message
            error_msg = self._extract_error_message()
            raise RuntimeError(f"Order placement failed: {error_msg}")

        if has_success:
            logger.info("Order submission confirmed via page content")
        else:
            logger.warning("No clear confirmation - will verify in history")

        return True

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
        """Verify trade appears in Transaction History or Order History."""
        logger.info(f"Verifying trade in history: {order.side} {order.shares} {order.ticker}")

        # Try Transaction History first
        try:
            self._navigate_to_history("Transaction History")
            if self._find_trade_in_table(order):
                logger.info("Trade found in Transaction History")
                return True
        except Exception as e:
            logger.warning(f"Transaction History check failed: {e}")

        # Fallback to Order History
        try:
            self._navigate_to_history("Order History")
            if self._find_trade_in_table(order):
                logger.info("Trade found in Order History")
                return True
        except Exception as e:
            logger.warning(f"Order History check failed: {e}")

        logger.warning("Could not verify trade in history")
        return False

    def _navigate_to_history(self, history_type: str):
        """Navigate to Transaction History or Order History."""
        self._dismiss_overlays()

        # Hover My Portfolio to trigger dropdown
        try:
            portfolio_link = self.page.get_by_role("link", name=re.compile("My Portfolio", re.I))
            portfolio_link.hover()
            time.sleep(0.3)
        except Exception as e:
            # Menu might not need hover - continue to direct click
            logger.debug(f"Portfolio hover failed (may be OK): {e}")

        # Click history link
        history_link = self.page.get_by_role("link", name=re.compile(history_type, re.I))
        history_link.click()
        self.page.wait_for_load_state("networkidle", timeout=30000)
        self._dismiss_overlays()
        time.sleep(1)

    def _find_trade_in_table(self, order: TradeOrder) -> bool:
        """Find the trade in the history table."""
        # Look for ticker in table
        rows = self.page.locator("tr", has_text=re.compile(order.ticker, re.I))

        if rows.count() == 0:
            return False

        # Check if any row matches side and shares
        for i in range(min(rows.count(), 10)):  # Check first 10 matches
            try:
                row_text = rows.nth(i).text_content().upper()
                if order.side in row_text and str(order.shares) in row_text:
                    logger.info(f"Found matching trade: {row_text[:100]}")
                    return True
            except Exception as e:
                logger.debug(f"Row {i} check failed: {e}")
                continue

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
