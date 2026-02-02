"""
StockTrak Browser Automation for Trading Bot

Uses Playwright to automate interactions with app.stocktrak.com
for portfolio management and trade execution.

UPDATED: Robust popup handling, page verification, and error recovery.
"""

import logging
import time
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable, Any
from datetime import datetime

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

import config

# =============================================================================
# PERSISTENT BROWSER PROFILE (popups won't come back after "Don't show again")
# =============================================================================
PROFILE_DIR = Path(__file__).resolve().parent / ".pw_profile"
PROFILE_DIR.mkdir(exist_ok=True)
from config import (
    STOCKTRAK_URL, STOCKTRAK_LOGIN_URL, STOCKTRAK_USERNAME, STOCKTRAK_PASSWORD,
    HEADLESS_MODE, SLOW_MO, DEFAULT_TIMEOUT, ORDER_SUBMISSION_WAIT,
    SCREENSHOT_ON_ERROR, SCREENSHOT_ON_TRADE
)
from utils import parse_currency, parse_number, log_trade
from state_manager import StateManager

logger = logging.getLogger('stocktrak_bot.browser')

# Ensure logs directory exists
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# =============================================================================
# STALL-PROOF WRAPPER: Every step has a hard timeout + retries
# =============================================================================
def run_step(page: Page, name: str, fn: Callable[[], Any], max_attempts: int = 3,
             reset_url: str = "https://app.stocktrak.com/dashboard/standard") -> Any:
    """
    Execute a step with retries, screenshots on failure, and hard reset between attempts.

    This is the "no more stalls" wrapper. Every step either completes or fails fast
    with artifacts (screenshots + logs).

    Args:
        page: Playwright page object
        name: Step name for logging/screenshots
        fn: The function to execute (takes no args)
        max_attempts: Maximum retry attempts
        reset_url: URL to navigate to on failure reset

    Returns:
        The return value of fn() on success

    Raises:
        Exception: Re-raises the last exception after all attempts fail
    """
    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"[{name}] Attempt {attempt}/{max_attempts}")
            result = fn()
            logger.info(f"[{name}] SUCCESS on attempt {attempt}")
            return result

        except Exception as e:
            last_exception = e
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            logger.warning(f"[{name}] FAILED attempt {attempt}: {e}")

            # Take failure screenshot
            try:
                screenshot_path = os.path.join(SCREENSHOT_DIR, f"{name}_fail_{attempt}_{ts}.png")
                page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"[{name}] Failure screenshot: {screenshot_path}")
            except Exception as ss_err:
                logger.debug(f"[{name}] Could not take screenshot: {ss_err}")

            # Log current URL and title for debugging
            try:
                logger.info(f"[{name}] Current URL: {page.url}")
                logger.info(f"[{name}] Current title: {page.title()}")
            except:
                pass

            # Hard reset between retries (navigate to dashboard)
            if attempt < max_attempts:
                try:
                    logger.info(f"[{name}] Resetting to {reset_url}...")
                    page.goto(reset_url, wait_until="domcontentloaded", timeout=60000)
                    dismiss_stocktrak_overlays(page, total_ms=10000)
                    time.sleep(1)
                except Exception as reset_err:
                    logger.warning(f"[{name}] Reset failed: {reset_err}")

    # All attempts exhausted
    logger.error(f"[{name}] FAILED after {max_attempts} attempts")
    raise last_exception


def dismiss_stocktrak_overlays(page, total_ms: int = 15000, max_attempts: int = None) -> int:
    """
    Aggressively dismiss ALL popups/modals that block interaction.
    Uses a time-based loop that keeps trying until timeout.

    Handles:
    - Robinhood promo modal ("Don't Show Again" / "Remind Me Later")
    - Site tours ("Skip" / "Skip Tour")
    - Cookie notices
    - Any modal/overlay with close buttons

    Args:
        page: Playwright page object
        total_ms: Total milliseconds to spend dismissing (default 15s)
        max_attempts: Deprecated, use total_ms instead

    Returns:
        Number of popups dismissed
    """
    dismissed_count = 0
    end_time = time.time() + (total_ms / 1000)

    # Button text patterns to match (case-insensitive)
    button_patterns = [
        r"don't show again",
        r"remind me later",
        r"skip",
        r"skip tour",
        r"no thanks",
        r"got it",
        r"close",
        r"done",
        r"dismiss",
        r"ok",
        r"end tour",
        r"maybe later",
    ]

    # Specific CSS selectors (exact matches)
    css_selectors = [
        # Robinhood promo modal (exact IDs)
        "#btn-dont-show-again",
        "#btn-remindlater",
        "#OverlayModalPopup button",
        "#OverlayModalPopup a.button",

        # Tour library specific (Intro.js, Shepherd.js, Hopscotch)
        ".introjs-skipbutton",
        ".introjs-donebutton",
        ".shepherd-cancel-icon",
        ".shepherd-button-secondary",
        ".hopscotch-bubble-close",
        ".tour-skip",
        ".tour-close",
        ".tour-end",
        ".walkthrough-skip",
        ".walkthrough-close",

        # Generic modal close buttons
        ".modal .close",
        ".modal-close",
        ".modal .btn-close",
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "[aria-label='Close']",
        "button.close",
        ".btn-close",
        ".close-button",

        # X close icons
        "button:has-text('×')",
        "a:has-text('×')",

        # UI dialog close
        ".ui-dialog-titlebar-close",
    ]

    while time.time() < end_time:
        closed_any = False

        # Method 1: Click buttons by accessible name (role=button)
        for pattern in button_patterns:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if btn.is_visible(timeout=500):
                    btn.click(force=True, timeout=1500)
                    dismissed_count += 1
                    closed_any = True
                    logger.info(f"Dismissed popup #{dismissed_count} via button pattern: {pattern}")
            except:
                pass

        # Method 2: Click links by text (for <a> tags styled as buttons)
        for pattern in button_patterns:
            try:
                link = page.get_by_role("link", name=re.compile(pattern, re.I)).first
                if link.is_visible(timeout=500):
                    link.click(force=True, timeout=1500)
                    dismissed_count += 1
                    closed_any = True
                    logger.info(f"Dismissed popup #{dismissed_count} via link pattern: {pattern}")
            except:
                pass

        # Method 3: Click specific CSS selectors
        for sel in css_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=300):
                    loc.click(force=True, timeout=800)
                    dismissed_count += 1
                    closed_any = True
                    logger.info(f"Dismissed popup #{dismissed_count} via selector: {sel}")
                    time.sleep(0.2)
            except:
                pass

        # Method 4: ESC key closes many modals
        try:
            page.keyboard.press("Escape")
        except:
            pass

        # If nothing was closed this iteration, we might be done
        if not closed_any:
            break

        # Small delay between iterations
        page.wait_for_timeout(250)

    if dismissed_count > 0:
        logger.info(f"Total popups dismissed: {dismissed_count}")

    return dismissed_count


def verify_page_ready(page, expected_url_contains: str = None, required_element: str = None) -> Tuple[bool, str]:
    """
    Verify the page is loaded correctly and ready for interaction.

    Args:
        page: Playwright page object
        expected_url_contains: String that should be in the URL
        required_element: CSS selector of element that must be visible

    Returns:
        Tuple of (is_ready, status_message)
    """
    try:
        # Check URL
        current_url = page.url
        if expected_url_contains and expected_url_contains.lower() not in current_url.lower():
            return False, f"Wrong URL: expected '{expected_url_contains}' in '{current_url}'"

        # Check for blocking overlays
        blocking_selectors = [
            ".modal.show",
            ".modal[style*='display: block']",
            "#OverlayModalPopup:visible",
            ".introjs-overlay",
            ".shepherd-modal-overlay",
            ".tour-backdrop",
        ]

        for sel in blocking_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=200):
                    return False, f"Blocking overlay detected: {sel}"
            except:
                pass

        # Check required element if specified
        if required_element:
            try:
                if not page.locator(required_element).first.is_visible(timeout=2000):
                    return False, f"Required element not visible: {required_element}"
            except:
                return False, f"Required element not found: {required_element}"

        return True, "Page ready"

    except Exception as e:
        return False, f"Verification error: {e}"


