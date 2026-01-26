"""
StockTrak Browser Automation for Trading Bot

Uses Playwright to automate interactions with app.stocktrak.com
for portfolio management and trade execution.

UPDATED: Robust popup handling, page verification, and error recovery.
"""

import logging
import time
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

from config import (
    STOCKTRAK_URL, STOCKTRAK_LOGIN_URL, STOCKTRAK_USERNAME, STOCKTRAK_PASSWORD,
    HEADLESS_MODE, SLOW_MO, DEFAULT_TIMEOUT, ORDER_SUBMISSION_WAIT,
    SCREENSHOT_ON_ERROR, SCREENSHOT_ON_TRADE
)
from utils import parse_currency, parse_number, log_trade

logger = logging.getLogger('stocktrak_bot.browser')

# Ensure logs directory exists
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def dismiss_stocktrak_overlays(page, max_attempts: int = 5) -> int:
    """
    Aggressively dismiss ALL popups/modals that block interaction.

    Handles:
    - Robinhood promo modal
    - Site tours (multiple steps)
    - Cookie notices
    - Any modal/overlay with close buttons

    Args:
        page: Playwright page object
        max_attempts: Maximum number of dismiss cycles

    Returns:
        Number of popups dismissed
    """
    dismissed_count = 0

    # All known selectors for StockTrak popups
    all_dismiss_selectors = [
        # Robinhood promo modal (exact IDs from inspection)
        "#btn-dont-show-again",
        "#btn-remindlater",
        "#OverlayModalPopup button",
        "#OverlayModalPopup a.button",

        # Generic Ok/Close buttons
        "button:has-text('Ok')",
        "button:has-text('OK')",
        "button:has-text('Close')",
        "button:has-text('Done')",
        "button:has-text('Got it')",
        "button:has-text('Dismiss')",
        "a:has-text('Ok')",
        "a:has-text('OK')",
        "a:has-text('Close')",
        "a:has-text(\"Don't Show Again\")",
        "a:has-text(\"Remind Me Later\")",

        # Site tour selectors
        "button:has-text('Skip')",
        "button:has-text('Skip Tour')",
        "button:has-text('Skip tour')",
        "button:has-text('End Tour')",
        "button:has-text('End tour')",
        "button:has-text('No Thanks')",
        "button:has-text('No thanks')",
        "button:has-text('Maybe Later')",
        "button:has-text('Next')",  # Click through tour if Skip not available
        "a:has-text('Skip')",
        "a:has-text('Skip Tour')",
        "a:has-text('No Thanks')",

        # Tour library specific (Intro.js, Shepherd.js, Hopscotch)
        ".introjs-skipbutton",
        ".introjs-donebutton",
        ".introjs-button.introjs-skipbutton",
        ".shepherd-cancel-icon",
        ".shepherd-button-secondary",
        ".shepherd-button:has-text('Skip')",
        ".shepherd-button:has-text('Exit')",
        ".hopscotch-bubble-close",
        ".hopscotch-cta:has-text('Skip')",
        ".tour-skip",
        ".tour-close",
        ".tour-end",
        ".walkthrough-skip",
        ".walkthrough-close",

        # Generic modal close buttons
        ".modal .close",
        ".modal-close",
        ".modal .btn-close",
        ".modal [aria-label='Close']",
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "[aria-label='Close']",
        "[aria-label='close']",
        "button.close",
        "button.close-button",
        ".close-button",
        ".btn-close",
        "button:has-text('×')",
        "a:has-text('×')",
        ".modal button.btn-secondary",

        # Overlay/backdrop clicks (last resort)
        ".modal-backdrop",
        ".overlay-close",
    ]

    for attempt in range(max_attempts):
        found_any = False

        for sel in all_dismiss_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=300):
                    try:
                        loc.click(timeout=800)
                        dismissed_count += 1
                        found_any = True
                        logger.info(f"Dismissed popup #{dismissed_count} using: {sel}")
                        time.sleep(0.3)
                    except Exception as click_err:
                        logger.debug(f"Click failed for {sel}: {click_err}")
            except:
                pass

        # Try ESC key
        try:
            page.keyboard.press("Escape")
            time.sleep(0.2)
        except:
            pass

        # Try clicking outside modals (on body)
        try:
            # Click at top-left corner which is usually safe
            page.mouse.click(10, 10)
            time.sleep(0.2)
        except:
            pass

        # If we found and dismissed something, do another pass
        # If nothing found, we're done
        if not found_any:
            break

        time.sleep(0.3)

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

    def start_browser(self, headless: bool = None):
        """
        Start browser with configured settings.

        Args:
            headless: Override headless setting
        """
        if headless is not None:
            self.headless = headless

        logger.info(f"Starting browser (headless={self.headless})...")

        self.playwright = sync_playwright().start()
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

        Returns:
            True if login successful, False otherwise
        """
        logger.info("Attempting login to StockTrak...")

        try:
            # Navigate to login page
            self.page.goto(STOCKTRAK_LOGIN_URL)
            self.page.wait_for_load_state('domcontentloaded')
            time.sleep(2)

            # CRITICAL: Dismiss any popups that appear on login page
            logger.info("Dismissing any popups on login page...")
            dismiss_stocktrak_overlays(self.page, max_attempts=5)
            time.sleep(1)

            # Screenshot for debugging
            screenshot_path = take_debug_screenshot(self.page, 'login_page')

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

            # Wait for navigation - use domcontentloaded (faster than networkidle)
            logger.info("Waiting for page to load after login...")
            self.page.wait_for_load_state('domcontentloaded')
            time.sleep(2)

            # CRITICAL: Clear ALL popups with aggressive loop
            logger.info("=== CLEARING ALL POPUPS (this may take a moment) ===")
            total_dismissed = 0

            for pass_num in range(5):  # Up to 5 passes
                logger.info(f"Popup clearing pass {pass_num + 1}/5...")
                dismissed = dismiss_stocktrak_overlays(self.page, max_attempts=3)
                total_dismissed += dismissed
                take_debug_screenshot(self.page, f'after_popup_pass_{pass_num + 1}')
                time.sleep(1)

                # If no popups found in this pass, we might be done
                if dismissed == 0:
                    # But wait a bit and try once more in case of delayed popups
                    time.sleep(2)
                    final_check = dismiss_stocktrak_overlays(self.page, max_attempts=2)
                    total_dismissed += final_check
                    if final_check == 0:
                        logger.info("No more popups detected")
                        break

            logger.info(f"=== TOTAL POPUPS DISMISSED: {total_dismissed} ===")
            take_debug_screenshot(self.page, 'after_all_popups_cleared')

            # Verify page is ready
            is_ready, status = verify_page_ready(self.page, expected_url_contains='dashboard')
            if not is_ready:
                logger.warning(f"Page verification: {status}")
                # Try one more aggressive popup clear
                dismiss_stocktrak_overlays(self.page, max_attempts=5)

            # Check for success indicators
            success_indicators = [
                'portfolio', 'dashboard', 'home', 'account',
                'holdings', 'trade', 'positions', 'overview'
            ]

            current_url = self.page.url.lower()
            page_content = self.page.content().lower()

            for indicator in success_indicators:
                if indicator in current_url or f'>{indicator}' in page_content:
                    self.logged_in = True
                    logger.info(f"Login successful! (found: {indicator})")
                    return True

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
            screenshot_path = take_debug_screenshot(self.page, 'login_uncertain')
            logger.warning(f"Uncertain state screenshot: {screenshot_path}")
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
        Get total number of executed trades.
        CRITICAL for staying under 80 trade limit.

        Returns:
            Number of trades executed
        """
        try:
            # Dismiss any popups first
            dismiss_stocktrak_overlays(self.page)

            # Navigate to transaction history
            transaction_urls = [
                f"{self.base_url}/portfolio/transactions",
                f"{self.base_url}/portfolio/history",
                f"{self.base_url}/trading/history",
                f"{self.base_url}/account/transactions",
                f"{self.base_url}/history",
            ]

            for url in transaction_urls:
                try:
                    self.page.goto(url)
                    self.page.wait_for_load_state('networkidle')
                    if any(x in self.page.url.lower() for x in ['transaction', 'history']):
                        break
                except:
                    continue

            time.sleep(2)
            self._screenshot('transactions_page')

            # Count transaction rows
            row_selectors = [
                '.transaction-row',
                '.trade-row',
                '.history-row',
                'table tbody tr',
                'table tr:not(:first-child)',
            ]

            for selector in row_selectors:
                try:
                    rows = self.page.locator(selector).all()
                    # Filter to actual trade rows (not headers, not empty)
                    count = 0
                    for row in rows:
                        text = row.text_content().lower()
                        if any(word in text for word in ['buy', 'sell', 'bought', 'sold']):
                            count += 1
                    if count > 0:
                        logger.info(f"Transaction count: {count}")
                        return count
                except:
                    continue

            # Alternative: look for trade count display
            count_selectors = [
                '.trade-count',
                '.transaction-count',
                '#trades-used',
            ]
            for selector in count_selectors:
                try:
                    elem = self.page.locator(selector).first
                    count = parse_number(elem.text_content())
                    if count > 0:
                        return count
                except:
                    continue

            logger.warning("Could not determine transaction count, returning 0")
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

        try:
            # Dismiss any popups first
            dismiss_stocktrak_overlays(self.page)

            # Navigate to CORRECT trade page URL
            trade_url = self._trade_equities_url(ticker)
            logger.info(f"Navigating to: {trade_url}")
            self.page.goto(trade_url)
            self.page.wait_for_load_state('domcontentloaded')
            time.sleep(2)

            # Dismiss any popups on trade page
            dismiss_stocktrak_overlays(self.page, max_attempts=3)

            # CRITICAL: Verify we're on the trade page
            self.assert_on_trade_page(ticker)

            take_debug_screenshot(self.page, f'trade_page_{ticker}')

            # The symbol should already be filled from URL parameter
            # But verify/fill if needed
            symbol_selectors = [
                'input[name="symbol"]',
                'input[name="ticker"]',
                '#symbol',
                '#ticker',
                'input[placeholder*="symbol" i]',
                'input[placeholder*="ticker" i]',
                'input[aria-label*="symbol" i]',
            ]

            # Check if symbol is already filled
            symbol_filled = False
            for sel in symbol_selectors:
                try:
                    elem = self.page.locator(sel).first
                    if elem.is_visible(timeout=500):
                        current_val = elem.input_value()
                        if ticker.upper() in current_val.upper():
                            symbol_filled = True
                            break
                except:
                    continue

            if not symbol_filled:
                self._try_fill(symbol_selectors, ticker)
                time.sleep(1)

            # Select Buy action
            buy_selectors = [
                'input[value="buy"]',
                'input[type="radio"][value="buy"]',
                'button:has-text("Buy")',
                'label:has-text("Buy")',
                '[data-action="buy"]',
                '#buy-radio',
                '.buy-button',
            ]
            self._try_click(buy_selectors)

            # Also try select dropdown
            action_selectors = ['select[name="action"]', '#action', 'select[name="side"]']
            for selector in action_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.select_option(selector, value='buy')
                        break
                except:
                    pass

            # Fill quantity
            qty_selectors = [
                'input[name="quantity"]',
                'input[name="shares"]',
                'input[name="qty"]',
                '#quantity',
                '#shares',
                '#qty',
                'input[placeholder*="quantity" i]',
                'input[placeholder*="shares" i]',
                'input[type="number"]',
            ]
            self._try_fill(qty_selectors, str(shares))

            # Select Limit order type
            limit_selectors = [
                'input[value="limit"]',
                'input[type="radio"][value="limit"]',
                'button:has-text("Limit")',
                'label:has-text("Limit")',
                '#limit-radio',
            ]
            self._try_click(limit_selectors)

            # Also try select dropdown
            type_selectors = ['select[name="orderType"]', 'select[name="order_type"]', '#orderType']
            for selector in type_selectors:
                try:
                    self.page.select_option(selector, value='limit')
                    break
                except:
                    pass

            # Fill limit price
            price_selectors = [
                'input[name="limitPrice"]',
                'input[name="limit_price"]',
                'input[name="price"]',
                '#limitPrice',
                '#limit_price',
                '#price',
                'input[placeholder*="price" i]',
                'input[placeholder*="limit" i]',
            ]
            self._try_fill(price_selectors, f'{limit_price:.2f}')

            # Select Day order duration
            duration_selectors = ['select[name="duration"]', 'select[name="timeInForce"]', '#duration']
            for selector in duration_selectors:
                try:
                    self.page.select_option(selector, value='day')
                    break
                except:
                    pass

            time.sleep(1)
            take_debug_screenshot(self.page, f'order_filled_{ticker}')

            # DRY RUN: Stop here without submitting
            if dry_run:
                logger.info(f"DRY RUN complete for {ticker} - order NOT submitted")
                return True, "Dry run complete - order not submitted"

            # Preview order (if available)
            preview_selectors = [
                'button:has-text("Preview")',
                'button:has-text("Review")',
                'button:has-text("Review Order")',
                '#preview-btn',
                '#review-btn',
                'input[value="Preview"]',
            ]
            self._try_click(preview_selectors)
            time.sleep(2)
            take_debug_screenshot(self.page, f'order_preview_{ticker}')

            # Submit order
            submit_selectors = [
                'button:has-text("Submit")',
                'button:has-text("Place Order")',
                'button:has-text("Confirm")',
                'button:has-text("Execute")',
                'button:has-text("Submit Order")',
                '#submit-btn',
                '#place-order',
                'input[value="Submit"]',
                'button[type="submit"]',
            ]
            submitted = self._try_click(submit_selectors)

            if not submitted:
                screenshot_path = take_debug_screenshot(self.page, f'order_submit_failed_{ticker}')
                logger.error(f"Could not find submit button for {ticker}. Screenshot: {screenshot_path}")
                return False, f"Could not find submit button. Screenshot: {screenshot_path}"

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

        try:
            # Dismiss any popups first
            dismiss_stocktrak_overlays(self.page)

            # Navigate to CORRECT trade page URL
            trade_url = self._trade_equities_url(ticker)
            logger.info(f"Navigating to: {trade_url}")
            self.page.goto(trade_url)
            self.page.wait_for_load_state('domcontentloaded')
            time.sleep(2)

            # Dismiss any popups on trade page
            dismiss_stocktrak_overlays(self.page, max_attempts=3)

            # CRITICAL: Verify we're on the trade page
            self.assert_on_trade_page(ticker)

            take_debug_screenshot(self.page, f'sell_trade_page_{ticker}')

            # The symbol should already be filled from URL parameter
            # But verify/fill if needed
            symbol_selectors = [
                'input[name="symbol"]',
                'input[name="ticker"]',
                '#symbol',
                '#ticker',
                'input[placeholder*="symbol" i]',
                'input[placeholder*="ticker" i]',
            ]

            # Check if symbol is already filled
            symbol_filled = False
            for sel in symbol_selectors:
                try:
                    elem = self.page.locator(sel).first
                    if elem.is_visible(timeout=500):
                        current_val = elem.input_value()
                        if ticker.upper() in current_val.upper():
                            symbol_filled = True
                            break
                except:
                    continue

            if not symbol_filled:
                self._try_fill(symbol_selectors, ticker)
                time.sleep(1)

            # Select Sell action
            sell_selectors = [
                'input[value="sell"]',
                'input[type="radio"][value="sell"]',
                'button:has-text("Sell")',
                'label:has-text("Sell")',
                '[data-action="sell"]',
                '#sell-radio',
                '.sell-button',
            ]
            self._try_click(sell_selectors)

            # Also try select dropdown
            action_selectors = ['select[name="action"]', '#action', 'select[name="side"]']
            for selector in action_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.select_option(selector, value='sell')
                        break
                except:
                    pass

            # Fill quantity
            qty_selectors = [
                'input[name="quantity"]',
                'input[name="shares"]',
                'input[name="qty"]',
                '#quantity',
                '#shares',
                '#qty',
                'input[placeholder*="quantity" i]',
                'input[placeholder*="shares" i]',
                'input[type="number"]',
            ]
            self._try_fill(qty_selectors, str(shares))

            # Select Limit order type
            limit_selectors = [
                'input[value="limit"]',
                'input[type="radio"][value="limit"]',
                'button:has-text("Limit")',
                'label:has-text("Limit")',
                '#limit-radio',
            ]
            self._try_click(limit_selectors)

            # Also try select dropdown
            type_selectors = ['select[name="orderType"]', 'select[name="order_type"]', '#orderType']
            for selector in type_selectors:
                try:
                    self.page.select_option(selector, value='limit')
                    break
                except:
                    pass

            # Fill limit price
            price_selectors = [
                'input[name="limitPrice"]',
                'input[name="limit_price"]',
                'input[name="price"]',
                '#limitPrice',
                '#limit_price',
                '#price',
                'input[placeholder*="price" i]',
                'input[placeholder*="limit" i]',
            ]
            self._try_fill(price_selectors, f'{limit_price:.2f}')

            # Select Day order duration
            duration_selectors = ['select[name="duration"]', 'select[name="timeInForce"]', '#duration']
            for selector in duration_selectors:
                try:
                    self.page.select_option(selector, value='day')
                    break
                except:
                    pass

            time.sleep(1)
            take_debug_screenshot(self.page, f'sell_order_filled_{ticker}')

            # DRY RUN: Stop here without submitting
            if dry_run:
                logger.info(f"DRY RUN complete for SELL {ticker} - order NOT submitted")
                return True, "Dry run complete - order not submitted"

            # Preview order (if available)
            preview_selectors = [
                'button:has-text("Preview")',
                'button:has-text("Review")',
                'button:has-text("Review Order")',
                '#preview-btn',
                '#review-btn',
                'input[value="Preview"]',
            ]
            self._try_click(preview_selectors)
            time.sleep(2)
            take_debug_screenshot(self.page, f'sell_order_preview_{ticker}')

            # Submit order
            submit_selectors = [
                'button:has-text("Submit")',
                'button:has-text("Place Order")',
                'button:has-text("Confirm")',
                'button:has-text("Execute")',
                'button:has-text("Submit Order")',
                '#submit-btn',
                '#place-order',
                'input[value="Submit"]',
                'button[type="submit"]',
            ]
            submitted = self._try_click(submit_selectors)

            if not submitted:
                screenshot_path = take_debug_screenshot(self.page, f'sell_submit_failed_{ticker}')
                logger.error(f"Could not find submit button for SELL {ticker}. Screenshot: {screenshot_path}")
                return False, f"Could not find submit button. Screenshot: {screenshot_path}"

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

    def _try_fill(self, selectors: List[str], value: str) -> bool:
        """Try multiple selectors to fill a form field"""
        for selector in selectors:
            try:
                if self.page.locator(selector).count() > 0:
                    self.page.fill(selector, value)
                    logger.debug(f"Filled '{value}' with selector: {selector}")
                    return True
            except Exception as e:
                logger.debug(f"Fill failed with {selector}: {e}")
                continue
        return False

    def _try_click(self, selectors: List[str]) -> bool:
        """Try multiple selectors to click an element"""
        for selector in selectors:
            try:
                if self.page.locator(selector).count() > 0:
                    self.page.click(selector)
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

        # Step 3: Get portfolio info
        print("\n[STEP 3] Fetching portfolio information...")

        value = bot.get_portfolio_value()
        if value:
            print(f"✓ Portfolio value: ${value:,.2f}")
        else:
            print("⚠ Could not get portfolio value")

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
