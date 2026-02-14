"""
SPRINT3 Trading Strategy for StockTrak Bot

Implements a high-intensity 3-day trading sprint for end-of-competition catch-up.
Uses a Core/Satellite mandate with momentum-based forecasting.

Key Features:
- 60% Core (VOO 25%, VTI 20%, VEA 15%) - stable, max 25% per position
- 40% Satellites (16 slots x 2.5% each) - rotated daily for momentum capture
- Trend-following scoring with volatility penalty
- Strict 24-hour + buffer holding period enforcement (timestamp-based)
- Market hours morning trading window (9:40-10:05 AM ET)

Competition Constraints Enforced:
- Max 80 trades total (70 remaining, 65 sprint cap, 5 buffer)
- Max 25% per position (CRITICAL: enforced at purchase time)
- Min 4 holdings at all times
- BUY price >= $5 (use $6 safety buffer)
- 24-hour minimum hold (timestamp-based, not date-based)
- No leveraged/inverse ETFs
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import pytz

from config import CORE_POSITIONS, get_bucket_for_ticker
from state_manager import StateManager
from market_data import MarketDataCollector
from validators import is_prohibited
from utils import calculate_limit_price, calculate_shares_for_allocation, get_current_time_et

logger = logging.getLogger('stocktrak_bot.sprint3')

# =============================================================================
# SPRINT3 CONFIGURATION
# =============================================================================

# Core allocation (60% total)
SPRINT3_CORE = {
    'VOO': 0.25,  # 25%
    'VTI': 0.20,  # 20%
    'VEA': 0.15,  # 15%
}

# Satellite slots (40% total = 16 x 2.5%)
SPRINT3_SATELLITE_COUNT = 16
SPRINT3_SATELLITE_SIZE = 0.025  # 2.5% per slot

# Trade budget
SPRINT3_TRADE_CAP = 65  # Leave 5 trade buffer
SPRINT3_BUFFER_TRADES = 5

# Price safety
SPRINT3_MIN_PRICE = 6.00  # Only buy if price >= $6
SPRINT3_LIMIT_FLOOR = 5.01  # Limit price cannot be below $5.01

# Holding period (24h + buffer in seconds)
SPRINT3_HOLD_BUFFER_SECONDS = 120  # 2 minute buffer on top of 24h

# Market hours (Eastern Time) - Morning trading window
# UPDATED: Using morning window for stable execution (after initial volatility)
SPRINT3_EXECUTION_WINDOW_START = "09:40"  # 9:40 AM ET
SPRINT3_EXECUTION_WINDOW_END = "10:05"    # 10:05 AM ET

# =============================================================================
# SATELLITE UNIVERSE (curated, liquid, non-leveraged)
# =============================================================================

SPRINT3_SATELLITE_ETFS = [
    # Semis / AI
    'SMH', 'SOXX', 'XLK',
    # Defense
    'PPA', 'ITA', 'XAR',
    # Energy
    'XLE', 'XOP',
    # Metals / Copper
    'COPX', 'XME', 'PICK',
    # Uranium / Nuclear
    'URA', 'URNM', 'NLR',
    # Biotech
    'XBI', 'IDNA',
    # Space
    'UFO', 'ROKT',
]

SPRINT3_SATELLITE_STOCKS = [
    # Semis / AI
    'NVDA', 'AMD', 'AVGO', 'ASML', 'AMAT', 'LRCX', 'KLAC',
    # Defense
    'LMT', 'NOC', 'RTX', 'GD', 'KTOS', 'AVAV',
    # Energy
    'XOM', 'CVX',
    # Metals
    'FCX', 'SCCO',
    # Uranium
    'CCJ',
    # Biotech
    'CRSP', 'NTLA', 'BEAM',
    # Space (price must be >= $6)
    'RKLB', 'ASTS', 'LUNR', 'RDW',
]

# Combined universe
SPRINT3_SATELLITE_UNIVERSE = SPRINT3_SATELLITE_ETFS + SPRINT3_SATELLITE_STOCKS


# =============================================================================
# SCORING / FORECASTING
# =============================================================================

@dataclass
class Sprint3Candidate:
    """Scored satellite candidate for sprint3."""
    ticker: str
    score: float
    r1: float      # 1-day return
    r3: float      # 3-day return
    r10: float     # 10-day return
    rr3: float     # 3-day return relative to VOO
    rr10: float    # 10-day return relative to VOO
    vol10: float   # 10-day volatility
    price: float
    sma20: float
    sma50: float
    trend_ok: bool
    is_eligible: bool
    disqualify_reason: Optional[str] = None

    @property
    def is_etf(self) -> bool:
        return self.ticker in SPRINT3_SATELLITE_ETFS


def calculate_sprint3_score(
    ticker_data: Dict,
    voo_data: Dict
) -> Optional[Sprint3Candidate]:
    """
    Calculate ForecastScore for a satellite candidate.

    Score = 0.55*rr3 + 0.35*rr10 - 0.25*vol10

    Where:
    - rr3 = ticker's 3-day return - VOO's 3-day return (relative momentum)
    - rr10 = ticker's 10-day return - VOO's 10-day return
    - vol10 = standard deviation of daily returns over 10 days

    This score rewards short-term relative momentum while penalizing volatility.

    UPDATED: Now uses actual r1, r3, r10, vol10, SMA20 values from market_data.py
    instead of approximations. This improves ranking accuracy significantly.
    """
    if not ticker_data or not voo_data:
        return None

    ticker = ticker_data.get('ticker', 'UNKNOWN')
    price = ticker_data.get('price', 0)

    # Get ACTUAL returns from market data (not approximations!)
    # MarketDataCollector now provides actual r1, r3, r10 values
    r1 = ticker_data.get('return_1d', 0) or 0
    r3 = ticker_data.get('return_3d', 0) or 0
    r10 = ticker_data.get('return_10d', 0) or 0
    r21 = ticker_data.get('return_21d', 0) or 0

    # Fallback: If actual values not available, use approximations
    # This maintains backwards compatibility with older cached data
    if r3 == 0 and r21 != 0:
        r3 = r21 * (3/21)  # Fallback approximation
    if r10 == 0 and r21 != 0:
        r10 = r21 * (10/21)  # Fallback approximation
    if r1 == 0 and r21 != 0:
        r1 = r21 * (1/21)  # Fallback approximation

    # VOO ACTUAL returns for relative calculation
    voo_r3 = voo_data.get('return_3d', 0) or 0
    voo_r10 = voo_data.get('return_10d', 0) or 0

    # Fallback for VOO if not available
    if voo_r3 == 0:
        voo_r21 = voo_data.get('return_21d', 0) or 0
        voo_r3 = voo_r21 * (3/21) if voo_r21 else 0
    if voo_r10 == 0:
        voo_r21 = voo_data.get('return_21d', 0) or 0
        voo_r10 = voo_r21 * (10/21) if voo_r21 else 0

    # Relative returns (actual momentum vs benchmark)
    rr3 = r3 - voo_r3
    rr10 = r10 - voo_r10

    # ACTUAL 10-day volatility (not 21-day proxy!)
    vol10 = ticker_data.get('vol10', None)
    if vol10 is None:
        # Fallback to 21-day if 10-day not available
        vol10 = ticker_data.get('volatility_21d', 0.05) or 0.05

    # ForecastScore
    score = 0.55 * rr3 + 0.35 * rr10 - 0.25 * vol10

    # SMAs for trend filter - use ACTUAL SMA20 now!
    sma20 = ticker_data.get('sma20', None)  # Actual SMA20 from market data
    sma50 = ticker_data.get('sma50', 0) or 0
    sma100 = ticker_data.get('sma100', 0) or 0
    sma200 = ticker_data.get('sma200', sma100) or sma100

    # Fallback SMA20 estimation only if actual not available
    if sma20 is None and price and sma50:
        sma20 = (price + sma50) / 2  # Rough estimate as fallback
    elif sma20 is None:
        sma20 = price

    # Trend filter: Close > SMA20 AND SMA20 > SMA50
    trend_ok = (price > sma20) and (sma20 > sma50) if (sma20 and sma50) else True

    # Eligibility
    is_eligible = True
    disqualify_reason = None

    # Check price
    if price < SPRINT3_MIN_PRICE:
        is_eligible = False
        disqualify_reason = f"Price ${price:.2f} < ${SPRINT3_MIN_PRICE}"

    # Check prohibited
    if is_prohibited(ticker):
        is_eligible = False
        disqualify_reason = "Prohibited security"

    # Check trend
    if not trend_ok:
        is_eligible = False
        disqualify_reason = "Trend filter failed"

    return Sprint3Candidate(
        ticker=ticker,
        score=score,
        r1=r1,
        r3=r3,
        r10=r10,
        rr3=rr3,
        rr10=rr10,
        vol10=vol10,
        price=price,
        sma20=sma20,
        sma50=sma50,
        trend_ok=trend_ok,
        is_eligible=is_eligible,
        disqualify_reason=disqualify_reason
    )


def score_all_sprint3_candidates(market_data: Dict) -> List[Sprint3Candidate]:
    """
    Score all satellites in the sprint3 universe.

    Returns candidates sorted by score (highest first).
    """
    voo_data = market_data.get('VOO')
    if not voo_data:
        logger.error("Cannot score candidates: VOO data missing")
        return []

    candidates = []

    for ticker in SPRINT3_SATELLITE_UNIVERSE:
        ticker_data = market_data.get(ticker)
        if not ticker_data:
            logger.debug(f"No data for {ticker}")
            continue

        # Add ticker to data dict
        ticker_data['ticker'] = ticker

        candidate = calculate_sprint3_score(ticker_data, voo_data)
        if candidate:
            candidates.append(candidate)

    # Sort by score descending
    candidates.sort(key=lambda x: x.score, reverse=True)

    return candidates


def get_top_sprint3_candidates(
    market_data: Dict,
    n: int = 16,
    exclude_tickers: List[str] = None,
    require_eligible: bool = True,
    vix_level: float = None
) -> List[Sprint3Candidate]:
    """
    Get top N satellite candidates for sprint3.

    Args:
        market_data: Market data dict
        n: Number of candidates to return
        exclude_tickers: Tickers to exclude (e.g., just sold)
        require_eligible: Only return eligible candidates
        vix_level: Current VIX for risk regime adjustment

    Returns:
        Top N candidates by score
    """
    if exclude_tickers is None:
        exclude_tickers = []

    all_candidates = score_all_sprint3_candidates(market_data)

    # Filter
    filtered = []
    for c in all_candidates:
        if c.ticker in exclude_tickers:
            continue
        if require_eligible and not c.is_eligible:
            continue
        filtered.append(c)

    # Risk regime adjustment: if VOO < SMA50, prefer ETFs over single stocks
    if vix_level and vix_level > 25:
        # In high VIX, limit single-name stocks to 6
        etfs = [c for c in filtered if c.is_etf]
        stocks = [c for c in filtered if not c.is_etf]
        result = etfs[:n] + stocks[:min(6, n - len(etfs[:n]))]
        result.sort(key=lambda x: x.score, reverse=True)
        return result[:n]

    return filtered[:n]


# =============================================================================
# HOLDING PERIOD VALIDATION
# =============================================================================

def can_sell_sprint3(position: Dict, current_time: datetime = None) -> Tuple[bool, str]:
    """
    Check if a position can be sold (24h + buffer elapsed).

    Args:
        position: Position dict with 'buy_fill_time' or 'entry_date'
        current_time: Current time (for testing)

    Returns:
        Tuple of (can_sell, reason)
    """
    if current_time is None:
        current_time = datetime.now(pytz.timezone('US/Eastern'))

    # Get buy time
    buy_time_str = position.get('buy_fill_time') or position.get('entry_date')
    if not buy_time_str:
        # No buy time recorded - assume old enough
        return True, "No buy time recorded - assuming eligible"

    try:
        # Parse buy time
        if 'T' in buy_time_str:
            buy_time = datetime.fromisoformat(buy_time_str.replace('Z', '+00:00'))
        else:
            # Date only - assume market close (4:00 PM ET)
            buy_date = datetime.fromisoformat(buy_time_str)
            et = pytz.timezone('US/Eastern')
            buy_time = et.localize(buy_date.replace(hour=16, minute=0))

        # Make current_time timezone aware if needed
        if current_time.tzinfo is None:
            et = pytz.timezone('US/Eastern')
            current_time = et.localize(current_time)

        # Minimum hold: 24h + buffer
        min_hold_delta = timedelta(hours=24, seconds=SPRINT3_HOLD_BUFFER_SECONDS)
        earliest_sell = buy_time + min_hold_delta

        if current_time >= earliest_sell:
            return True, f"Held {(current_time - buy_time).total_seconds() / 3600:.1f}h"
        else:
            remaining = (earliest_sell - current_time).total_seconds() / 60
            return False, f"Need {remaining:.0f} more minutes"

    except Exception as e:
        logger.warning(f"Error parsing buy time '{buy_time_str}': {e}")
        return True, "Parse error - assuming eligible"


def is_market_open() -> Tuple[bool, str]:
    """
    Check if market is currently open.

    Returns:
        Tuple of (is_open, reason)
    """
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)

    # Check day of week (Monday=0, Friday=4)
    if now.weekday() > 4:
        return False, f"Weekend (day {now.weekday()})"

    # Check time
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    if now < market_open:
        return False, f"Pre-market ({now.strftime('%H:%M')} ET)"

    if now >= market_close:
        return False, f"After hours ({now.strftime('%H:%M')} ET)"

    return True, f"Market open ({now.strftime('%H:%M')} ET)"


def is_in_execution_window() -> Tuple[bool, str]:
    """
    Check if we're in the sprint3 execution window (9:40-10:05 AM ET).

    Morning window provides stable execution after initial market volatility.
    The 24-hour hold is enforced via timestamps, not trading times.
    """
    et = pytz.timezone('US/Eastern')
    now = datetime.now(et)

    # Parse window times
    window_start = datetime.strptime(SPRINT3_EXECUTION_WINDOW_START, "%H:%M")
    window_end = datetime.strptime(SPRINT3_EXECUTION_WINDOW_END, "%H:%M")

    current_time = now.time()
    start_time = window_start.time()
    end_time = window_end.time()

    if start_time <= current_time < end_time:
        return True, f"In execution window ({now.strftime('%H:%M')} ET)"

    return False, f"Outside window ({now.strftime('%H:%M')} ET, window is {SPRINT3_EXECUTION_WINDOW_START}-{SPRINT3_EXECUTION_WINDOW_END})"


# =============================================================================
# SPRINT3 EXECUTION
# =============================================================================

class Sprint3Executor:
    """
    Executes the 3-day sprint strategy.

    Day 1: Build core + 16 satellites
    Day 2: Rotate all 16 satellites
    Day 3: Rotate remaining trades up to cap
    """

    def __init__(self, bot, state: StateManager, dry_run: bool = False):
        self.bot = bot
        self.state = state
        self.dry_run = dry_run
        self.collector = MarketDataCollector()

    def get_sprint_state(self) -> Dict:
        """Get current sprint state from state manager."""
        sprint_state = self.state.state.get('sprint3', {})
        return {
            'mode': sprint_state.get('mode', 'SPRINT3'),
            'sprint_day': sprint_state.get('sprint_day', 0),
            'trades_used_sprint': sprint_state.get('trades_used_sprint', 0),
            'last_run_time': sprint_state.get('last_run_time'),
            'last_run_day': sprint_state.get('last_run_day'),
            'satellites_held': sprint_state.get('satellites_held', []),
        }

    def update_sprint_state(self, **kwargs):
        """Update sprint state in state manager."""
        if 'sprint3' not in self.state.state:
            self.state.state['sprint3'] = {}

        self.state.state['sprint3'].update(kwargs)
        self.state.save()

    def get_trades_budget(self) -> Dict:
        """Calculate trade budget for sprint."""
        trades_used_total = self.state.get_trades_used()
        trades_remaining = self.state.get_trades_remaining()

        sprint_cap = min(SPRINT3_TRADE_CAP, trades_remaining - SPRINT3_BUFFER_TRADES)
        sprint_used = self.get_sprint_state()['trades_used_sprint']
        sprint_remaining = max(0, sprint_cap - sprint_used)

        return {
            'total_used': trades_used_total,
            'total_remaining': trades_remaining,
            'sprint_cap': sprint_cap,
            'sprint_used': sprint_used,
            'sprint_remaining': sprint_remaining,
        }

    def print_status(self):
        """Print current sprint status."""
        sprint = self.get_sprint_state()
        budget = self.get_trades_budget()

        print("\n" + "=" * 70)
        print("SPRINT3 STATUS")
        print("=" * 70)
        print(f"Mode:              {sprint['mode']}")
        print(f"Sprint Day:        {sprint['sprint_day']}/3")
        print(f"Last Run:          {sprint['last_run_time'] or 'Never'}")
        print("-" * 70)
        print(f"Total Trades Used: {budget['total_used']}/80")
        print(f"Sprint Cap:        {budget['sprint_cap']}")
        print(f"Sprint Used:       {budget['sprint_used']}")
        print(f"Sprint Remaining:  {budget['sprint_remaining']}")
        print("-" * 70)

        # Market status
        market_open, market_reason = is_market_open()
        in_window, window_reason = is_in_execution_window()
        print(f"Market:            {market_reason}")
        print(f"Execution Window:  {window_reason}")
        print("-" * 70)

        # Current positions
        positions = self.state.get_positions()
        core_count = sum(1 for t in positions if t in SPRINT3_CORE)
        satellite_count = len(positions) - core_count
        print(f"Positions:         {len(positions)} total ({core_count} core, {satellite_count} satellites)")

        if sprint['satellites_held']:
            print(f"Satellites Held:   {', '.join(sprint['satellites_held'][:8])}")
            if len(sprint['satellites_held']) > 8:
                print(f"                   {', '.join(sprint['satellites_held'][8:])}")

        print("=" * 70)

    def execute_sprint_day(self, force_day: int = None) -> Dict:
        """
        Execute sprint3 for the appropriate day.

        Args:
            force_day: Force a specific day (for testing)

        Returns:
            Execution result dict
        """
        # Check market is open
        market_open, market_reason = is_market_open()
        if not market_open and not self.dry_run:
            return {
                'success': False,
                'error': f"Market closed: {market_reason}",
                'trades_executed': 0
            }

        # Check execution window
        in_window, window_reason = is_in_execution_window()
        if not in_window and not self.dry_run:
            logger.warning(f"Outside execution window: {window_reason}")
            # Continue with warning - allow for testing

        # Determine sprint day
        sprint_state = self.get_sprint_state()
        current_day = sprint_state['sprint_day']

        if force_day:
            next_day = force_day
        elif current_day == 0:
            next_day = 1
        elif current_day < 3:
            # Check if we already ran today
            last_run = sprint_state.get('last_run_day')
            today = datetime.now().date().isoformat()
            if last_run == today:
                return {
                    'success': False,
                    'error': f"Already ran sprint day {current_day} today",
                    'trades_executed': 0
                }
            next_day = current_day + 1
        else:
            return {
                'success': False,
                'error': "Sprint complete (day 3 already executed)",
                'trades_executed': 0
            }

        logger.info(f"Executing SPRINT3 Day {next_day}")

        # Execute appropriate day
        if next_day == 1:
            result = self._execute_day1()
        elif next_day == 2:
            result = self._execute_day2()
        else:
            result = self._execute_day3()

        # Update sprint state
        if result['success']:
            self.update_sprint_state(
                sprint_day=next_day,
                trades_used_sprint=self.get_sprint_state()['trades_used_sprint'] + result['trades_executed'],
                last_run_time=datetime.now().isoformat(),
                last_run_day=datetime.now().date().isoformat()
            )

        return result

    def _execute_day1(self) -> Dict:
        """
        Day 1: Build core positions + 16 satellites.

        Expected trades: ~3 core + 16 satellites = ~19 trades
        """
        logger.info("=" * 70)
        logger.info("SPRINT3 DAY 1: Building portfolio")
        logger.info("=" * 70)

        trades_executed = 0
        errors = []

        # Get market data
        market_data = self.collector.get_all_data(
            list(SPRINT3_CORE.keys()) + SPRINT3_SATELLITE_UNIVERSE
        )

        if not market_data.get('VOO'):
            return {'success': False, 'error': 'Could not fetch market data', 'trades_executed': 0}

        # Get portfolio value
        try:
            portfolio_value, cash, buying_power = self.bot.get_capital_from_trade_kpis("VOO")
        except Exception as e:
            return {'success': False, 'error': f'Could not get capital: {e}', 'trades_executed': 0}

        logger.info(f"Portfolio: ${portfolio_value:,.2f}, Cash: ${cash:,.2f}, Buying Power: ${buying_power:,.2f}")

        positions = self.state.get_positions()

        # Build core positions if needed
        for ticker, target_pct in SPRINT3_CORE.items():
            if ticker in positions:
                logger.info(f"CORE {ticker}: Already held, skipping")
                continue

            ticker_data = market_data.get(ticker, {})
            price = ticker_data.get('price', 0)

            if price < 1:
                logger.warning(f"CORE {ticker}: No price data")
                continue

            shares = calculate_shares_for_allocation(portfolio_value, target_pct, price)

            if shares < 1:
                logger.warning(f"CORE {ticker}: Position too small")
                continue

            result = self._execute_buy(ticker, shares, f"SPRINT3_D1_CORE_{target_pct*100:.0f}PCT", price)
            if result['success']:
                trades_executed += 1
                self.state.add_position(ticker, shares, price, bucket='CORE')
            else:
                errors.append(f"{ticker}: {result.get('error')}")

        # Get top 16 satellites
        candidates = get_top_sprint3_candidates(
            market_data,
            n=SPRINT3_SATELLITE_COUNT,
            exclude_tickers=list(positions.keys()),
            require_eligible=True
        )

        logger.info(f"Found {len(candidates)} eligible satellite candidates")

        # Buy satellites
        satellites_bought = []
        for candidate in candidates:
            if trades_executed >= SPRINT3_TRADE_CAP:
                logger.warning("Hit sprint trade cap")
                break

            shares = calculate_shares_for_allocation(
                portfolio_value, SPRINT3_SATELLITE_SIZE, candidate.price
            )

            if shares < 1:
                logger.debug(f"SATELLITE {candidate.ticker}: Position too small")
                continue

            result = self._execute_buy(
                candidate.ticker, shares,
                f"SPRINT3_D1_SAT_SCORE_{candidate.score:.4f}",
                candidate.price
            )

            if result['success']:
                trades_executed += 1
                self.state.add_position(
                    candidate.ticker, shares, candidate.price,
                    bucket=get_bucket_for_ticker(candidate.ticker) or 'SATELLITE'
                )
                satellites_bought.append(candidate.ticker)
            else:
                errors.append(f"{candidate.ticker}: {result.get('error')}")

            time.sleep(3)  # Delay between trades

        # Update sprint state with satellites
        self.update_sprint_state(satellites_held=satellites_bought)

        logger.info(f"Day 1 complete: {trades_executed} trades, {len(errors)} errors")

        return {
            'success': True,
            'trades_executed': trades_executed,
            'satellites_bought': satellites_bought,
            'errors': errors
        }

    def _execute_day2(self) -> Dict:
        """
        Day 2: Rotate ALL 16 satellites.

        Expected trades: 16 sells + 16 buys = 32 trades
        """
        logger.info("=" * 70)
        logger.info("SPRINT3 DAY 2: Full satellite rotation")
        logger.info("=" * 70)

        trades_executed = 0
        errors = []
        sells_executed = []
        buys_executed = []

        # Get market data
        market_data = self.collector.get_all_data(
            list(SPRINT3_CORE.keys()) + SPRINT3_SATELLITE_UNIVERSE
        )

        if not market_data.get('VOO'):
            return {'success': False, 'error': 'Could not fetch market data', 'trades_executed': 0}

        # Get portfolio value
        try:
            portfolio_value, cash, buying_power = self.bot.get_capital_from_trade_kpis("VOO")
        except Exception as e:
            return {'success': False, 'error': f'Could not get capital: {e}', 'trades_executed': 0}

        positions = self.state.get_positions()
        sprint_state = self.get_sprint_state()
        satellites_held = sprint_state.get('satellites_held', [])

        # Sell all satellites (if 24h elapsed)
        for ticker in satellites_held:
            if ticker not in positions:
                logger.debug(f"SELL {ticker}: Not in positions")
                continue

            position = positions[ticker]
            can_sell, reason = can_sell_sprint3(position)

            if not can_sell:
                logger.warning(f"SELL {ticker}: Cannot sell - {reason}")
                errors.append(f"{ticker}: {reason}")
                continue

            shares = position.get('shares', 0)
            if shares < 1:
                continue

            result = self._execute_sell(ticker, shares, f"SPRINT3_D2_ROTATE")
            if result['success']:
                trades_executed += 1
                self.state.remove_position(ticker)
                sells_executed.append(ticker)
            else:
                errors.append(f"SELL {ticker}: {result.get('error')}")

            time.sleep(3)

        logger.info(f"Sells complete: {len(sells_executed)}")

        # Get new top candidates (excluding just-sold)
        candidates = get_top_sprint3_candidates(
            market_data,
            n=SPRINT3_SATELLITE_COUNT,
            exclude_tickers=sells_executed + list(SPRINT3_CORE.keys()),
            require_eligible=True
        )

        # Buy new satellites
        for candidate in candidates:
            budget = self.get_trades_budget()
            if budget['sprint_remaining'] <= 0:
                logger.warning("Sprint budget exhausted")
                break

            shares = calculate_shares_for_allocation(
                portfolio_value, SPRINT3_SATELLITE_SIZE, candidate.price
            )

            if shares < 1:
                continue

            result = self._execute_buy(
                candidate.ticker, shares,
                f"SPRINT3_D2_SAT_SCORE_{candidate.score:.4f}",
                candidate.price
            )

            if result['success']:
                trades_executed += 1
                self.state.add_position(
                    candidate.ticker, shares, candidate.price,
                    bucket=get_bucket_for_ticker(candidate.ticker) or 'SATELLITE'
                )
                buys_executed.append(candidate.ticker)
            else:
                errors.append(f"BUY {candidate.ticker}: {result.get('error')}")

            time.sleep(3)

        # Update satellites held
        self.update_sprint_state(satellites_held=buys_executed)

        logger.info(f"Day 2 complete: {trades_executed} trades ({len(sells_executed)} sells, {len(buys_executed)} buys)")

        return {
            'success': True,
            'trades_executed': trades_executed,
            'sells': sells_executed,
            'buys': buys_executed,
            'errors': errors
        }

    def _execute_day3(self) -> Dict:
        """
        Day 3: Rotate remaining trades to hit cap.

        Rotations = floor((SprintCap - TradesUsed) / 2)
        """
        logger.info("=" * 70)
        logger.info("SPRINT3 DAY 3: Final rotation")
        logger.info("=" * 70)

        trades_executed = 0
        errors = []
        sells_executed = []
        buys_executed = []

        budget = self.get_trades_budget()
        rotations_available = budget['sprint_remaining'] // 2

        logger.info(f"Budget allows {rotations_available} rotations ({budget['sprint_remaining']} trades)")

        if rotations_available < 1:
            return {
                'success': True,
                'trades_executed': 0,
                'message': 'No rotation budget remaining'
            }

        # Get market data
        market_data = self.collector.get_all_data(
            list(SPRINT3_CORE.keys()) + SPRINT3_SATELLITE_UNIVERSE
        )

        if not market_data.get('VOO'):
            return {'success': False, 'error': 'Could not fetch market data', 'trades_executed': 0}

        # Get portfolio value
        try:
            portfolio_value, cash, buying_power = self.bot.get_capital_from_trade_kpis("VOO")
        except Exception as e:
            return {'success': False, 'error': f'Could not get capital: {e}', 'trades_executed': 0}

        positions = self.state.get_positions()
        sprint_state = self.get_sprint_state()
        satellites_held = sprint_state.get('satellites_held', [])

        # Score current satellites to find worst ones to rotate
        current_scores = []
        for ticker in satellites_held:
            if ticker not in positions:
                continue
            ticker_data = market_data.get(ticker, {})
            if ticker_data:
                ticker_data['ticker'] = ticker
                candidate = calculate_sprint3_score(ticker_data, market_data.get('VOO', {}))
                if candidate:
                    current_scores.append((ticker, candidate.score, positions[ticker]))

        # Sort by score ascending (worst first)
        current_scores.sort(key=lambda x: x[1])

        # Rotate worst performers
        rotations_done = 0
        for ticker, score, position in current_scores[:rotations_available]:
            can_sell, reason = can_sell_sprint3(position)
            if not can_sell:
                logger.warning(f"SELL {ticker}: Cannot sell - {reason}")
                continue

            shares = position.get('shares', 0)
            if shares < 1:
                continue

            # Sell
            result = self._execute_sell(ticker, shares, f"SPRINT3_D3_ROTATE_WORST")
            if result['success']:
                trades_executed += 1
                self.state.remove_position(ticker)
                sells_executed.append(ticker)
            else:
                errors.append(f"SELL {ticker}: {result.get('error')}")
                continue

            time.sleep(3)
            rotations_done += 1

        # Get new candidates
        candidates = get_top_sprint3_candidates(
            market_data,
            n=len(sells_executed),
            exclude_tickers=list(positions.keys()) + sells_executed,
            require_eligible=True
        )

        # Buy replacements
        for candidate in candidates[:len(sells_executed)]:
            shares = calculate_shares_for_allocation(
                portfolio_value, SPRINT3_SATELLITE_SIZE, candidate.price
            )

            if shares < 1:
                continue

            result = self._execute_buy(
                candidate.ticker, shares,
                f"SPRINT3_D3_SAT_SCORE_{candidate.score:.4f}",
                candidate.price
            )

            if result['success']:
                trades_executed += 1
                self.state.add_position(
                    candidate.ticker, shares, candidate.price,
                    bucket=get_bucket_for_ticker(candidate.ticker) or 'SATELLITE'
                )
                buys_executed.append(candidate.ticker)
            else:
                errors.append(f"BUY {candidate.ticker}: {result.get('error')}")

            time.sleep(3)

        # Update satellites held
        new_satellites = [t for t in satellites_held if t not in sells_executed] + buys_executed
        self.update_sprint_state(satellites_held=new_satellites)

        logger.info(f"Day 3 complete: {trades_executed} trades ({len(sells_executed)} sells, {len(buys_executed)} buys)")
        logger.info("SPRINT3 COMPLETE!")

        return {
            'success': True,
            'trades_executed': trades_executed,
            'sells': sells_executed,
            'buys': buys_executed,
            'errors': errors
        }

    def _execute_buy(self, ticker: str, shares: int, rationale: str, price: float) -> Dict:
        """Execute a buy order."""
        logger.info(f"BUY {shares} {ticker} @ ~${price:.2f} ({rationale})")

        if self.dry_run:
            logger.info(f"[DRY RUN] Would buy {shares} {ticker}")
            return {'success': True, 'dry_run': True}

        # Calculate limit price
        limit_price = max(round(price * 1.002, 2), SPRINT3_LIMIT_FLOOR)

        from execution_pipeline import ExecutionPipeline, TradeOrder

        order = TradeOrder(
            ticker=ticker,
            side="BUY",
            shares=shares,
            rationale=rationale,
            portfolio_pct=SPRINT3_SATELLITE_SIZE * 100
        )

        pipeline = ExecutionPipeline(self.bot, state_manager=self.state, dry_run=self.dry_run)
        result = pipeline.execute(order)

        return {
            'success': result.success,
            'error': result.message if not result.success else None
        }

    def _execute_sell(self, ticker: str, shares: int, rationale: str) -> Dict:
        """Execute a sell order."""
        logger.info(f"SELL {shares} {ticker} ({rationale})")

        if self.dry_run:
            logger.info(f"[DRY RUN] Would sell {shares} {ticker}")
            return {'success': True, 'dry_run': True}

        from execution_pipeline import ExecutionPipeline, TradeOrder

        order = TradeOrder(
            ticker=ticker,
            side="SELL",
            shares=shares,
            rationale=rationale
        )

        pipeline = ExecutionPipeline(self.bot, state_manager=self.state, dry_run=self.dry_run)
        result = pipeline.execute(order)

        return {
            'success': result.success,
            'error': result.message if not result.success else None
        }


# =============================================================================
# DRY RUN / PLANNING
# =============================================================================

def plan_sprint3(market_data: Dict, positions: Dict, sprint_day: int = 1) -> Dict:
    """
    Plan sprint3 trades without executing.

    Args:
        market_data: Market data dict
        positions: Current positions
        sprint_day: Which day to plan (1, 2, or 3)

    Returns:
        Plan dict with proposed trades
    """
    plan = {
        'day': sprint_day,
        'sells': [],
        'buys': [],
        'errors': [],
    }

    voo_data = market_data.get('VOO')
    if not voo_data:
        plan['errors'].append('VOO data missing')
        return plan

    if sprint_day == 1:
        # Plan core buys
        for ticker, target_pct in SPRINT3_CORE.items():
            if ticker not in positions:
                ticker_data = market_data.get(ticker, {})
                price = ticker_data.get('price', 0)
                if price > 0:
                    plan['buys'].append({
                        'ticker': ticker,
                        'type': 'CORE',
                        'target_pct': target_pct,
                        'price': price
                    })

        # Plan satellite buys
        candidates = get_top_sprint3_candidates(
            market_data,
            n=SPRINT3_SATELLITE_COUNT,
            exclude_tickers=list(positions.keys()),
            require_eligible=True
        )

        for c in candidates:
            plan['buys'].append({
                'ticker': c.ticker,
                'type': 'SATELLITE',
                'score': c.score,
                'price': c.price,
                'rr3': c.rr3,
                'rr10': c.rr10,
                'vol10': c.vol10
            })

    elif sprint_day == 2:
        # Plan all satellite sells
        for ticker in list(positions.keys()):
            if ticker in SPRINT3_CORE:
                continue
            plan['sells'].append({
                'ticker': ticker,
                'shares': positions[ticker].get('shares', 0)
            })

        # Plan replacement buys
        candidates = get_top_sprint3_candidates(
            market_data,
            n=SPRINT3_SATELLITE_COUNT,
            exclude_tickers=list(SPRINT3_CORE.keys()),
            require_eligible=True
        )

        for c in candidates:
            plan['buys'].append({
                'ticker': c.ticker,
                'type': 'SATELLITE',
                'score': c.score,
                'price': c.price
            })

    elif sprint_day == 3:
        # Score current satellites
        satellites = [t for t in positions if t not in SPRINT3_CORE]
        scores = []
        for ticker in satellites:
            ticker_data = market_data.get(ticker, {})
            if ticker_data:
                ticker_data['ticker'] = ticker
                c = calculate_sprint3_score(ticker_data, voo_data)
                if c:
                    scores.append((ticker, c.score))

        scores.sort(key=lambda x: x[1])

        # Plan to rotate worst 4 (example)
        for ticker, score in scores[:4]:
            plan['sells'].append({
                'ticker': ticker,
                'score': score
            })

        # Get replacements
        candidates = get_top_sprint3_candidates(
            market_data,
            n=4,
            exclude_tickers=list(positions.keys()),
            require_eligible=True
        )

        for c in candidates:
            plan['buys'].append({
                'ticker': c.ticker,
                'score': c.score,
                'price': c.price
            })

    # Summary
    plan['total_trades'] = len(plan['sells']) + len(plan['buys'])

    return plan


def print_sprint3_plan(plan: Dict):
    """Print a sprint3 plan in readable format."""
    print("\n" + "=" * 70)
    print(f"SPRINT3 DAY {plan['day']} PLAN")
    print("=" * 70)

    print(f"\nSELLS ({len(plan['sells'])}):")
    for sell in plan['sells']:
        print(f"  SELL {sell.get('ticker')}: {sell.get('shares', 'all')} shares")

    print(f"\nBUYS ({len(plan['buys'])}):")
    for buy in plan['buys']:
        if buy.get('type') == 'CORE':
            print(f"  BUY {buy['ticker']}: {buy['target_pct']*100:.0f}% allocation @ ${buy['price']:.2f}")
        else:
            print(f"  BUY {buy['ticker']}: Score={buy.get('score', 0):.4f} @ ${buy.get('price', 0):.2f}")

    print(f"\nTOTAL TRADES: {plan['total_trades']}")

    if plan['errors']:
        print(f"\nERRORS:")
        for err in plan['errors']:
            print(f"  - {err}")

    print("=" * 70)


def print_sprint3_scoring_report(market_data: Dict):
    """Print scoring report for all sprint3 candidates."""
    candidates = score_all_sprint3_candidates(market_data)

    print("\n" + "=" * 100)
    print("SPRINT3 SATELLITE SCORING REPORT")
    print("=" * 100)
    print(f"{'Rank':<5} {'Ticker':<8} {'Score':>10} {'RR3':>10} {'RR10':>10} {'Vol10':>10} "
          f"{'Price':>10} {'Trend':>8} {'Eligible':>10}")
    print("-" * 100)

    for i, c in enumerate(candidates, 1):
        trend = "OK" if c.trend_ok else "FAIL"
        eligible = "YES" if c.is_eligible else c.disqualify_reason[:10] if c.disqualify_reason else "NO"
        print(f"{i:<5} {c.ticker:<8} {c.score:>10.4f} {c.rr3:>10.4f} {c.rr10:>10.4f} "
              f"{c.vol10:>10.4f} {c.price:>10.2f} {trend:>8} {eligible:>10}")

    print("=" * 100)

    # Top 16 eligible
    eligible = [c for c in candidates if c.is_eligible][:16]
    print(f"\nTOP 16 ELIGIBLE: {', '.join(c.ticker for c in eligible)}")
