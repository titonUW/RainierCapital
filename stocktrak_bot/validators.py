"""
Pre-Trade Validation for StockTrak Bot
Ensures all competition rules are followed before executing trades.

UPDATED (24-Hour Hold Patch):
- Timestamp-based 24-hour holding period enforcement (not date-based)
- Structural 1/N diversification: exactly 1 satellite per bucket
- 8 buckets × 5% = 40% satellite allocation
- No position exceeds 25% at buy (max is 25% core, 5% satellite)
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional, List

from config import (
    PROHIBITED_TICKERS, PROHIBITED_SUFFIXES, SAFETY_BUFFER_PRICE,
    MIN_PRICE_AT_BUY, MAX_SINGLE_POSITION_PCT, MIN_HOLDINGS,
    MAX_TRADES_TOTAL, HARD_STOP_TRADES, MIN_HOLD_SECONDS, HOLD_BUFFER_SECONDS,
    CORE_POSITIONS, SATELLITE_BUCKETS, MAX_PER_BUCKET, MIN_BUCKETS,
    EVENT_FREEZE_DATES, REGIME_PARAMS, get_bucket_for_ticker
)
from utils import is_trading_day, get_trading_days_between

logger = logging.getLogger('stocktrak_bot.validators')


def _parse_timestamp_utc(ts: str) -> Optional[datetime]:
    """
    Parse a timestamp string to UTC datetime.

    Handles:
    - ISO format with timezone
    - ISO format with Z suffix
    - Naive timestamps (assumed local, converted to UTC)

    Args:
        ts: Timestamp string

    Returns:
        datetime in UTC or None if unparseable
    """
    if not ts:
        return None

    # Handle trailing Z (e.g., 2026-01-20T14:30:00Z)
    ts = ts.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return None

    # If naive, assume local system timezone (safe for historical logs)
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)

    return dt.astimezone(timezone.utc)


def is_prohibited(ticker: str) -> bool:
    """
    Check if a ticker is prohibited (leveraged, inverse, crypto ETFs, OTC, foreign)

    Args:
        ticker: Stock ticker symbol

    Returns:
        True if prohibited, False if allowed
    """
    if not ticker:
        return True

    ticker_upper = ticker.upper().strip()

    # Check explicit prohibition list
    if ticker_upper in PROHIBITED_TICKERS:
        logger.warning(f"PROHIBITED: {ticker} is in prohibited list")
        return True

    # Check prohibited suffixes (OTC/Foreign)
    for suffix in PROHIBITED_SUFFIXES:
        if suffix in ticker_upper:
            logger.warning(f"PROHIBITED: {ticker} has prohibited suffix {suffix}")
            return True

    return False


def validate_price(ticker: str, price: float, is_buy: bool = True) -> Tuple[bool, str]:
    """
    Validate price meets competition requirements

    Args:
        ticker: Stock ticker symbol
        price: Current price
        is_buy: True if checking for buy, False for sell

    Returns:
        Tuple of (is_valid, reason)
    """
    if price is None:
        return False, "Price is None"

    if is_buy:
        # For buys, use safety buffer (SAFETY_BUFFER_PRICE = $6)
        if price < SAFETY_BUFFER_PRICE:
            return False, f"Price ${price:.2f} below safety buffer ${SAFETY_BUFFER_PRICE:.2f}"
    else:
        # For existing positions, warn if approaching $5 limit
        if price < MIN_PRICE_AT_BUY:
            return False, f"Price ${price:.2f} below minimum ${MIN_PRICE_AT_BUY:.2f} - MUST SELL"
        elif price < 5.50:
            return True, f"WARNING: Price ${price:.2f} approaching $5 limit"

    return True, "Price OK"


def validate_share_quantity(shares: int, ticker: str = None) -> Tuple[bool, str]:
    """
    Validate share quantity is positive and reasonable.

    Args:
        shares: Number of shares
        ticker: Optional ticker for logging

    Returns:
        Tuple of (is_valid, reason)
    """
    ticker_str = f" for {ticker}" if ticker else ""

    if shares is None:
        return False, f"Share quantity is None{ticker_str}"

    if not isinstance(shares, (int, float)):
        return False, f"Share quantity must be a number{ticker_str}, got {type(shares)}"

    shares = int(shares)

    if shares <= 0:
        return False, f"Share quantity must be positive{ticker_str}, got {shares}"

    if shares > 100000:  # Sanity check - suspiciously large order
        return False, f"Share quantity {shares} seems too large{ticker_str} (max 100,000)"

    return True, "Share quantity OK"


def validate_position_size(
    ticker: str,
    shares: int,
    price: float,
    portfolio_value: float,
    existing_positions: Dict
) -> Tuple[bool, str]:
    """
    Validate position size meets 25% max rule

    Args:
        ticker: Stock ticker to buy
        shares: Number of shares to buy
        price: Current price per share
        portfolio_value: Total portfolio value
        existing_positions: Dict of current positions

    Returns:
        Tuple of (is_valid, reason)
    """
    # First validate share quantity
    qty_valid, qty_reason = validate_share_quantity(shares, ticker)
    if not qty_valid:
        return False, qty_reason

    new_position_value = shares * price

    # Check if this purchase would exceed 25% of portfolio
    max_position_value = portfolio_value * MAX_SINGLE_POSITION_PCT

    if ticker in existing_positions:
        # Adding to existing position
        existing_value = existing_positions[ticker].get('value', 0)
        total_value = existing_value + new_position_value
        if total_value > max_position_value:
            return False, f"Position ${total_value:,.2f} would exceed 25% limit (${max_position_value:,.2f})"
    else:
        if new_position_value > max_position_value:
            return False, f"Position ${new_position_value:,.2f} exceeds 25% limit (${max_position_value:,.2f})"

    return True, "Position size OK"


def validate_trade_count(trades_used: int, is_new_buy: bool = True) -> Tuple[bool, str]:
    """
    Validate we haven't exceeded trade limits

    Args:
        trades_used: Number of trades already used
        is_new_buy: True if this is a new buy (uses soft cap), False for sells

    Returns:
        Tuple of (is_valid, reason)
    """
    # Hard limit - no trades allowed
    if trades_used >= MAX_TRADES_TOTAL:
        return False, f"HARD STOP: {trades_used}/{MAX_TRADES_TOTAL} trades used - NO MORE TRADES"

    # Soft limit for buys - emergency exits only
    if is_new_buy and trades_used >= HARD_STOP_TRADES:
        return False, f"SOFT STOP: {trades_used}/{HARD_STOP_TRADES} trades - only emergency exits allowed"

    remaining = MAX_TRADES_TOTAL - trades_used
    return True, f"Trades OK ({trades_used} used, {remaining} remaining)"


def validate_holding_period(position: Dict, now_utc: datetime = None) -> Tuple[bool, str]:
    """
    Validate 24-hour + buffer holding period has passed (timestamp-based).

    CRITICAL: This is the competition's actual rule - 24 hours minimum hold.
    Uses actual timestamps, not trading days.

    Args:
        position: Position dict with 'last_buy_timestamp' or 'entry_timestamp'
        now_utc: Current UTC time (defaults to now, for testing)

    Returns:
        Tuple of (can_sell, reason)
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Get the most recent buy timestamp (critical for 24h enforcement)
    ts_str = position.get('last_buy_timestamp') or position.get('entry_timestamp')

    if not ts_str:
        # Fail-closed for compliance: if no timestamp, don't allow sell
        return False, "No buy timestamp recorded (fail-closed for compliance)"

    buy_ts = _parse_timestamp_utc(ts_str)
    if not buy_ts:
        return False, f"Unparseable buy timestamp: {ts_str}"

    # Calculate elapsed time
    elapsed = (now_utc - buy_ts).total_seconds()
    required = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS

    if elapsed < required:
        remaining_seconds = required - elapsed
        remaining_minutes = remaining_seconds / 60
        remaining_hours = remaining_seconds / 3600

        if remaining_hours >= 1:
            return False, f"24h hold not met: {remaining_hours:.1f} hours remaining"
        else:
            return False, f"24h hold not met: {remaining_minutes:.1f} minutes remaining"

    hours_held = elapsed / 3600
    return True, f"24h hold met: {hours_held:.2f} hours since last buy"


