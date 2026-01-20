"""
Utility Functions for StockTrak Bot
"""

import re
import logging
from datetime import datetime, timedelta
import pytz

# Timezone
ET = pytz.timezone('US/Eastern')

logger = logging.getLogger('stocktrak_bot.utils')


def parse_currency(text):
    """
    Parse currency string to float
    Examples: "$1,234.56" -> 1234.56, "1234.56" -> 1234.56
    """
    if not text:
        return None
    try:
        # Remove currency symbols, commas, spaces
        cleaned = text.replace('$', '').replace(',', '').replace(' ', '')
        # Find the number (handles negative numbers too)
        match = re.search(r'-?[\d.]+', cleaned)
        if match:
            return float(match.group())
    except (ValueError, AttributeError):
        pass
    return None


def parse_number(text):
    """
    Parse number string to int
    Examples: "1,234" -> 1234, "1234" -> 1234
    """
    if not text:
        return 0
    try:
        cleaned = text.replace(',', '').replace(' ', '')
        match = re.search(r'-?[\d]+', cleaned)
        if match:
            return int(match.group())
    except (ValueError, AttributeError):
        pass
    return 0


def parse_percentage(text):
    """
    Parse percentage string to float
    Examples: "12.34%" -> 0.1234, "-5.5%" -> -0.055
    """
    if not text:
        return None
    try:
        cleaned = text.replace('%', '').replace(',', '').replace(' ', '')
        match = re.search(r'-?[\d.]+', cleaned)
        if match:
            return float(match.group()) / 100.0
    except (ValueError, AttributeError):
        pass
    return None


def is_market_hours():
    """Check if US stock market is currently open"""
    now = datetime.now(ET)

    # Weekend check
    if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False

    # Time check (9:30 AM - 4:00 PM ET)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now <= market_close


def is_trading_day(date=None):
    """Check if given date is a trading day (excludes weekends and holidays)"""
    if date is None:
        date = datetime.now(ET).date()

    # Weekend check
    if date.weekday() >= 5:
        return False

    # US Market Holidays 2026 (approximate - verify closer to date)
    holidays_2026 = [
        datetime(2026, 1, 1).date(),   # New Year's Day
        datetime(2026, 1, 19).date(),  # MLK Day
        datetime(2026, 2, 16).date(),  # Presidents' Day
        # Competition ends Feb 20, so no need for later holidays
    ]

    if date in holidays_2026:
        return False

    return True


def get_next_trading_day(date=None):
    """Get the next trading day from given date"""
    if date is None:
        date = datetime.now(ET).date()

    next_day = date + timedelta(days=1)
    while not is_trading_day(next_day):
        next_day += timedelta(days=1)

    return next_day


def get_trading_days_between(start_date, end_date):
    """Count trading days between two dates (exclusive of end date)"""
    count = 0
    current = start_date
    while current < end_date:
        if is_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


def get_current_time_et():
    """Get current time in Eastern timezone"""
    return datetime.now(ET)


def is_execution_window():
    """Check if we're in the execution window (3:50 PM - 3:59 PM ET)"""
    now = datetime.now(ET)
    execution_start = now.replace(hour=15, minute=50, second=0, microsecond=0)
    execution_end = now.replace(hour=15, minute=59, second=59, microsecond=999999)
    return execution_start <= now <= execution_end


def format_currency(value):
    """Format number as currency string"""
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def format_percentage(value):
    """Format decimal as percentage string"""
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def format_shares(shares):
    """Format share count"""
    if shares is None:
        return "N/A"
    return f"{shares:,}"


def calculate_limit_price(current_price, is_buy=True, buffer_pct=0.002):
    """
    Calculate limit price with buffer
    For buys: slightly above market (to ensure fill)
    For sells: slightly below market (to ensure fill)
    """
    if is_buy:
        return round(current_price * (1 + buffer_pct), 2)
    else:
        return round(current_price * (1 - buffer_pct), 2)


def calculate_shares_for_allocation(portfolio_value, target_pct, price, commission=5.00):
    """
    Calculate number of shares to buy for a target allocation
    Accounts for commission costs
    """
    target_value = portfolio_value * target_pct
    effective_value = target_value - commission  # Account for commission
    if effective_value <= 0 or price <= 0:
        return 0
    return int(effective_value / price)


def sanitize_ticker(ticker):
    """Clean and validate ticker symbol"""
    if not ticker:
        return None
    # Remove whitespace and convert to uppercase
    cleaned = ticker.strip().upper()
    # Basic validation: 1-5 characters, alphanumeric
    if re.match(r'^[A-Z]{1,5}$', cleaned):
        return cleaned
    return None


def setup_logging(log_level='INFO', log_file='logs/trading_bot.log'):
    """Configure logging for the bot"""
    import os

    # Create logs directory if needed
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    # Also create a trade-specific logger
    trade_logger = logging.getLogger('trades')
    trade_handler = logging.FileHandler('logs/trades.log')
    trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    trade_logger.addHandler(trade_handler)

    return logging.getLogger('stocktrak_bot')


def log_trade(action, ticker, shares, price, reason=''):
    """Log a trade to the trades log"""
    trade_logger = logging.getLogger('trades')
    trade_logger.info(f"{action} | {ticker} | {shares} shares | ${price:.2f} | {reason}")


class RetryHandler:
    """Handle retries with exponential backoff"""

    def __init__(self, max_retries=4, base_delay=2.0):
        self.max_retries = max_retries
        self.base_delay = base_delay

    def execute(self, func, *args, **kwargs):
        """Execute function with retry logic"""
        import time

        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"All {self.max_retries + 1} attempts failed")

        raise last_exception