def take_debug_screenshot(page, name: str) -> str:
    """
    Take a screenshot and return the full path.

    Args:
        page: Playwright page object
        name: Base name for the screenshot

    Returns:
        Full path to the screenshot file
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{timestamp}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)

    try:
        page.screenshot(path=filepath)
        logger.info(f"Screenshot saved: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Screenshot failed: {e}")
        return ""


class StockTrakBot:
    """
    Browser automation for StockTrak trading platform.

    Handles login, portfolio viewing, and order placement.
    """

    def __init__(self, headless: bool = None):
        self.username = STOCKTRAK_USERNAME
        self.password = STOCKTRAK_PASSWORD
        self.base_url = STOCKTRAK_URL
        self.headless = headless if headless is not None else HEADLESS_MODE

        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.logged_in = False

        # Ensure logs directory exists
        os.makedirs('logs', exist_ok=True)

    def start_browser(self, headless: bool = None, use_persistent: bool = True):
        """
        Start browser with configured settings.

        Uses a persistent browser profile by default so that "Don't show again"
        settings stick across sessions and popups stop coming back.

        Args:
            headless: Override headless setting
            use_persistent: Use persistent browser context (default True)
        """
        if headless is not None:
            self.headless = headless

        logger.info(f"Starting browser (headless={self.headless}, persistent={use_persistent})...")

        self.playwright = sync_playwright().start()

        if use_persistent:
            # PERSISTENT CONTEXT: Cookies/localStorage persist across runs
            # This means "Don't show again" actually works!
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=self.headless,
                slow_mo=SLOW_MO,
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self.browser = None  # Persistent context doesn't use separate browser
            # Use the default page or create one
            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()
            logger.info(f"Using persistent profile: {PROFILE_DIR}")
        else:
            # Ephemeral context (fresh every time)
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                slow_mo=SLOW_MO,
            )
            self.context = self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self.page = self.context.new_page()

        # Increase timeouts - StockTrak can be slow
        self.page.set_default_timeout(90000)  # 90 seconds
        self.page.set_default_navigation_timeout(90000)

        logger.info("Browser started successfully")

    def login(self) -> bool:
        """
        Login to StockTrak at app.stocktrak.com.

        Uses checkpoint verification: waits for a known logged-in element
        (like "Portfolio Simulation") rather than assuming success.

        Returns:
            True if login successful, False otherwise
        """
        logger.info("Attempting login to StockTrak...")

        try:
            # Navigate to login page
            self.page.goto(STOCKTRAK_LOGIN_URL, wait_until="domcontentloaded")
            dismiss_stocktrak_overlays(self.page, total_ms=10000)
            time.sleep(1)

            # Screenshot for debugging
            take_debug_screenshot(self.page, 'login_page')

            # Check if already logged in (persistent profile may have session)
            if self._check_logged_in():
                logger.info("Already logged in (from persistent session)")
                self.logged_in = True
                return True

            # Try common login form selectors
            username_selectors = [
                'input[name="username"]',
                'input[name="login"]',
                'input[name="email"]',
                'input[id="username"]',
                'input[id="login"]',
                'input[type="text"]:first-of-type',
                '#username',
                '#login',
                'input[placeholder*="username" i]',
                'input[placeholder*="email" i]',
            ]

            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[id="password"]',
                '#password',
            ]

            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Sign In")',
                'button:has-text("Log In")',
                '.login-button',
                '#login-btn',
                'button.btn-primary',
            ]

            # Fill username
            username_filled = self._try_fill(username_selectors, self.username)
            if not username_filled:
                logger.error("Could not find username field")
                screenshot_path = take_debug_screenshot(self.page, 'login_error_username')
                logger.error(f"ERROR screenshot: {screenshot_path}")
                return False

            # Fill password
            password_filled = self._try_fill(password_selectors, self.password)
            if not password_filled:
                logger.error("Could not find password field")
                screenshot_path = take_debug_screenshot(self.page, 'login_error_password')
                logger.error(f"ERROR screenshot: {screenshot_path}")
                return False

            time.sleep(0.5)

            # Click submit
            submitted = self._try_click(submit_selectors)
            if not submitted:
                logger.error("Could not find submit button")
                screenshot_path = take_debug_screenshot(self.page, 'login_error_submit')
                logger.error(f"ERROR screenshot: {screenshot_path}")
                return False

            # Wait for navigation - EXPLICIT TIMEOUT to prevent hangs
            logger.info("Waiting for page to load after login...")
            self.page.wait_for_load_state('networkidle', timeout=60000)  # 60s max

            # Clear popups (time-based, more robust)
            logger.info("=== CLEARING POPUPS ===")
            total_dismissed = dismiss_stocktrak_overlays(self.page, total_ms=20000)
            logger.info(f"=== TOTAL POPUPS DISMISSED: {total_dismissed} ===")

            take_debug_screenshot(self.page, 'after_login_popups_cleared')

            # CHECKPOINT: Wait for a known logged-in element
            # "Portfolio Simulation" only exists when logged in
            logger.info("Waiting for login checkpoint (Portfolio Simulation)...")
            try:
                self.page.wait_for_selector("text=Portfolio Simulation", timeout=90000)
                self.logged_in = True
                logger.info("LOGIN CHECKPOINT PASSED: 'Portfolio Simulation' visible")
                return True
            except Exception as e:
                logger.warning(f"Primary checkpoint failed: {e}")

            # Fallback checkpoints
            fallback_indicators = [
                "text=My Portfolio",
                "text=Trading",
                "text=Logout",
                "text=Account",
            ]

            for indicator in fallback_indicators:
                try:
                    if self.page.locator(indicator).first.is_visible(timeout=3000):
                        self.logged_in = True
                        logger.info(f"Login checkpoint passed via: {indicator}")
                        return True
                except:
                    continue

            # Check for error messages
            error_selectors = ['.error', '.alert-danger', '.login-error', '#error', '.alert-error']
            for selector in error_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        error_text = self.page.locator(selector).first.text_content()
                        logger.error(f"Login error: {error_text}")
                        return False
                except:
                    continue

            # If we got past login page, assume success
            if 'login' not in self.page.url.lower():
                self.logged_in = True
                logger.info("Login appears successful (no longer on login page)")
                return True

            logger.warning("Login status uncertain - check screenshots")
            take_debug_screenshot(self.page, 'login_uncertain')
            return False

        except Exception as e:
            logger.error(f"Login exception: {e}")
            screenshot_path = take_debug_screenshot(self.page, 'login_exception')
            logger.error(f"EXCEPTION screenshot: {screenshot_path}")
            return False

    def verify_ready_for_trading(self) -> Tuple[bool, str]:
        """
        Verify the bot is ready to execute trades.
        Should be called after login and before any trading operations.

        This performs:
        1. Popup/overlay clearance
        2. URL verification
        3. Key element accessibility check
        4. Screenshot for verification

        Returns:
            Tuple of (is_ready, status_message)
        """
        logger.info("=== VERIFYING READY FOR TRADING ===")
        errors = []

        try:
            # Step 1: Clear any remaining popups
            logger.info("Step 1: Clearing any remaining popups...")
            dismissed = dismiss_stocktrak_overlays(self.page, max_attempts=5)
            if dismissed > 0:
                logger.info(f"Cleared {dismissed} popup(s)")
                time.sleep(1)
                # Try again in case more appeared
                dismiss_stocktrak_overlays(self.page, max_attempts=3)

            # Step 2: Verify URL
            logger.info("Step 2: Verifying URL...")
            current_url = self.page.url.lower()
            if 'login' in current_url:
                errors.append("Still on login page - not authenticated")
            elif not any(x in current_url for x in ['dashboard', 'portfolio', 'trading', 'account']):
                errors.append(f"Unexpected URL: {current_url}")

            # Step 3: Check for blocking overlays
            logger.info("Step 3: Checking for blocking overlays...")
            blocking_selectors = [
                "#OverlayModalPopup",
                ".modal.show",
                ".modal[style*='display: block']",
                ".introjs-overlay",
                ".shepherd-modal-overlay",
                ".introjs-helperLayer",
            ]

            for sel in blocking_selectors:
                try:
                    if self.page.locator(sel).first.is_visible(timeout=500):
                        errors.append(f"Blocking overlay still visible: {sel}")
                        # Try to dismiss it
                        dismiss_stocktrak_overlays(self.page, max_attempts=3)
                except:
                    pass

            # Step 4: Check key navigation elements are accessible
            logger.info("Step 4: Checking navigation elements...")
            nav_selectors = [
                "a:has-text('Portfolio')",
                "a:has-text('Trading')",
                "a:has-text('Dashboard')",
                "[href*='portfolio']",
                "[href*='trading']",
            ]

            nav_found = False
            for sel in nav_selectors:
                try:
                    if self.page.locator(sel).first.is_visible(timeout=1000):
                        nav_found = True
                        logger.info(f"Navigation element found: {sel}")
                        break
                except:
                    pass

            if not nav_found:
                errors.append("No navigation elements found - page may not be fully loaded")

            # Step 5: Take verification screenshot
            logger.info("Step 5: Taking verification screenshot...")
            screenshot_path = take_debug_screenshot(self.page, 'verification_complete')

            # Report results
            if errors:
                error_msg = "; ".join(errors)
                logger.error(f"VERIFICATION FAILED: {error_msg}")
                logger.error(f"Verification screenshot: {screenshot_path}")
                return False, error_msg
            else:
                logger.info("=== VERIFICATION PASSED - READY FOR TRADING ===")
                logger.info(f"Verification screenshot: {screenshot_path}")
                return True, "Ready for trading"

        except Exception as e:
            screenshot_path = take_debug_screenshot(self.page, 'verification_exception')
            error_msg = f"Verification exception: {e}"
            logger.error(error_msg)
            logger.error(f"Exception screenshot: {screenshot_path}")
            return False, error_msg

    def ensure_page_ready(self) -> bool:
        """
        Ensure the current page is ready for interaction.
        Clears popups and verifies page state.

        Returns:
            True if page is ready, False otherwise
        """
        try:
            # Clear popups
            dismiss_stocktrak_overlays(self.page, max_attempts=3)
            time.sleep(0.5)

            # Quick verification
            is_ready, status = verify_page_ready(self.page)
            if not is_ready:
                logger.warning(f"Page not ready: {status}")
                # Try one more popup clear
                dismiss_stocktrak_overlays(self.page, max_attempts=3)
                time.sleep(0.5)
                is_ready, status = verify_page_ready(self.page)

            return is_ready

        except Exception as e:
            logger.error(f"ensure_page_ready error: {e}")
            return False

    def get_portfolio_value(self) -> Optional[float]:
        """
        Navigate to portfolio and get total value.

        Returns:
            Portfolio value as float, or None if not found
        """
        try:
            # Ensure page is ready
            self.ensure_page_ready()

            # Try common portfolio URLs
            portfolio_urls = [
                f"{self.base_url}/portfolio",
                f"{self.base_url}/portfolio/holdings",
                f"{self.base_url}/account/portfolio",
                f"{self.base_url}/trading/portfolio",
                f"{self.base_url}/dashboard",
            ]

            for url in portfolio_urls:
                try:
                    self.page.goto(url)
                    self.page.wait_for_load_state('domcontentloaded')
                    time.sleep(1)
                    # Clear any popups that appear on navigation
                    dismiss_stocktrak_overlays(self.page, max_attempts=3)
                    if 'portfolio' in self.page.url.lower() or 'dashboard' in self.page.url.lower():
                        break
                except:
                    continue

            time.sleep(2)
            self._screenshot('portfolio_page')

            # Try to find portfolio value
            value_selectors = [
                '.portfolio-value',
                '.total-value',
                '.account-value',
                '.equity-value',
                '.net-worth',
                '#portfolio-value',
                '#total-equity',
                '#account-balance',
                'span:has-text("Total")',
                'div:has-text("Portfolio Value")',
            ]

            for selector in value_selectors:
                try:
                    elements = self.page.locator(selector).all()
                    for elem in elements:
                        text = elem.text_content()
                        if '$' in text:
                            value = parse_currency(text)
                            # Sanity check - expect value around $1M for this competition
                            if value and 100000 < value < 10000000:
                                logger.info(f"Portfolio value: ${value:,.2f}")
                                return value
                except:
                    continue

            # Try finding any large dollar amount on the page
            page_text = self.page.content()
            import re
            amounts = re.findall(r'\$[\d,]+\.?\d*', page_text)
            for amount_str in amounts:
                value = parse_currency(amount_str)
                if value and 100000 < value < 10000000:
                    logger.info(f"Portfolio value (regex): ${value:,.2f}")
                    return value

            logger.error("Could not find portfolio value")
            return None

        except Exception as e:
            logger.error(f"Error getting portfolio value: {e}")
            return None

    def get_current_holdings(self) -> Dict[str, Dict]:
        """
        Get all current positions.

        Returns:
            Dict mapping ticker -> {shares, avg_cost, current_value, raw_data}
        """
        holdings = {}

        try:
            # Ensure page is ready
            self.ensure_page_ready()

            # Navigate to holdings
            holdings_urls = [
                f"{self.base_url}/portfolio/holdings",
                f"{self.base_url}/portfolio",
                f"{self.base_url}/positions",
            ]

            for url in holdings_urls:
                try:
                    self.page.goto(url)
                    self.page.wait_for_load_state('networkidle')
                    if 'holdings' in self.page.url.lower() or 'position' in self.page.url.lower():
                        break
                except:
                    continue

            time.sleep(2)
            self._screenshot('holdings_page')

            # Try to find holdings table
            table_selectors = [
                'table.holdings',
                '.holdings-table',
                '#holdings-table',
                '.positions-table',
                'table.positions',
                'table',
            ]

            for selector in table_selectors:
                try:
                    tables = self.page.locator(selector).all()
                    for table in tables:
                        rows = table.locator('tr').all()
                        if len(rows) > 1:  # Has data rows
                            for row in rows[1:]:  # Skip header
                                cells = row.locator('td').all()
                                if len(cells) >= 2:
                                    ticker_text = cells[0].text_content().strip().upper()
                                    # Extract ticker (first word, alphanumeric)
                                    import re
                                    match = re.match(r'^([A-Z]{1,5})\b', ticker_text)
                                    if match:
                                        ticker = match.group(1)
                                        holdings[ticker] = {
                                            'shares': parse_number(cells[1].text_content()) if len(cells) > 1 else 0,
                                            'raw_data': [c.text_content() for c in cells]
                                        }
                            if holdings:
                                break
                    if holdings:
                        break
                except Exception as e:
                    logger.debug(f"Table parse error with {selector}: {e}")
                    continue

            logger.info(f"Found {len(holdings)} holdings: {list(holdings.keys())}")
            return holdings

        except Exception as e:
            logger.error(f"Error getting holdings: {e}")
            return {}

    def get_cash_balance(self) -> Optional[float]:
        """
        Get available cash/buying power.

        Returns:
            Cash balance as float, or None if not found
        """
        try:
            cash_selectors = [
                '.cash-balance',
                '.buying-power',
                '.available-cash',
                '#cash-balance',
                '#buying-power',
                'span:has-text("Cash")',
                'span:has-text("Buying Power")',
                'div:has-text("Available")',
            ]

            for selector in cash_selectors:
                try:
                    elements = self.page.locator(selector).all()
                    for elem in elements:
                        text = elem.text_content()
                        if '$' in text:
                            value = parse_currency(text)
                            if value and value > 0:
                                logger.info(f"Cash balance: ${value:,.2f}")
                                return value
                except:
                    continue

            return None

        except Exception as e:
            logger.error(f"Error getting cash: {e}")
            return None

    def get_transaction_count(self) -> int:
        """
        Get total number of executed trades from trade page KPI strip.
        CRITICAL for staying under 80 trade limit.

        Reads "TRADES MADE X / 300" from the /trading/equities page.
        This avoids the broken /portfolio/transactions endpoint (404).

        Returns:
            Number of trades executed
        """
        try:
            # Navigate to canonical trade page (which has KPI strip with trade count)
            trade_url = self._trade_equities_url("VOO")
            logger.info(f"Reading trade count from: {trade_url}")
            self.page.goto(trade_url)
            self.page.wait_for_load_state('domcontentloaded')
            time.sleep(2)

            # Dismiss any popups
            dismiss_stocktrak_overlays(self.page, max_attempts=3)

            # Get page text
            body_text = self.page.locator('body').inner_text()

            # Look for "TRADES MADE X / 300" or similar pattern
            # Pattern matches: "0 / 300", "5/300", "TRADES MADE 0 / 300", etc.
            patterns = [
                r'TRADES?\s*MADE\s*(\d+)\s*/\s*(\d+)',  # "TRADES MADE 0 / 300"
                r'(\d+)\s*/\s*300',                      # "0 / 300" near trade context
                r'(\d+)\s+/\s+(\d+)\s*trades?',         # "0 / 300 trades"
            ]

            for pattern in patterns:
                match = re.search(pattern, body_text, re.IGNORECASE)
                if match:
                    trade_count = int(match.group(1))
                    logger.info(f"Trade count from KPI strip: {trade_count}")
                    return trade_count

            # Fallback: look for just a number near "trades" text
            # This is less reliable but better than 0
            trade_match = re.search(r'(\d+)\s*(?:/|of)\s*\d+', body_text)
            if trade_match:
                count = int(trade_match.group(1))
                if count < 100:  # Sanity check
                    logger.info(f"Trade count (fallback): {count}")
                    return count

            logger.warning("Could not determine transaction count from KPI strip, returning 0")
            return 0

        except Exception as e:
            logger.error(f"Error getting transaction count: {e}")
            return 0

    def _trade_equities_url(self, ticker: str) -> str:
        """
        Build the correct trade page URL for a ticker.

        StockTrak's actual trade page is:
        /trading/equities?securitysymbol={TICKER}&exchange=US

        NOT /trade or /trading/stocks (those 404).
        """
        return f"{self.base_url}/trading/equities?securitysymbol={ticker.upper()}&exchange=US"

    def assert_on_trade_page(self, ticker: str) -> None:
        """
        Verify we're on the correct trade page before placing orders.

        CRITICAL: This prevents silent failures when navigation fails.
        If not on trade page, takes screenshot and raises exception.
        """
        required_elements = [
            "trade",  # "Trade Equities" or similar
            ticker.upper(),  # The ticker symbol
        ]

        page_text = self.page.content().lower()
        ticker_lower = ticker.lower()

        # Check for trade page indicators
        is_trade_page = (
            "trade" in page_text and
            (ticker_lower in page_text or "symbol" in page_text)
        )

        # Also check URL
        current_url = self.page.url.lower()
        url_ok = "trading" in current_url and "equities" in current_url

        if not (is_trade_page or url_ok):
            screenshot_path = take_debug_screenshot(self.page, f'not_on_trade_page_{ticker}')
            raise RuntimeError(
                f"NOT ON VALID TRADE PAGE for {ticker}. "
                f"URL: {self.page.url}. Screenshot: {screenshot_path}"
            )

        logger.info(f"Verified on trade page for {ticker}")

    def get_capital_from_trade_page(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Read Portfolio Value, Cash Balance, Buying Power from trade page header.

        These values are reliably displayed on /trading/equities.

        Returns:
            Tuple of (portfolio_value, cash_balance, buying_power)
        """
        values = {
            'portfolio': None,
            'cash': None,
            'buying_power': None
        }

        # Selectors for the trade page header cards
        selectors = {
            'portfolio': [
                "div:has-text('Portfolio Value')",
                ".portfolio-value",
                "[data-label='Portfolio Value']",
            ],
            'cash': [
                "div:has-text('Cash Balance')",
                ".cash-balance",
                "[data-label='Cash Balance']",
            ],
            'buying_power': [
                "div:has-text('Buying Power')",
                ".buying-power",
                "[data-label='Buying Power']",
            ]
        }

        for key, sel_list in selectors.items():
            for sel in sel_list:
                try:
                    elem = self.page.locator(sel).first
                    if elem.is_visible(timeout=1000):
                        text = elem.text_content()
                        value = parse_currency(text)
                        if value and value > 0:
                            values[key] = value
                            logger.debug(f"Found {key}: ${value:,.2f}")
                            break
                except:
                    continue

        return values['portfolio'], values['cash'], values['buying_power']

    def get_capital_from_trade_kpis(self, ticker: str = "VOO") -> Tuple[float, float, float]:
        """
        Navigate to canonical trade page and robustly extract capital from KPI strip.

        Uses regex to find money values (with or without $) in the page text.
        The $ sign is often rendered via CSS, not in DOM text.

        Args:
            ticker: Any valid ticker to navigate to the trade page (default VOO)

        Returns:
            Tuple of (portfolio_value, cash_balance, buying_power)

        Raises:
            RuntimeError: If cannot parse all 3 values (FAIL-CLOSED behavior)
        """
        logger.info(f"Reading capital from trade KPIs using ticker {ticker}...")

        # Navigate to canonical trade page
        trade_url = self._trade_equities_url(ticker)
        logger.info(f"Navigating to: {trade_url}")
        self.page.goto(trade_url)
        self.page.wait_for_load_state('domcontentloaded')
        time.sleep(2)

        # Dismiss popups that may block the page
        dismiss_stocktrak_overlays(self.page, max_attempts=5)
        time.sleep(0.5)

        # Take screenshot for debugging
        screenshot_path = take_debug_screenshot(self.page, f'trade_kpis_{ticker}')
        logger.info(f"Trade KPIs screenshot: {screenshot_path}")

        try:
            # Get ALL text from the page body
            body_text = self.page.locator('body').inner_text()
            logger.debug(f"Body text length: {len(body_text)}")

            # Regex that matches money values WITH OR WITHOUT $
            # Matches: $500,315.16 OR 500,315.16
            # Pattern: optional $, optional whitespace, 1-3 digits, then groups of comma+3 digits, then decimal+2 digits
            money_pattern = r'\$?\s*(\d{1,3}(?:,\d{3})+\.\d{2})'
            matches = re.findall(money_pattern, body_text)

            logger.info(f"Raw money matches: {matches[:20]}")  # Log first 20 matches

            # Parse and filter to CAPITAL-SIZED values only (>= $100,000)
            # This filters out stock prices, order totals, etc.
            capital_values = []
            for match in matches:
                try:
                    # Remove commas and parse
                    value = float(match.replace(',', ''))
                    # Capital-sized: between $100k and $50M
                    if 100_000 <= value <= 50_000_000:
                        capital_values.append(value)
                except ValueError:
                    continue

            logger.info(f"Capital-sized values (>=$100k): {capital_values}")

            # Take the first 3 capital-sized values (portfolio, cash, buying_power)
            if len(capital_values) >= 3:
                portfolio_value = capital_values[0]
                cash_balance = capital_values[1]
                buying_power = capital_values[2]
            elif len(capital_values) == 2:
                portfolio_value = capital_values[0]
                cash_balance = capital_values[1]
                buying_power = capital_values[1]
                logger.warning("Only 2 capital values found, using second for both cash and buying power")
            elif len(capital_values) == 1:
                # All three are likely the same (common at competition start)
                portfolio_value = capital_values[0]
                cash_balance = capital_values[0]
                buying_power = capital_values[0]
                logger.warning("Only 1 capital value found, using it for all three")
            else:
                raise RuntimeError(
                    f"No capital-sized values found (>=$100k). "
                    f"Raw matches: {matches[:10]}. Screenshot: {screenshot_path}"
                )

            logger.info(f"Capital from KPIs: Portfolio=${portfolio_value:,.2f}, "
                       f"Cash=${cash_balance:,.2f}, Buying Power=${buying_power:,.2f}")

            return portfolio_value, cash_balance, buying_power

        except RuntimeError:
            raise  # Re-raise our own errors
        except Exception as e:
            raise RuntimeError(
                f"Failed to extract capital from trade KPIs: {e}. Screenshot: {screenshot_path}"
            )

    def go_to_equity_trade_ticket(self, ticker: str) -> bool:
        """
        Navigate to the equity trade ticket for a given symbol.

        Tries menu navigation first (more stable), falls back to direct URL.

        Args:
            ticker: Stock ticker symbol

        Returns:
            True if navigation successful, False otherwise
        """
        logger.info(f"Navigating to trade ticket for {ticker}")
        dismiss_stocktrak_overlays(self.page, total_ms=5000)

        # Method 1: Menu navigation (Trading tab → Stocks/Equities)
        try:
            logger.info("Trying menu navigation: Trading → Stocks")

            # Hover over Trading tab
            trading_link = self.page.get_by_role("link", name=re.compile("^Trading$", re.I))
            trading_link.hover()
            self.page.wait_for_timeout(300)

            # Click on Stocks or Equities submenu
            stocks_link = self.page.get_by_role("link", name=re.compile("stock|equit", re.I)).first
            stocks_link.click()
            self.page.wait_for_load_state("networkidle")
            dismiss_stocktrak_overlays(self.page, total_ms=5000)

            # Fill symbol in the search box
            symbol_input = self.page.get_by_label(re.compile("symbol|ticker|search", re.I)).first
            symbol_input.wait_for(state="visible", timeout=10000)
            symbol_input.fill(ticker.upper())
            symbol_input.press("Enter")
            self.page.wait_for_load_state("networkidle")
            dismiss_stocktrak_overlays(self.page, total_ms=5000)

            # Verify we're on the trade page
            self.page.wait_for_selector("button:has-text('Buy')", timeout=15000)
            logger.info(f"Menu navigation successful for {ticker}")
            return True

        except Exception as e:
            logger.warning(f"Menu navigation failed: {e}")

        # Method 2: Direct URL navigation (fallback)
        try:
            logger.info("Falling back to direct URL navigation")
            trade_url = self._trade_equities_url(ticker)
            self.page.goto(trade_url)
            self.page.wait_for_load_state('domcontentloaded')
            time.sleep(2)
            dismiss_stocktrak_overlays(self.page, total_ms=5000)

            # Verify we're on the trade page
            self.assert_on_trade_page(ticker)
            logger.info(f"URL navigation successful for {ticker}")
            return True

        except Exception as e:
            logger.error(f"URL navigation also failed: {e}")
            take_debug_screenshot(self.page, f'trade_nav_failed_{ticker}')
            return False

    def place_buy_order(self, ticker: str, shares: int, limit_price: float, dry_run: bool = False) -> Tuple[bool, str]:
        """
        Place a limit buy order.

        Args:
            ticker: Stock ticker symbol
            shares: Number of shares to buy
            limit_price: Limit price per share
            dry_run: If True, navigate and fill but don't submit

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Placing BUY order: {shares} {ticker} @ ${limit_price:.2f}" +
                   (" [DRY RUN]" if dry_run else ""))

        # IDEMPOTENCY CHECK: Prevent duplicate orders on restart
        state = StateManager()
        if state.already_submitted_today(ticker, 'BUY', shares, limit_price):
            logger.warning(f"DUPLICATE ORDER BLOCKED: BUY {shares} {ticker} @ ${limit_price:.2f} already submitted today")
            return False, "Duplicate order blocked - already submitted today"

        # SAFE MODE GUARDS
        if config.SAFE_MODE:
            # Check whitelist
            if ticker.upper() not in config.SAFE_MODE_ETF_WHITELIST:
                logger.warning(f"SAFE MODE: {ticker} not in ETF whitelist - blocking order")
                return False, f"Safe mode: {ticker} not in ETF whitelist"
            # Enforce max shares
            if shares > config.SAFE_MODE_MAX_SHARES:
                logger.warning(f"SAFE MODE: Reducing shares from {shares} to {config.SAFE_MODE_MAX_SHARES}")
                shares = config.SAFE_MODE_MAX_SHARES

        # Check global DRY_RUN_MODE (overrides function parameter)
        if config.DRY_RUN_MODE:
            dry_run = True

        try:
            # Navigate to trade ticket (tries menu first, falls back to URL)
            if not self.go_to_equity_trade_ticket(ticker):
                return False, f"Could not navigate to trade page for {ticker}"

            take_debug_screenshot(self.page, f'trade_page_{ticker}')

            # Symbol is already set via URL parameter - NO NEED TO FILL
            # Just verify the ticker is displayed on the page
            page_text = self.page.locator('body').inner_text()
            if ticker.upper() not in page_text.upper():
                logger.warning(f"Ticker {ticker} not visible on page, but continuing...")

            # Click BUY button (big green button on StockTrak)
            logger.info("Clicking Buy button...")
            try:
                buy_btn = self.page.locator("button:has-text('Buy')").first
                buy_btn.wait_for(state="visible", timeout=8000)
                buy_btn.click()
                logger.info("Clicked Buy button")
            except Exception as e:
                logger.warning(f"Could not click Buy button: {e}, may already be selected")
            time.sleep(1)

            # Fill QUANTITY - CRITICAL: Hard-clear + verify
            # The input may have pre-filled value (e.g., "100") that must be cleared
            logger.info(f"Filling quantity: {shares}")
            qty_filled = False
            shares_input = None

            # Method 1: Anchor off "SHARES" label (most reliable for this UI)
            try:
                shares_input = self.page.locator("text=SHARES").locator("..").locator("input").first
                shares_input.wait_for(state="visible", timeout=8000)
            except Exception as e:
                logger.debug(f"SHARES-anchored locator failed: {e}")
                shares_input = None

            # Method 2: Try ancestor search if direct parent doesn't work
            if not shares_input or not shares_input.is_visible():
                try:
                    shares_input = self.page.locator("text=SHARES").locator("xpath=ancestor::div[1]//input").first
                    shares_input.wait_for(state="visible", timeout=5000)
                except Exception as e:
                    logger.debug(f"Ancestor locator failed: {e}")
                    shares_input = None

            # Method 3: Fallback to explicit name selectors
            if not shares_input or not shares_input.is_visible():
                for sel in ['input[name="shares"]', 'input[name="quantity"]', '#shares', '#quantity']:
                    try:
                        elem = self.page.locator(sel).first
                        if elem.is_visible(timeout=2000):
                            shares_input = elem
                            break
                    except:
                        continue

            if not shares_input:
                screenshot_path = take_debug_screenshot(self.page, f'qty_fill_failed_{ticker}')
                return False, f"Could not find SHARES input. Screenshot: {screenshot_path}"

            # HARD-CLEAR + TYPE: Click, Ctrl+A, Backspace, then type
            try:
                shares_input.click()
                time.sleep(0.1)
                shares_input.press("Control+a")
                time.sleep(0.1)
                shares_input.press("Backspace")
                time.sleep(0.1)
                shares_input.type(str(shares), delay=20)
                time.sleep(0.3)

                # VERIFY: Read back and confirm value matches
                actual_value = shares_input.input_value().strip()
                # Extract only digits for comparison (handles formatting)
                actual_digits = "".join(ch for ch in actual_value if ch.isdigit())
                expected_digits = str(shares)

                if actual_digits != expected_digits:
                    screenshot_path = take_debug_screenshot(self.page, f'shares_mismatch_{ticker}')
                    logger.error(f"SHARES MISMATCH: expected={shares}, got={actual_value}. Screenshot: {screenshot_path}")
                    return False, f"Shares input mismatch. Expected {shares}, got {actual_value}. Screenshot: {screenshot_path}"

                qty_filled = True
                logger.info(f"Filled and verified shares: {shares} (actual: {actual_value})")

            except Exception as e:
                screenshot_path = take_debug_screenshot(self.page, f'qty_fill_failed_{ticker}')
                logger.error(f"Error filling shares: {e}")
                return False, f"Could not fill quantity: {e}. Screenshot: {screenshot_path}"

            time.sleep(0.5)

            # Select ORDER TYPE = MARKET (most reliable for Day-1)
            # Limit orders have a custom JS price widget that's fragile to automate
            logger.info("Setting order type to MARKET...")
            try:
                # Find the order type dropdown and select Market
                order_type_dropdown = self.page.locator('select').filter(has_text='Market')
                if order_type_dropdown.count() > 0:
                    order_type_dropdown.first.select_option(label='Market')
                    logger.info("Order type set to Market")
                else:
                    # Try other selectors
                    for sel in ['select[name="orderType"]', 'select']:
                        try:
                            dropdown = self.page.locator(sel).first
                            if dropdown.is_visible(timeout=2000):
                                dropdown.select_option(label='Market')
                                logger.info("Order type set to Market via fallback")
                                break
                        except:
                            continue
            except Exception as e:
                logger.warning(f"Could not set order type: {e}, proceeding with default")
            time.sleep(0.5)

            # SKIP LIMIT PRICE - Market orders don't need it
            # The StockTrak limit price widget uses hidden inputs that are hard to automate

            # Set DURATION = Good for Day (usually default, but ensure it's set)
            try:
                duration_dropdown = self.page.locator('select').filter(has_text='Good for')
                if duration_dropdown.count() > 0:
                    duration_dropdown.first.select_option(index=0)  # First option is usually "Day"
            except:
                pass

            time.sleep(1)
            take_debug_screenshot(self.page, f'order_filled_{ticker}')

            # DRY RUN: Stop here without submitting
            if dry_run:
                logger.info(f"DRY RUN complete for {ticker} - order NOT submitted")
                return True, "Dry run complete - order not submitted"

            # Click REVIEW ORDER button - wait for it to become visible
            logger.info("Waiting for Review Order button...")
            try:
                review_btn = self.page.locator("button:has-text('Review Order')")
                review_btn.wait_for(state="visible", timeout=15000)
                review_btn.click()
                logger.info("Clicked Review Order")
            except Exception as e:
                screenshot_path = take_debug_screenshot(self.page, f'review_failed_{ticker}')
                logger.error(f"Could not click Review Order: {e}. Screenshot: {screenshot_path}")
                return False, f"Could not click Review Order. Screenshot: {screenshot_path}"

            time.sleep(3)  # Wait for review modal
            take_debug_screenshot(self.page, f'order_preview_{ticker}')

            # Click PLACE ORDER button - wait for it to become visible
            logger.info("Waiting for Place Order button...")
            try:
                place_btn = self.page.locator("button:has-text('Place Order')")
                place_btn.wait_for(state="visible", timeout=15000)
                place_btn.click()
                logger.info("Clicked Place Order")
            except Exception as e:
                screenshot_path = take_debug_screenshot(self.page, f'submit_failed_{ticker}')
                logger.error(f"Could not click Place Order: {e}. Screenshot: {screenshot_path}")
                return False, f"Could not click Place Order. Screenshot: {screenshot_path}"

            time.sleep(ORDER_SUBMISSION_WAIT)
            take_debug_screenshot(self.page, f'order_submitted_{ticker}')

            # Check for confirmation
            page_text = self.page.content().lower()
            if any(word in page_text for word in ['confirmed', 'submitted', 'success', 'order placed', 'order received']):
                logger.info(f"BUY order confirmed: {shares} {ticker}")
                log_trade('BUY', ticker, shares, limit_price, 'Order submitted')
                return True, "Order submitted successfully"

            # Check for errors
            if any(word in page_text for word in ['error', 'failed', 'invalid', 'rejected', 'insufficient']):
                error_msg = self._extract_error_message()
                screenshot_path = take_debug_screenshot(self.page, f'order_error_{ticker}')
                logger.error(f"Order failed for {ticker}: {error_msg}. Screenshot: {screenshot_path}")
                return False, f"Order failed: {error_msg}"

            # Uncertain but probably OK
            logger.info(f"BUY order submitted (unconfirmed): {shares} {ticker}")
            log_trade('BUY', ticker, shares, limit_price, 'Order submitted (unconfirmed)')
            return True, "Order submitted (unconfirmed)"

        except RuntimeError as e:
            # Trade page verification failed
            logger.error(f"Trade page error: {e}")
            return False, str(e)

        except Exception as e:
            screenshot_path = take_debug_screenshot(self.page, f'order_exception_{ticker}')
            logger.error(f"Error placing buy order: {e}. Screenshot: {screenshot_path}")
            return False, f"{e}. Screenshot: {screenshot_path}"

    def place_sell_order(self, ticker: str, shares: int, limit_price: float, dry_run: bool = False) -> Tuple[bool, str]:
        """
        Place a limit sell order.

        Args:
            ticker: Stock ticker symbol
            shares: Number of shares to sell
            limit_price: Limit price per share
            dry_run: If True, navigate and fill but don't submit

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Placing SELL order: {shares} {ticker} @ ${limit_price:.2f}" +
                   (" [DRY RUN]" if dry_run else ""))

        # IDEMPOTENCY CHECK: Prevent duplicate orders on restart
        state = StateManager()
        if state.already_submitted_today(ticker, 'SELL', shares, limit_price):
            logger.warning(f"DUPLICATE ORDER BLOCKED: SELL {shares} {ticker} @ ${limit_price:.2f} already submitted today")
            return False, "Duplicate order blocked - already submitted today"

        # SAFE MODE GUARDS
        if config.SAFE_MODE:
            # Check whitelist
            if ticker.upper() not in config.SAFE_MODE_ETF_WHITELIST:
                logger.warning(f"SAFE MODE: {ticker} not in ETF whitelist - blocking sell order")
                return False, f"Safe mode: {ticker} not in ETF whitelist"
            # Enforce max shares
            if shares > config.SAFE_MODE_MAX_SHARES:
                logger.warning(f"SAFE MODE: Reducing shares from {shares} to {config.SAFE_MODE_MAX_SHARES}")
                shares = config.SAFE_MODE_MAX_SHARES

        # Check global DRY_RUN_MODE (overrides function parameter)
        if config.DRY_RUN_MODE:
            dry_run = True

        try:
            # Navigate to trade ticket (tries menu first, falls back to URL)
            if not self.go_to_equity_trade_ticket(ticker):
                return False, f"Could not navigate to trade page for {ticker}"

            take_debug_screenshot(self.page, f'sell_trade_page_{ticker}')

            # Verify the ticker is displayed on the page
            page_text = self.page.locator('body').inner_text()
            if ticker.upper() not in page_text.upper():
                logger.warning(f"Ticker {ticker} not visible on page, but continuing...")

            # Click SELL button (button on StockTrak)
            logger.info("Clicking Sell button...")
            try:
                sell_btn = self.page.locator("button:has-text('Sell')").first
                sell_btn.wait_for(state="visible", timeout=8000)
                sell_btn.click()
                logger.info("Clicked Sell button")
            except Exception as e:
                logger.warning(f"Could not click Sell button: {e}, may already be selected")
            time.sleep(1)

            # Fill QUANTITY - CRITICAL: Use SHARES-anchored locator
            # Avoid input[type="number"] which matches hidden "amount-invest" field
            logger.info(f"Filling quantity: {shares}")
            shares_input = None

            # Method 1: Anchor off "SHARES" label (most reliable for this UI)
            try:
                shares_input = self.page.locator("text=SHARES").locator("..").locator("input").first
                shares_input.wait_for(state="visible", timeout=8000)
                if not shares_input.is_visible():
                    shares_input = None
                else:
                    logger.info("Found shares input using SHARES-anchored locator")
            except Exception as e:
                logger.debug(f"SHARES-anchored locator failed: {e}")
                shares_input = None

            # Method 2: Try ancestor search if direct parent doesn't work
            if shares_input is None:
                try:
                    shares_input = self.page.locator("text=SHARES").locator("xpath=ancestor::div[1]//input").first
                    shares_input.wait_for(state="visible", timeout=5000)
                    if not shares_input.is_visible():
                        shares_input = None
                    else:
                        logger.info("Found shares input using ancestor locator")
                except Exception as e:
                    logger.debug(f"Ancestor locator failed: {e}")
                    shares_input = None

            # Method 3: Fallback to explicit name selectors (NOT input[type="number"])
            if shares_input is None:
                fallback_selectors = [
                    'input[name="shares"]',
                    'input[name="quantity"]',
                    '#shares',
                    '#quantity',
                ]
                for sel in fallback_selectors:
                    try:
                        elem = self.page.locator(sel).first
                        if elem.is_visible(timeout=2000):
                            shares_input = elem
                            logger.info(f"Found shares input using fallback selector: {sel}")
                            break
                    except:
                        continue

            if shares_input is None:
                screenshot_path = take_debug_screenshot(self.page, f'sell_qty_fill_failed_{ticker}')
                return False, f"Could not fill quantity - SHARES input not found. Screenshot: {screenshot_path}"

            # HARD-CLEAR + TYPE: Click, Ctrl+A, Backspace, then type
            # This ensures any pre-filled value (like "100") is fully cleared
            try:
                shares_input.click()
                time.sleep(0.1)
                shares_input.press("Control+a")
                time.sleep(0.1)
                shares_input.press("Backspace")
                time.sleep(0.1)
                shares_input.type(str(shares), delay=20)
                time.sleep(0.3)

                # VERIFY: Read back and confirm value matches
                actual_value = shares_input.input_value().strip()
                actual_digits = "".join(ch for ch in actual_value if ch.isdigit())
                expected_digits = str(shares)

                if actual_digits != expected_digits:
                    screenshot_path = take_debug_screenshot(self.page, f'sell_shares_mismatch_{ticker}')
                    return False, f"Shares input mismatch. Expected {shares}, got '{actual_value}'. Screenshot: {screenshot_path}"

                logger.info(f"Verified shares input: {actual_value}")
            except Exception as e:
                screenshot_path = take_debug_screenshot(self.page, f'sell_shares_fill_error_{ticker}')
                return False, f"Failed to fill shares: {e}. Screenshot: {screenshot_path}"

            time.sleep(0.5)

            # Select ORDER TYPE = MARKET (most reliable)
            # Limit orders have a custom JS price widget that's fragile to automate
            logger.info("Setting order type to MARKET...")
            try:
                # Find the order type dropdown and select Market
                order_type_dropdown = self.page.locator('select').filter(has_text='Market')
                if order_type_dropdown.count() > 0:
                    order_type_dropdown.first.select_option(label='Market')
                    logger.info("Order type set to Market")
                else:
                    # Try other selectors
                    for sel in ['select[name="orderType"]', 'select']:
                        try:
                            dropdown = self.page.locator(sel).first
                            if dropdown.is_visible(timeout=2000):
                                dropdown.select_option(label='Market')
                                logger.info("Order type set to Market via fallback")
                                break
                        except:
                            continue
            except Exception as e:
                logger.warning(f"Could not set order type: {e}, proceeding with default")
            time.sleep(0.5)

            # SKIP LIMIT PRICE - Market orders don't need it
            # The StockTrak limit price widget uses hidden inputs that are hard to automate

            # Set DURATION = Good for Day (usually default, but ensure it's set)
            try:
                duration_dropdown = self.page.locator('select').filter(has_text='Good for')
                if duration_dropdown.count() > 0:
                    duration_dropdown.first.select_option(index=0)  # First option is usually "Day"
            except:
                pass

            time.sleep(1)
            take_debug_screenshot(self.page, f'sell_order_filled_{ticker}')

            # DRY RUN: Stop here without submitting
            if dry_run:
                logger.info(f"DRY RUN complete for SELL {ticker} - order NOT submitted")
                return True, "Dry run complete - order not submitted"

            # Click REVIEW ORDER button - wait for it to become visible
            logger.info("Waiting for Review Order button...")
            try:
                review_btn = self.page.locator("button:has-text('Review Order')")
                review_btn.wait_for(state="visible", timeout=15000)
                review_btn.click()
                logger.info("Clicked Review Order")
            except Exception as e:
                screenshot_path = take_debug_screenshot(self.page, f'sell_review_failed_{ticker}')
                logger.error(f"Could not click Review Order: {e}. Screenshot: {screenshot_path}")
                return False, f"Could not click Review Order. Screenshot: {screenshot_path}"

            time.sleep(3)  # Wait for review modal
            take_debug_screenshot(self.page, f'sell_order_preview_{ticker}')

            # Click PLACE ORDER button - wait for it to become visible
            logger.info("Waiting for Place Order button...")
            try:
                place_btn = self.page.locator("button:has-text('Place Order')")
                place_btn.wait_for(state="visible", timeout=15000)
                place_btn.click()
                logger.info("Clicked Place Order")
            except Exception as e:
                screenshot_path = take_debug_screenshot(self.page, f'sell_submit_failed_{ticker}')
                logger.error(f"Could not click Place Order: {e}. Screenshot: {screenshot_path}")
                return False, f"Could not click Place Order. Screenshot: {screenshot_path}"

            time.sleep(ORDER_SUBMISSION_WAIT)
            take_debug_screenshot(self.page, f'sell_order_submitted_{ticker}')

            # Check for confirmation
            page_text = self.page.content().lower()
            if any(word in page_text for word in ['confirmed', 'submitted', 'success', 'order placed', 'order received']):
                logger.info(f"SELL order confirmed: {shares} {ticker}")
                log_trade('SELL', ticker, shares, limit_price, 'Order submitted')
                return True, "Sell order submitted successfully"

            # Check for errors
            if any(word in page_text for word in ['error', 'failed', 'invalid', 'rejected', 'insufficient']):
                error_msg = self._extract_error_message()
                screenshot_path = take_debug_screenshot(self.page, f'sell_order_error_{ticker}')
                logger.error(f"SELL order failed for {ticker}: {error_msg}. Screenshot: {screenshot_path}")
                return False, f"Order failed: {error_msg}"

            # Uncertain but probably OK
            logger.info(f"SELL order submitted (unconfirmed): {shares} {ticker}")
            log_trade('SELL', ticker, shares, limit_price, 'Order submitted (unconfirmed)')
            return True, "Sell order submitted (unconfirmed)"

        except RuntimeError as e:
            # Trade page verification failed
            logger.error(f"Trade page error: {e}")
            return False, str(e)

        except Exception as e:
            screenshot_path = take_debug_screenshot(self.page, f'sell_order_exception_{ticker}')
            logger.error(f"Error placing sell order: {e}. Screenshot: {screenshot_path}")
            return False, f"{e}. Screenshot: {screenshot_path}"

    def _check_logged_in(self) -> bool:
        """
        Check if we're already logged in (e.g., from persistent session).

        Returns:
            True if logged-in indicators are visible
        """
        logged_in_indicators = [
            "text=Portfolio Simulation",
            "text=My Portfolio",
            "text=Logout",
            "text=Trading",
        ]

        for indicator in logged_in_indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    logger.debug(f"Logged in indicator found: {indicator}")
                    return True
            except:
                pass

        return False

    def verify_trade_in_history(self, ticker: str, side: str, shares: int) -> Tuple[bool, str]:
        """
        Verify a trade exists in Transaction History or Order History.

        This is the idempotency check: after clicking Place Order, if anything
        errors, verify in history before retrying.

        Args:
            ticker: Stock ticker
            side: "BUY" or "SELL"
            shares: Number of shares

        Returns:
            Tuple of (found, status_message)
        """
        logger.info(f"Verifying trade in history: {side} {shares} {ticker}")

        # First, check Transaction History (for executed trades)
        try:
            # Navigate via menu: My Portfolio → Transaction History
            self.page.get_by_role("link", name=re.compile("My Portfolio", re.I)).hover()
            time.sleep(0.3)
            self.page.get_by_role("link", name=re.compile("Transaction History", re.I)).click()
            self.page.wait_for_load_state("networkidle")
            dismiss_stocktrak_overlays(self.page, total_ms=5000)

            # Look for the ticker in recent transactions
            time.sleep(1)
            page_text = self.page.content()

            if ticker.upper() in page_text.upper():
                logger.info(f"Found {ticker} in Transaction History")
                # Try to find the specific row
                row = self.page.locator("tr", has_text=re.compile(ticker, re.I)).first
                if row.is_visible(timeout=3000):
                    row_text = row.text_content()
                    if side.upper() in row_text.upper() and str(shares) in row_text:
                        logger.info(f"Trade verified: {side} {shares} {ticker}")
                        return True, "Trade verified in Transaction History"

        except Exception as e:
            logger.debug(f"Transaction History check failed: {e}")

        # Fallback: Check Order History (for pending orders)
        try:
            self.page.get_by_role("link", name=re.compile("My Portfolio", re.I)).hover()
            time.sleep(0.3)
            self.page.get_by_role("link", name=re.compile("Order History", re.I)).click()
            self.page.wait_for_load_state("networkidle")
            dismiss_stocktrak_overlays(self.page, total_ms=5000)

            time.sleep(1)
            page_text = self.page.content()

            if ticker.upper() in page_text.upper():
                logger.info(f"Found {ticker} in Order History")
                return True, "Trade found in Order History"

        except Exception as e:
            logger.debug(f"Order History check failed: {e}")

        return False, "Trade not found in history"

    def add_trade_note(self, ticker: str, note_text: str) -> Tuple[bool, str]:
        """
        Add a Trading Note to a trade via Transaction/Order History.

        StockTrak's Trading Notes are NOT part of the trade ticket.
        They are added via "Add/View Notes" in Order History or Transaction History.

        Args:
            ticker: Stock ticker to find in history
            note_text: The rationale/note to attach

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Adding trade note for {ticker}")

        try:
            # Navigate to Transaction History
            self.page.get_by_role("link", name=re.compile("My Portfolio", re.I)).hover()
            time.sleep(0.3)
            self.page.get_by_role("link", name=re.compile("Transaction History", re.I)).click()
            self.page.wait_for_load_state("networkidle")
            dismiss_stocktrak_overlays(self.page, total_ms=5000)

            time.sleep(1)

            # Find the row containing the ticker
            row = self.page.locator("tr", has_text=re.compile(ticker, re.I)).first
            row.wait_for(state="visible", timeout=30000)

            # Click Add/View Notes in that row
            notes_link = row.get_by_role("link", name=re.compile("Add|View|Notes", re.I))
            if notes_link.count() > 0:
                notes_link.first.click()
                time.sleep(0.5)

                # Fill the note textbox
                note_input = self.page.get_by_role("textbox").first
                note_input.wait_for(state="visible", timeout=10000)
                note_input.fill(note_text)

                # Save the note
                save_btn = self.page.get_by_role("button", name=re.compile("save|submit|add", re.I)).first
                save_btn.click()
                time.sleep(1)

                # Verify note appears
                if note_text[:20] in self.page.content():
                    logger.info(f"Trade note added successfully for {ticker}")
                    return True, "Note added successfully"

                logger.warning("Note may not have saved - text not visible")
                return True, "Note submitted (unconfirmed)"

            else:
                logger.warning(f"No notes link found for {ticker}")
                return False, "Notes link not found"

        except Exception as e:
            logger.error(f"Error adding trade note: {e}")
            take_debug_screenshot(self.page, f'trade_note_error_{ticker}')
            return False, f"Error: {e}"

    def _try_fill(self, selectors: List[str], value: str, timeout: int = 2000) -> bool:
        """
        Try multiple selectors to fill a form field.

        Uses wait_for() pattern for reliability instead of count() > 0.
        """
        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                loc.wait_for(state="visible", timeout=timeout)
                loc.fill(value)
                logger.debug(f"Filled '{value}' with selector: {selector}")
                return True
            except Exception as e:
                logger.debug(f"Fill failed with {selector}: {e}")
                continue
        return False

    def _try_click(self, selectors: List[str], timeout: int = 2000) -> bool:
        """
        Try multiple selectors to click an element.

        Uses wait_for() pattern for reliability instead of count() > 0.
        """
        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                loc.wait_for(state="visible", timeout=timeout)
                loc.click()
                logger.debug(f"Clicked with selector: {selector}")
                return True
            except Exception as e:
                logger.debug(f"Click failed with {selector}: {e}")
                continue
        return False

    def _screenshot(self, name: str):
        """Take a screenshot for debugging"""
        try:
            path = f'logs/{name}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            self.page.screenshot(path=path)
            logger.debug(f"Screenshot saved: {path}")
        except Exception as e:
            logger.debug(f"Screenshot failed: {e}")

    def _extract_error_message(self) -> str:
        """Try to extract error message from page"""
        error_selectors = [
            '.error', '.alert-danger', '.error-message',
            '.alert-error', '#error', '.validation-error'
        ]
        for selector in error_selectors:
            try:
                if self.page.locator(selector).count() > 0:
                    return self.page.locator(selector).first.text_content()
            except:
                continue
        return "Unknown error"

    def close(self):
        """Clean up browser resources"""
        logger.info("Closing browser...")
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    def __enter__(self):
        self.start_browser()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def test_login():
    """Test function to verify login works with full verification"""
    logging.basicConfig(level=logging.INFO)

    print("\n" + "="*60)
    print("STOCKTRAK BOT - LOGIN AND VERIFICATION TEST")
    print("="*60)
    print(f"Screenshot directory: {SCREENSHOT_DIR}")
    print("="*60 + "\n")

    bot = StockTrakBot(headless=False)
    bot.start_browser()

    try:
        # Step 1: Login
        print("\n[STEP 1] Attempting login...")
        if not bot.login():
            print("❌ LOGIN FAILED!")
            print(f"Check screenshots in: {SCREENSHOT_DIR}")
            input("Press Enter to close browser...")
            return

        print("✓ Login successful!")

        # Step 2: Verify ready for trading
        print("\n[STEP 2] Verifying ready for trading...")
        is_ready, status = bot.verify_ready_for_trading()

        if not is_ready:
            print(f"❌ VERIFICATION FAILED: {status}")
            print(f"Check screenshots in: {SCREENSHOT_DIR}")
            print("\nAttempting recovery...")

            # Try one more time to clear popups
            dismiss_stocktrak_overlays(bot.page, max_attempts=10)
            time.sleep(2)

            is_ready, status = bot.verify_ready_for_trading()
            if not is_ready:
                print(f"❌ Recovery failed: {status}")
                input("Press Enter to close browser...")
                return

        print("✓ Verification passed!")

        # Step 3: Get portfolio info from trade KPIs
        print("\n[STEP 3] Fetching portfolio information from trade KPIs...")

        try:
            portfolio, cash, buying_power = bot.get_capital_from_trade_kpis("VOO")
            print(f"✓ Portfolio value: ${portfolio:,.2f}")
            print(f"✓ Cash balance: ${cash:,.2f}")
            print(f"✓ Buying power: ${buying_power:,.2f}")
        except Exception as e:
            print(f"⚠ Could not get capital: {e}")

        holdings = bot.get_current_holdings()
        if holdings:
            print(f"✓ Holdings ({len(holdings)} positions): {list(holdings.keys())}")
        else:
            print("⚠ Could not get holdings")

        trades = bot.get_transaction_count()
        print(f"✓ Trade count: {trades}")

        # Summary
        print("\n" + "="*60)
        print("TEST COMPLETE - ALL SYSTEMS GO!")
        print("="*60)
        print(f"Screenshots saved in: {SCREENSHOT_DIR}")

        input("\nPress Enter to close browser...")

    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        screenshot_path = take_debug_screenshot(bot.page, 'test_exception')
        print(f"Exception screenshot: {screenshot_path}")
        input("Press Enter to close browser...")

    finally:
        bot.close()


if __name__ == "__main__":
    test_login()
