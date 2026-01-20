"""
StockTrak Browser Automation for Trading Bot

Uses Playwright to automate interactions with app.stocktrak.com
for portfolio management and trade execution.
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
        self.page.set_default_timeout(DEFAULT_TIMEOUT)

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
            self.page.wait_for_load_state('networkidle')
            time.sleep(2)

            # Screenshot for debugging
            self._screenshot('login_page')

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
                self._screenshot('login_error_username')
                return False

            # Fill password
            password_filled = self._try_fill(password_selectors, self.password)
            if not password_filled:
                logger.error("Could not find password field")
                self._screenshot('login_error_password')
                return False

            time.sleep(0.5)

            # Click submit
            submitted = self._try_click(submit_selectors)
            if not submitted:
                logger.error("Could not find submit button")
                self._screenshot('login_error_submit')
                return False

            # Wait for navigation
            self.page.wait_for_load_state('networkidle')
            time.sleep(3)

            self._screenshot('after_login')

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
            return False

        except Exception as e:
            logger.error(f"Login exception: {e}")
            self._screenshot('login_exception')
            return False

    def get_portfolio_value(self) -> Optional[float]:
        """
        Navigate to portfolio and get total value.

        Returns:
            Portfolio value as float, or None if not found
        """
        try:
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
                    self.page.wait_for_load_state('networkidle')
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

    def place_buy_order(self, ticker: str, shares: int, limit_price: float) -> Tuple[bool, str]:
        """
        Place a limit buy order.

        Args:
            ticker: Stock ticker symbol
            shares: Number of shares to buy
            limit_price: Limit price per share

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Placing BUY order: {shares} {ticker} @ ${limit_price:.2f}")

        try:
            # Navigate to trade page
            trade_urls = [
                f"{self.base_url}/trading/stocks",
                f"{self.base_url}/trade",
                f"{self.base_url}/trading",
                f"{self.base_url}/trade/stocks",
                f"{self.base_url}/order",
            ]

            for url in trade_urls:
                try:
                    self.page.goto(url)
                    self.page.wait_for_load_state('networkidle')
                    if any(x in self.page.url.lower() for x in ['trade', 'order']):
                        break
                except:
                    continue

            time.sleep(2)
            self._screenshot(f'trade_page_{ticker}')

            # Fill symbol
            symbol_selectors = [
                'input[name="symbol"]',
                'input[name="ticker"]',
                '#symbol',
                '#ticker',
                'input[placeholder*="symbol" i]',
                'input[placeholder*="ticker" i]',
                'input[aria-label*="symbol" i]',
            ]
            self._try_fill(symbol_selectors, ticker)
            time.sleep(1)

            # Select Buy action
            action_selectors = [
                'select[name="action"]',
                '#action',
                'select[name="side"]',
                '#side',
            ]
            for selector in action_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.select_option(selector, value='buy')
                        break
                except:
                    try:
                        self.page.select_option(selector, label='Buy')
                        break
                    except:
                        continue

            # Also try radio buttons or tabs for buy/sell
            buy_button_selectors = [
                'input[value="buy"]',
                'button:has-text("Buy")',
                'label:has-text("Buy")',
                '[data-action="buy"]',
            ]
            self._try_click(buy_button_selectors)

            # Fill quantity
            qty_selectors = [
                'input[name="quantity"]',
                'input[name="shares"]',
                '#quantity',
                '#shares',
                'input[placeholder*="quantity" i]',
                'input[placeholder*="shares" i]',
            ]
            self._try_fill(qty_selectors, str(shares))

            # Select Limit order type
            type_selectors = [
                'select[name="orderType"]',
                'select[name="order_type"]',
                'select[name="type"]',
                '#orderType',
                '#order_type',
            ]
            for selector in type_selectors:
                try:
                    self.page.select_option(selector, value='limit')
                    break
                except:
                    try:
                        self.page.select_option(selector, label='Limit')
                        break
                    except:
                        continue

            # Also try radio/button for order type
            limit_selectors = [
                'input[value="limit"]',
                'button:has-text("Limit")',
                'label:has-text("Limit")',
            ]
            self._try_click(limit_selectors)

            # Fill limit price
            price_selectors = [
                'input[name="limitPrice"]',
                'input[name="limit_price"]',
                'input[name="price"]',
                '#limitPrice',
                '#limit_price',
                '#price',
                'input[placeholder*="price" i]',
            ]
            self._try_fill(price_selectors, f'{limit_price:.2f}')

            # Select Day order duration
            duration_selectors = [
                'select[name="duration"]',
                'select[name="timeInForce"]',
                '#duration',
                '#timeInForce',
            ]
            for selector in duration_selectors:
                try:
                    self.page.select_option(selector, value='day')
                    break
                except:
                    try:
                        self.page.select_option(selector, label='Day')
                        break
                    except:
                        continue

            time.sleep(1)
            self._screenshot(f'order_filled_{ticker}')

            # Preview order (if available)
            preview_selectors = [
                'button:has-text("Preview")',
                'button:has-text("Review")',
                '#preview-btn',
                'input[value="Preview"]',
            ]
            self._try_click(preview_selectors)
            time.sleep(2)
            self._screenshot(f'order_preview_{ticker}')

            # Submit order
            submit_selectors = [
                'button:has-text("Submit")',
                'button:has-text("Place Order")',
                'button:has-text("Confirm")',
                'button:has-text("Execute")',
                '#submit-btn',
                '#place-order',
                'input[value="Submit"]',
                'button[type="submit"]',
            ]
            submitted = self._try_click(submit_selectors)

            if not submitted:
                logger.error(f"Could not find submit button for {ticker}")
                return False, "Could not find submit button"

            time.sleep(ORDER_SUBMISSION_WAIT)
            self._screenshot(f'order_submitted_{ticker}')

            # Check for confirmation
            page_text = self.page.content().lower()
            if any(word in page_text for word in ['confirmed', 'submitted', 'success', 'order placed']):
                logger.info(f"BUY order confirmed: {shares} {ticker}")
                log_trade('BUY', ticker, shares, limit_price, 'Order submitted')
                return True, "Order submitted successfully"

            # Check for errors
            if any(word in page_text for word in ['error', 'failed', 'invalid', 'rejected', 'insufficient']):
                error_msg = self._extract_error_message()
                logger.error(f"Order may have failed for {ticker}: {error_msg}")
                return False, f"Order failed: {error_msg}"

            # Uncertain but probably OK
            logger.info(f"BUY order submitted (unconfirmed): {shares} {ticker}")
            log_trade('BUY', ticker, shares, limit_price, 'Order submitted (unconfirmed)')
            return True, "Order submitted (unconfirmed)"

        except Exception as e:
            logger.error(f"Error placing buy order: {e}")
            self._screenshot(f'order_error_{ticker}')
            return False, str(e)

    def place_sell_order(self, ticker: str, shares: int, limit_price: float) -> Tuple[bool, str]:
        """
        Place a limit sell order.

        Args:
            ticker: Stock ticker symbol
            shares: Number of shares to sell
            limit_price: Limit price per share

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Placing SELL order: {shares} {ticker} @ ${limit_price:.2f}")

        try:
            # Navigate to trade page
            self.page.goto(f"{self.base_url}/trading/stocks")
            self.page.wait_for_load_state('networkidle')
            time.sleep(2)

            # Fill symbol
            symbol_selectors = [
                'input[name="symbol"]',
                'input[name="ticker"]',
                '#symbol',
                '#ticker',
            ]
            self._try_fill(symbol_selectors, ticker)
            time.sleep(1)

            # Select Sell action
            action_selectors = [
                'select[name="action"]',
                '#action',
                'select[name="side"]',
            ]
            for selector in action_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.select_option(selector, value='sell')
                        break
                except:
                    try:
                        self.page.select_option(selector, label='Sell')
                        break
                    except:
                        continue

            # Try sell button
            sell_button_selectors = [
                'input[value="sell"]',
                'button:has-text("Sell")',
                'label:has-text("Sell")',
            ]
            self._try_click(sell_button_selectors)

            # Fill quantity
            qty_selectors = [
                'input[name="quantity"]',
                'input[name="shares"]',
                '#quantity',
                '#shares',
            ]
            self._try_fill(qty_selectors, str(shares))

            # Select Limit order
            type_selectors = [
                'select[name="orderType"]',
                'select[name="order_type"]',
                '#orderType',
            ]
            for selector in type_selectors:
                try:
                    self.page.select_option(selector, value='limit')
                    break
                except:
                    continue

            # Fill price
            price_selectors = [
                'input[name="limitPrice"]',
                'input[name="price"]',
                '#limitPrice',
                '#price',
            ]
            self._try_fill(price_selectors, f'{limit_price:.2f}')

            # Duration = Day
            duration_selectors = ['select[name="duration"]', '#duration']
            for selector in duration_selectors:
                try:
                    self.page.select_option(selector, value='day')
                    break
                except:
                    continue

            time.sleep(1)
            self._screenshot(f'sell_order_filled_{ticker}')

            # Preview
            self._try_click(['button:has-text("Preview")', 'button:has-text("Review")'])
            time.sleep(2)

            # Submit
            submit_selectors = [
                'button:has-text("Submit")',
                'button:has-text("Place Order")',
                'button:has-text("Confirm")',
                '#submit-btn',
            ]
            self._try_click(submit_selectors)

            time.sleep(ORDER_SUBMISSION_WAIT)
            self._screenshot(f'sell_order_submitted_{ticker}')

            # Check result
            page_text = self.page.content().lower()
            if any(word in page_text for word in ['confirmed', 'submitted', 'success']):
                logger.info(f"SELL order confirmed: {shares} {ticker}")
                log_trade('SELL', ticker, shares, limit_price, 'Order submitted')
                return True, "Sell order submitted successfully"

            if any(word in page_text for word in ['error', 'failed', 'invalid']):
                return False, "Sell order may have failed"

            return True, "Sell order submitted (unconfirmed)"

        except Exception as e:
            logger.error(f"Error placing sell order: {e}")
            self._screenshot(f'sell_order_error_{ticker}')
            return False, str(e)

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
    """Test function to verify login works"""
    logging.basicConfig(level=logging.INFO)

    bot = StockTrakBot(headless=False)
    bot.start_browser()

    try:
        if bot.login():
            print("LOGIN SUCCESS!")
            value = bot.get_portfolio_value()
            print(f"Portfolio value: ${value:,.2f}" if value else "Could not get portfolio value")

            holdings = bot.get_current_holdings()
            print(f"Holdings: {holdings}")

            trades = bot.get_transaction_count()
            print(f"Trade count: {trades}")
        else:
            print("LOGIN FAILED!")

        input("Press Enter to close browser...")

    finally:
        bot.close()


if __name__ == "__main__":
    test_login()