def validate_holding_period_legacy(position: Dict, current_date=None) -> Tuple[bool, str]:
    """
    DEPRECATED: Legacy T+2 trading days validation.

    This function is kept for backwards compatibility but should NOT be used
    for compliance. Use validate_holding_period() instead.

    Args:
        position: Position dict with 'entry_date'
        current_date: Date to check against (defaults to today)

    Returns:
        Tuple of (can_sell, reason)
    """
    if current_date is None:
        current_date = datetime.now().date()

    entry_date_str = position.get('entry_date')
    if not entry_date_str:
        return False, "No entry date recorded"

    if isinstance(entry_date_str, str):
        entry_date = datetime.fromisoformat(entry_date_str).date()
    else:
        entry_date = entry_date_str

    # Count trading days since entry
    trading_days = get_trading_days_between(entry_date, current_date)

    if trading_days < 2:  # T+2
        return False, f"Holding period not met: {trading_days}/2 trading days"

    return True, f"Holding period met ({trading_days} trading days)"


def validate_min_holdings(
    current_holdings: Dict,
    ticker_to_sell: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Validate minimum holdings requirement (must hold at least 4 securities)

    Args:
        current_holdings: Dict of current positions
        ticker_to_sell: Ticker we want to sell (if any)

    Returns:
        Tuple of (is_valid, reason)
    """
    current_count = len(current_holdings)

    if ticker_to_sell:
        # Check if selling would drop below minimum
        if current_count <= MIN_HOLDINGS:
            return False, f"Cannot sell: would drop below {MIN_HOLDINGS} minimum holdings"
    else:
        if current_count < MIN_HOLDINGS:
            return False, f"Warning: currently {current_count} holdings, need {MIN_HOLDINGS} minimum"

    return True, f"Holdings OK ({current_count} securities)"


def validate_bucket_limits(
    ticker: str,
    current_positions: Dict
) -> Tuple[bool, str]:
    """
    Validate bucket diversification limits (structural 1/N: exactly 1 per bucket)

    UPDATED (Perfection Patch):
    - MAX_PER_BUCKET = 1 (structural 1/N diversification)
    - 8 buckets × 1 position each = 8 satellites
    - This ensures diversification across independent themes

    Args:
        ticker: Ticker to add
        current_positions: Dict of current positions

    Returns:
        Tuple of (is_valid, reason)
    """
    # Core positions don't have bucket limits
    if ticker in CORE_POSITIONS:
        return True, "Core position - no bucket limit"

    bucket = get_bucket_for_ticker(ticker)
    if not bucket:
        return True, "Ticker not in any bucket"

    # Count existing positions in this bucket
    bucket_count = 0
    for pos_ticker in current_positions.keys():
        if pos_ticker in CORE_POSITIONS:
            continue
        if get_bucket_for_ticker(pos_ticker) == bucket:
            bucket_count += 1

    if bucket_count >= MAX_PER_BUCKET:
        return False, f"Bucket {bucket} filled (1/N: exactly 1 per bucket)"

    return True, f"Bucket {bucket} empty (1/N structural diversification)"


def validate_event_freeze(current_datetime=None) -> Tuple[bool, str]:
    """
    Check if we're in an event freeze period (no new buys)

    Args:
        current_datetime: Datetime to check (defaults to now)

    Returns:
        Tuple of (can_trade, reason)
    """
    if current_datetime is None:
        current_datetime = datetime.now()

    current_date = current_datetime.date()

    if current_date in EVENT_FREEZE_DATES:
        return False, f"EVENT FREEZE: {current_date} is a freeze date (FOMC)"

    return True, "No event freeze"


def validate_weekly_cap(
    week_replacements: int,
    vix_level: float
) -> Tuple[bool, str]:
    """
    Validate weekly replacement cap based on VIX regime

    Args:
        week_replacements: Replacements made this week
        vix_level: Current VIX level

    Returns:
        Tuple of (can_replace, reason)
    """
    regime = get_vix_regime(vix_level)
    weekly_cap = REGIME_PARAMS[regime]['weekly_replacement_cap']

    if week_replacements >= weekly_cap:
        return False, f"Weekly cap reached: {week_replacements}/{weekly_cap} ({regime} regime)"

    return True, f"Weekly cap OK ({week_replacements}/{weekly_cap})"


def get_vix_regime(vix_level: float) -> str:
    """Determine VIX regime from level"""
    if vix_level < 20:
        return 'NORMAL'
    elif vix_level <= 30:
        return 'CAUTION'
    else:
        return 'SHOCK'


def get_market_regime(voo_price: float, voo_sma200: float) -> str:
    """Determine market regime from VOO price vs SMA200"""
    if voo_sma200 is None:
        return 'RISK_ON'  # Default to risk-on if no SMA data

    if voo_price > voo_sma200:
        return 'RISK_ON'
    else:
        return 'RISK_OFF'


def can_buy(
    ticker: str,
    price: float,
    shares: int,
    portfolio_value: float,
    trades_used: int,
    week_replacements: int,
    vix_level: float,
    current_positions: Dict,
    market_data: Dict,
    bypass_weekly_cap: bool = False
) -> Tuple[bool, Dict[str, Tuple[bool, str]]]:
    """
    Complete buy validation - all checks must pass

    Args:
        ticker: Ticker to buy
        price: Current price
        shares: Shares to buy
        portfolio_value: Total portfolio value
        trades_used: Trades already used
        week_replacements: Replacements this week
        vix_level: Current VIX
        current_positions: Current holdings
        market_data: Full market data dict
        bypass_weekly_cap: If True, skip weekly cap check (for Day-1 continuation/emergency)

    Returns:
        Tuple of (all_passed, detailed_results)
    """
    checks = {}

    # Basic validations
    checks['prohibited'] = (not is_prohibited(ticker),
                            "Not prohibited" if not is_prohibited(ticker) else "PROHIBITED")
    checks['price'] = validate_price(ticker, price, is_buy=True)
    checks['position_size'] = validate_position_size(ticker, shares, price, portfolio_value, current_positions)
    checks['trade_count'] = validate_trade_count(trades_used, is_new_buy=True)
    checks['bucket_limits'] = validate_bucket_limits(ticker, current_positions)
    checks['event_freeze'] = validate_event_freeze()

    # Weekly cap - bypass for Day-1 continuation and emergency buys
    if bypass_weekly_cap:
        checks['weekly_cap'] = (True, "Weekly cap bypassed (Day-1 continuation/emergency)")
    else:
        checks['weekly_cap'] = validate_weekly_cap(week_replacements, vix_level)

    # Trend validation
    ticker_data = market_data.get(ticker, {})
    if ticker_data:
        is_uptrend = validate_uptrend(ticker_data)
        checks['uptrend'] = is_uptrend

    all_passed = all(check[0] for check in checks.values())

    return all_passed, checks


def can_sell(
    ticker: str,
    position: Dict,
    current_holdings: Dict,
    trades_used: int
) -> Tuple[bool, Dict[str, Tuple[bool, str]]]:
    """
    Complete sell validation

    Args:
        ticker: Ticker to sell
        position: Position data
        current_holdings: All current holdings
        trades_used: Trades already used

    Returns:
        Tuple of (all_passed, detailed_results)
    """
    checks = {}

    checks['holding_period'] = validate_holding_period(position)
    checks['min_holdings'] = validate_min_holdings(current_holdings, ticker)
    checks['trade_count'] = validate_trade_count(trades_used, is_new_buy=False)

    all_passed = all(check[0] for check in checks.values())

    return all_passed, checks


def validate_uptrend(ticker_data: Dict) -> Tuple[bool, str]:
    """
    Validate uptrend: Close > SMA50 AND SMA50 > SMA200

    Args:
        ticker_data: Dict with 'price', 'sma50', 'sma200' or 'sma100'

    Returns:
        Tuple of (is_uptrend, reason)
    """
    price = ticker_data.get('price')
    sma50 = ticker_data.get('sma50')
    sma200 = ticker_data.get('sma200') or ticker_data.get('sma100')

    if not all([price, sma50, sma200]):
        return False, "Missing SMA data for trend validation"

    if price > sma50 and sma50 > sma200:
        return True, f"Uptrend confirmed: ${price:.2f} > SMA50 (${sma50:.2f}) > SMA200 (${sma200:.2f})"
    else:
        return False, f"Not uptrend: ${price:.2f}, SMA50=${sma50:.2f}, SMA200=${sma200:.2f}"


def validate_double7_low(ticker_data: Dict) -> Tuple[bool, str]:
    """
    Validate Double-7 Low: Today's close is lowest of past 7 days

    Args:
        ticker_data: Dict with 'price' and 'closes_7d'

    Returns:
        Tuple of (is_double7_low, reason)
    """
    price = ticker_data.get('price')
    closes_7d = ticker_data.get('closes_7d', [])

    if not price or len(closes_7d) < 7:
        return False, "Insufficient data for Double-7 check"

    if price <= min(closes_7d):
        return True, f"Double-7 Low: ${price:.2f} is 7-day low"
    else:
        return False, f"Not Double-7 Low: ${price:.2f} > min({min(closes_7d):.2f})"


def validate_double7_high(ticker_data: Dict) -> Tuple[bool, str]:
    """
    Validate Double-7 High: Today's close is highest of past 7 days

    Args:
        ticker_data: Dict with 'price' and 'closes_7d'

    Returns:
        Tuple of (is_double7_high, reason)
    """
    price = ticker_data.get('price')
    closes_7d = ticker_data.get('closes_7d', [])

    if not price or len(closes_7d) < 7:
        return False, "Insufficient data for Double-7 check"

    if price >= max(closes_7d):
        return True, f"Double-7 High: ${price:.2f} is 7-day high"
    else:
        return False, f"Not Double-7 High: ${price:.2f} < max({max(closes_7d):.2f})"
