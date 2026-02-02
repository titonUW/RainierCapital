"""
Daily Trading Routine for StockTrak Bot

Contains the main trading logic executed daily at 3:55 PM ET:
- Market regime detection
- Position evaluation
- Entry/exit decisions
- Order execution

UPDATED (Perfection Patch):
- Friday-only discretionary rotations (DeMiguel-consistent turnover reduction)
- Daily trades are risk exits only (stop-loss, trend break, price violation)
- Structural 1/N diversification across 8 thematic buckets
"""

import logging
import signal
import sys
import threading
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import time
from contextlib import contextmanager


# =============================================================================
# EXECUTION TIMEOUT - Hard cap to prevent infinite hangs
# =============================================================================
EXECUTION_TIMEOUT_SECONDS = 540  # 9 minutes (leave 1 minute buffer before market close)


class ExecutionTimeoutError(Exception):
    """Raised when execution exceeds timeout."""
    pass


@contextmanager
def execution_timeout(seconds: int, error_message: str = "Execution timeout"):
    """
    Context manager for execution timeout.
    Works on both Unix (signal-based) and Windows (thread-based).
    """
    if sys.platform == 'win32':
        # Windows: Use threading-based timeout
        timer = threading.Timer(seconds, lambda: (_ for _ in ()).throw(ExecutionTimeoutError(error_message)))
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
    else:
        # Unix: Use signal-based timeout (more reliable)
        def timeout_handler(signum, frame):
            raise ExecutionTimeoutError(error_message)

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

import config
from config import (
    CORE_POSITIONS, SATELLITE_POSITION_SIZE, DAY1_SATELLITES,
    REGIME_PARAMS, EVENT_FREEZE_DATES, HARD_STOP_TRADES,
    get_bucket_for_ticker
)
from stocktrak_bot import StockTrakBot
from market_data import MarketDataCollector, print_market_summary
from state_manager import StateManager, sync_state_with_stocktrak
from scoring import (
    get_top_candidates, get_double7_buy_candidates,
    get_double7_sell_candidates, select_replacement_satellite,
    print_scoring_report, get_best_per_bucket
)
from validators import (
    get_vix_regime, get_market_regime, validate_holding_period,
    validate_price, can_buy, can_sell, validate_trade_count
)
from utils import (
    calculate_limit_price, calculate_shares_for_allocation,
    format_currency, is_trading_day, get_current_time_et
)
from execution_pipeline import ExecutionPipeline, TradeOrder, TradeResult

logger = logging.getLogger('stocktrak_bot.daily_routine')


def execute_trade_safely(bot, state: StateManager, ticker: str, side: str,
                         shares: int, rationale: str, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Execute a trade using the stall-proof execution pipeline.

    This replaces direct bot.place_buy_order/place_sell_order calls with
    a fully verified pipeline that:
    - Handles retries with screenshots
    - Verifies trade in history
    - Adds trade notes
    - Never double-places

    Args:
        bot: StockTrakBot instance
        state: StateManager instance
        ticker: Stock ticker
        side: "BUY" or "SELL"
        shares: Number of shares
        rationale: Trade rationale for notes
        dry_run: If True, stop before placing

    Returns:
        Tuple of (success, message)
    """
    order = TradeOrder(
        ticker=ticker,
        side=side,
        shares=shares,
        rationale=rationale
    )

    pipeline = ExecutionPipeline(bot, state_manager=state, dry_run=dry_run)
    result = pipeline.execute(order)

    if result.success:
        return True, f"{side} {shares} {ticker} executed successfully"
    else:
        return False, f"Failed at {result.state.value}: {result.message}"


def execute_daily_routine():
    """
    Main daily execution - called at 3:55 PM ET.

    This is the heart of the trading bot. It:
    1. Logs into StockTrak
    2. Collects market data
    3. Determines market/VIX regimes
    4. Evaluates existing positions for exits
    5. Looks for new entry opportunities
    6. Executes trades

    CRITICAL: Wrapped in execution_timeout to prevent hangs.
    """
    logger.info("=" * 70)
    logger.info(f"DAILY ROUTINE STARTED: {datetime.now()}")
    logger.info(f"TIMEOUT: {EXECUTION_TIMEOUT_SECONDS} seconds")
    logger.info("=" * 70)

    state = StateManager()

    # CRITICAL: Verify state integrity before proceeding
    if not _verify_state_integrity(state):
        logger.critical("STATE INTEGRITY CHECK FAILED - aborting execution")
        return

    # Check if already ran today
    if state.already_executed_today():
        logger.info("Already executed today - skipping")
        return

    # Check if trading day
    if not is_trading_day():
        logger.info("Not a trading day - skipping")
        return

    bot = None
    try:
        # WRAP ENTIRE EXECUTION IN TIMEOUT
        with execution_timeout(EXECUTION_TIMEOUT_SECONDS, "Daily routine exceeded timeout"):
            _execute_daily_routine_inner(bot, state)

    except ExecutionTimeoutError as e:
        logger.critical(f"EXECUTION TIMEOUT: {e}")
        state.log_error(f"Execution timeout after {EXECUTION_TIMEOUT_SECONDS}s")

    except Exception as e:
        logger.critical(f"CRITICAL ERROR in daily routine: {e}")
        import traceback
        logger.critical(traceback.format_exc())
        if state:
            state.log_error(str(e))

    finally:
        if bot:
            try:
                bot.close()
            except Exception:
                pass


def _verify_state_integrity(state: StateManager) -> bool:
    """
    Verify state file integrity before execution.

    Returns False if state appears corrupted.
    """
    trades_used = state.get_trades_used()

    # Check for impossible values
    if trades_used < 0:
        logger.critical(f"STATE CORRUPTION: trades_used is negative ({trades_used})")
        return False

    if trades_used > 100:  # Allow some buffer above 80 for edge cases
        logger.critical(f"STATE CORRUPTION: trades_used impossibly high ({trades_used})")
        return False

    # Check positions count is reasonable
    positions = state.get_positions()
    if len(positions) > 50:  # Way more than our strategy would ever hold
        logger.critical(f"STATE CORRUPTION: too many positions ({len(positions)})")
        return False

    logger.info(f"State integrity verified: {trades_used} trades, {len(positions)} positions")
    return True


def _execute_daily_routine_inner(bot, state: StateManager):
    """
    Inner execution logic (wrapped in timeout by caller).
    """
    # Initialize bot
    bot = StockTrakBot()
    bot.start_browser(headless=True)

    if not bot.login():
        raise Exception("Login failed - cannot proceed")

    # Get capital from trade page KPIs (robust, fail-closed)
    logger.info("Reading capital from trade page KPIs...")
    try:
        portfolio_value, cash_balance, buying_power = bot.get_capital_from_trade_kpis("VOO")
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to read capital - {e}")
        raise RuntimeError(f"Cannot proceed without capital data: {e}")

    logger.info(f"Capital: Portfolio=${portfolio_value:,.2f}, Cash=${cash_balance:,.2f}, Buying Power=${buying_power:,.2f}")

    # Get other StockTrak data
    logger.info("Fetching holdings and trade count...")
    stocktrak_holdings = bot.get_current_holdings()
    trade_count = bot.get_transaction_count()

    # Sync state with StockTrak
    sync_state_with_stocktrak(state, stocktrak_holdings, trade_count)

    # Get market data with circuit breaker
    logger.info("Fetching market data...")
    collector = MarketDataCollector()
    market_data = collector.get_all_data()

    # CRITICAL: Verify we have essential data
    if not _verify_market_data(market_data):
        raise RuntimeError("Market data fetch failed - cannot proceed safely")

    # Print market summary
    print_market_summary(market_data)

    # Determine regimes
    voo_data = market_data.get('VOO', {})
    voo_price = voo_data.get('price', 0)
    voo_sma200 = voo_data.get('sma200') or voo_data.get('sma100')

    # CRITICAL: VIX must be valid - no default
    vix_level = market_data.get('vix')
    if vix_level is None:
        raise RuntimeError("VIX data unavailable - cannot determine regime safely")

    market_regime = get_market_regime(voo_price, voo_sma200)
    vix_regime = get_vix_regime(vix_level)

    logger.info(f"Portfolio Value: {format_currency(portfolio_value)}")
    logger.info(f"Market Regime: {market_regime}")
    logger.info(f"VIX Regime: {vix_regime} (VIX={vix_level:.2f})")
    logger.info(f"Trades Used: {state.get_trades_used()}/80")

    # Print scoring report
    print_scoring_report(market_data, state.get_positions())

    # Execute based on regime
    if market_regime == 'RISK_OFF':
        execute_risk_off_mode(bot, state, market_data, portfolio_value, vix_level)
    else:
        execute_normal_mode(bot, state, market_data, portfolio_value, vix_level)

    # Log daily value
    state.log_daily_value(portfolio_value, vix_level)

    # Mark execution complete
    state.mark_execution()

    logger.info("Daily routine completed successfully")


def _verify_market_data(market_data: Dict) -> bool:
    """
    Verify we have minimum required market data.

    Returns False if critical data is missing.
    """
    # Must have VIX
    if market_data.get('vix') is None:
        logger.critical("MISSING: VIX data")
        return False

    # Must have VOO for regime detection
    voo_data = market_data.get('VOO')
    if not voo_data or not voo_data.get('price'):
        logger.critical("MISSING: VOO price data")
        return False

    # Count successful ticker fetches
    total_tickers = len([k for k in market_data.keys() if k not in ('vix',)])
    failed_tickers = len([k for k, v in market_data.items() if k != 'vix' and v is None])

    if failed_tickers > total_tickers * 0.5:  # More than 50% failed
        logger.critical(f"TOO MANY FAILURES: {failed_tickers}/{total_tickers} tickers failed")
        return False

    logger.info(f"Market data verified: {total_tickers - failed_tickers}/{total_tickers} tickers OK")
    return True


def is_friday() -> bool:
    """Check if today is Friday (day for discretionary rotations)."""
    return datetime.now().weekday() == 4  # Monday=0, Friday=4


def execute_normal_mode(
    bot: StockTrakBot,
    state: StateManager,
    market_data: Dict,
    portfolio_value: float,
    vix_level: float
):
    """
    Normal trading mode (RISK_ON regime).

    UPDATED (Perfection Patch - DeMiguel-consistent turnover reduction):
    - Daily: Only risk exits (stop-loss, trend break, price violation)
    - Friday only: Discretionary rotations (profit-taking, new entries, stale money)

    This "set and forget" approach aligns with the 1/N paper's spirit and
    improves rubric #4 (Cost & Efficiency).
    """
    logger.info("Executing NORMAL mode (Risk-On)...")

    positions = state.get_positions()
    vix_regime = get_vix_regime(vix_level)
    regime_params = REGIME_PARAMS[vix_regime]
    friday = is_friday()

    if friday:
        logger.info("FRIDAY - Discretionary rotations ENABLED")
    else:
        logger.info("NON-FRIDAY - Risk exits only (no discretionary rotations)")

    sells_executed = []
    buys_executed = []

    # ===== STEP 1: Evaluate existing positions for RISK EXITS (daily) =====
    logger.info("Evaluating positions for risk exits...")

    for ticker, position in positions.items():
        # Skip core positions (rarely sell)
        if ticker in CORE_POSITIONS:
            continue

        ticker_data = market_data.get(ticker, {})
        if not ticker_data:
            logger.warning(f"No market data for {ticker}")
            continue

        current_price = ticker_data.get('price', 0)
        entry_price = position.get('entry_price', current_price)
        shares = position.get('shares', 0)
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # Check holding period
        can_sell_now, hold_reason = validate_holding_period(position)
        if not can_sell_now:
            logger.debug(f"{ticker}: {hold_reason}")
            continue

        # Check for RISK EXIT triggers (these happen daily)
        sell_reason = None
        is_risk_exit = False

        # 1. Price violation risk (approaching $5) - RISK EXIT
        if current_price < 5.50:
            sell_reason = "PRICE_VIOLATION_RISK"
            is_risk_exit = True

        # 2. Stop-loss - RISK EXIT
        stop_loss = regime_params['stop_loss_pct']
        if pnl_pct <= -stop_loss:
            sell_reason = f"STOP_LOSS_{stop_loss*100:.0f}PCT"
            is_risk_exit = True

        # 3. Trend break (price < SMA50 with negative P&L) - RISK EXIT
        sma50 = ticker_data.get('sma50', 0)
        if current_price < sma50 and pnl_pct < 0:
            sell_reason = "TREND_BREAK"
            is_risk_exit = True

        # 4. Stale money (held 15+ days with <2% gain) - FRIDAY ONLY
        if friday and not sell_reason:
            entry_date_str = position.get('entry_date')
            if entry_date_str:
                from utils import get_trading_days_between
                entry_date = datetime.fromisoformat(entry_date_str).date()
                days_held = get_trading_days_between(entry_date, datetime.now().date())
                if days_held >= 15 and pnl_pct < 0.02:
                    sell_reason = "STALE_MONEY"
                    is_risk_exit = False  # Discretionary, not risk

        # Execute sell if triggered (risk exits daily, stale money Friday only)
        if sell_reason and (is_risk_exit or friday):
            # Validate trade count
            trade_valid, _ = validate_trade_count(state.get_trades_used(), is_new_buy=False)
            if not trade_valid:
                logger.warning(f"Cannot sell {ticker}: trade limit reached")
                continue

            # Use stall-proof execution pipeline
            exit_type = "RISK EXIT" if is_risk_exit else "DISCRETIONARY"
            success, msg = execute_trade_safely(
                bot, state, ticker, "SELL", shares,
                rationale=f"{exit_type}: {sell_reason}",
                dry_run=config.DRY_RUN_MODE
            )

            if success:
                logger.info(f"SELL [{exit_type}] {ticker}: {shares} shares ({sell_reason})")
                state.remove_position(ticker)
                sells_executed.append((ticker, position.get('bucket')))
                time.sleep(2)

    # ===== STEP 2: Check for profit-taking (FRIDAY ONLY) =====
    if friday:
        logger.info("Checking for profit-taking opportunities (Friday discretionary)...")

        double7_highs = get_double7_sell_candidates(market_data, positions)
        for ticker, reason in double7_highs:
            if any(t == ticker for t, _ in sells_executed):
                continue

            # Validate we can sell
            position = positions.get(ticker, {})
            can_sell_result, checks = can_sell(ticker, position, positions, state.get_trades_used())
            if not can_sell_result:
                logger.debug(f"{ticker}: Cannot sell - {checks}")
                continue

            # This is optional profit-taking, only do if we have trade budget
            if state.get_trades_used() >= HARD_STOP_TRADES:
                logger.info("Skipping optional profit-taking - at trade limit")
                break

            ticker_data = market_data.get(ticker, {})
            current_price = ticker_data.get('price', 0)
            shares = position.get('shares', 0)

            # Use stall-proof execution pipeline
            success, msg = execute_trade_safely(
                bot, state, ticker, "SELL", shares,
                rationale="PROFIT_TAKE_DISCRETIONARY",
                dry_run=config.DRY_RUN_MODE
            )
            if success:
                logger.info(f"PROFIT TAKE [DISCRETIONARY] {ticker}: {shares} shares")
                state.increment_week_replacements()
                state.remove_position(ticker)
                sells_executed.append((ticker, position.get('bucket')))
                time.sleep(2)
    else:
        logger.info("Skipping profit-taking (non-Friday)")

    # ===== STEP 3: Look for new entry opportunities (FRIDAY ONLY for discretionary) =====
    # Exception: If a risk exit created an empty bucket, we can replace to maintain min holdings

    # Check event freeze
    if datetime.now().date() in EVENT_FREEZE_DATES:
        logger.info("EVENT FREEZE - no new positions today")
        return

    # Update positions after sells
    positions = state.get_positions()
    current_satellites = sum(1 for t in positions if t not in CORE_POSITIONS)

    # Determine if we need emergency replacement (to maintain structural diversification)
    buckets_sold = [bucket for _, bucket in sells_executed if bucket]
    need_emergency_replacement = len(positions) < 4  # Below min holdings

    if friday:
        logger.info("Looking for new entry opportunities (Friday discretionary)...")
    elif need_emergency_replacement:
        logger.info("Looking for emergency replacement (maintain min holdings)...")
    else:
        logger.info("Skipping new entries (non-Friday, no emergency)")
        logger.info(f"Session summary: {len(sells_executed)} sells, 0 buys (non-Friday)")
        return

    # Check if we should buy (have room and budget)
    max_satellites = regime_params['max_satellites']
    weekly_cap = regime_params['weekly_replacement_cap']
    week_replacements = state.get_week_replacements()

    if current_satellites >= max_satellites:
        logger.info(f"At max satellites ({current_satellites}/{max_satellites}) for {vix_regime} regime")
        logger.info(f"Session summary: {len(sells_executed)} sells, 0 buys")
        return

    if friday and week_replacements >= weekly_cap:
        logger.info(f"At weekly cap ({week_replacements}/{weekly_cap})")
        logger.info(f"Session summary: {len(sells_executed)} sells, 0 buys")
        return

    if state.get_trades_used() >= HARD_STOP_TRADES:
        logger.info("At trade limit - no new buys")
        logger.info(f"Session summary: {len(sells_executed)} sells, 0 buys")
        return

    # Get candidates - prioritize replacing sold buckets to maintain 1/N structure
    if friday:
        buy_candidates = get_double7_buy_candidates(market_data, positions, vix_level)
    else:
        # Emergency replacement only - try to fill the buckets we just sold
        buy_candidates = []
        for sold_bucket in buckets_sold:
            replacement = select_replacement_satellite(
                market_data, positions, vix_level,
                exclude_tickers=[t for t, _ in sells_executed],
                for_bucket=sold_bucket
            )
            if replacement:
                buy_candidates.append(replacement)

    for candidate in buy_candidates:
        ticker = candidate.ticker
        price = candidate.price

        # Calculate position size
        shares = calculate_shares_for_allocation(
            portfolio_value, SATELLITE_POSITION_SIZE, price
        )

        if shares < 1:
            logger.debug(f"{ticker}: Position too small")
            continue

        # Full validation
        all_valid, checks = can_buy(
            ticker, price, shares, portfolio_value,
            state.get_trades_used(), week_replacements,
            vix_level, positions, market_data
        )

        if not all_valid:
            logger.debug(f"{ticker}: Failed validation - {checks}")
            continue

        # Execute buy using stall-proof pipeline
        entry_type = "DISCRETIONARY" if friday else "EMERGENCY"
        rationale = 'DOUBLE7_ENTRY' if friday else 'EMERGENCY_REPLACE'

        success, msg = execute_trade_safely(
            bot, state, ticker, "BUY", shares,
            rationale=f"{entry_type}: {rationale}",
            dry_run=config.DRY_RUN_MODE
        )

        if success:
            logger.info(f"BUY [{entry_type}] {ticker}: {shares} shares")
            limit_price = calculate_limit_price(price, is_buy=True)
            state.add_position(ticker, shares, limit_price, bucket=candidate.bucket)
            buys_executed.append(ticker)
            week_replacements += 1
            time.sleep(2)

            # Check limits
            if len(buys_executed) >= (max_satellites - current_satellites):
                break
            if state.get_trades_used() >= HARD_STOP_TRADES:
                break
            if friday and week_replacements >= weekly_cap:
                break
            if not friday and len(positions) >= 4:  # Emergency replacement complete
                break

    logger.info(f"Session summary: {len(sells_executed)} sells, {len(buys_executed)} buys")


def execute_risk_off_mode(
    bot: StockTrakBot,
    state: StateManager,
    market_data: Dict,
    portfolio_value: float,
    vix_level: float
):
    """
    Risk-off mode (VOO < SMA200).

    In risk-off:
    - Tighten stop-losses
    - No new satellite buys
    - Consider reducing satellite exposure
    """
    logger.info("Executing RISK-OFF mode...")

    positions = state.get_positions()
    tightened_stop = 0.10  # 10% stop in risk-off

    for ticker, position in positions.items():
        if ticker in CORE_POSITIONS:
            continue

        ticker_data = market_data.get(ticker, {})
        if not ticker_data:
            continue

        current_price = ticker_data.get('price', 0)
        entry_price = position.get('entry_price', current_price)
        shares = position.get('shares', 0)
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # Check holding period
        can_sell_now, _ = validate_holding_period(position)
        if not can_sell_now:
            continue

        # Tightened stop-loss in risk-off
        if pnl_pct <= -tightened_stop:
            trade_valid, _ = validate_trade_count(state.get_trades_used(), is_new_buy=False)
            if not trade_valid:
                continue

            # Use stall-proof execution pipeline
            success, msg = execute_trade_safely(
                bot, state, ticker, "SELL", shares,
                rationale="RISK_OFF_STOP",
                dry_run=config.DRY_RUN_MODE
            )

            if success:
                logger.info(f"RISK-OFF SELL {ticker}: {shares} shares")
                state.remove_position(ticker)
                time.sleep(2)

    logger.info("Risk-off mode complete - no new buys permitted")


def execute_day1_build():
    """
    Execute initial portfolio build on Day 1 (January 20).

    Builds the initial portfolio:
    - 3 core positions (VOO, VTI, VEA)
    - 8 satellite positions (pre-selected)
    """
    logger.info("=" * 70)
    logger.info("DAY-1 PORTFOLIO BUILD")
    logger.info(f"Started: {datetime.now()}")
    logger.info("=" * 70)

    state = StateManager()

    # Check if already built
    if state.get_trades_used() > 0:
        logger.warning("Trades already executed - Day-1 build may have run before")
        confirm = input("Continue anyway? (yes/no): ")
        if confirm.lower() != 'yes':
            return

    bot = None
    try:
        bot = StockTrakBot()
        bot.start_browser(headless=False)  # Show browser for Day-1

        if not bot.login():
            raise Exception("Login failed")

        # Get market data
        collector = MarketDataCollector()
        market_data = collector.get_all_data()
        print_market_summary(market_data)

        # Get capital from trade page KPIs - FAIL CLOSED, no assumptions
        logger.info("Reading capital from trade page KPIs...")
        portfolio_value, cash_balance, buying_power = bot.get_capital_from_trade_kpis("VOO")
        logger.info(f"Capital: Portfolio=${portfolio_value:,.2f}, Cash=${cash_balance:,.2f}, Buying Power=${buying_power:,.2f}")

        logger.info(f"Starting capital: {format_currency(portfolio_value)}")

        trades_executed = 0

        # ===== CORE POSITIONS (3 trades) =====
        logger.info("\n--- Building CORE positions ---")

        for ticker, target_pct in CORE_POSITIONS.items():
            ticker_data = market_data.get(ticker, {})
            price = ticker_data.get('price')

            if not price:
                price = collector.get_current_price(ticker)

            if not price or price < 1:
                logger.error(f"Could not get price for {ticker}")
                continue

            shares = calculate_shares_for_allocation(portfolio_value, target_pct, price)
            limit_price = calculate_limit_price(price, is_buy=True)

            logger.info(f"CORE: {ticker} - {shares} shares @ ${limit_price:.2f} ({target_pct*100:.0f}%)")

            # Use stall-proof execution pipeline
            success, msg = execute_trade_safely(
                bot, state, ticker, "BUY", shares,
                rationale=f"DAY1_CORE_{target_pct*100:.0f}PCT",
                dry_run=config.DRY_RUN_MODE
            )

            if success:
                logger.info(f"SUCCESS: {ticker} - {msg}")
                state.add_position(ticker, shares, limit_price, bucket='CORE')
                trades_executed += 1
            else:
                logger.error(f"Failed to buy {ticker}: {msg}")

            time.sleep(3)

        # ===== SATELLITE POSITIONS (8 trades) =====
        logger.info("\n--- Building SATELLITE positions ---")

        for ticker, bucket in DAY1_SATELLITES:
            ticker_data = market_data.get(ticker, {})
            price = ticker_data.get('price')

            if not price:
                price = collector.get_current_price(ticker)

            if not price or price < 6.00:
                logger.warning(f"{ticker} price ${price:.2f} below $6 - skipping")
                continue

            shares = calculate_shares_for_allocation(
                portfolio_value, SATELLITE_POSITION_SIZE, price
            )
            limit_price = calculate_limit_price(price, is_buy=True)

            logger.info(f"SATELLITE: {ticker} ({bucket}) - {shares} shares @ ${limit_price:.2f}")

            # Use stall-proof execution pipeline
            success, msg = execute_trade_safely(
                bot, state, ticker, "BUY", shares,
                rationale=f"DAY1_{bucket}",
                dry_run=config.DRY_RUN_MODE
            )

            if success:
                logger.info(f"SUCCESS: {ticker} - {msg}")
                state.add_position(ticker, shares, limit_price, bucket=bucket)
                trades_executed += 1
            else:
                logger.error(f"Failed to buy {ticker}: {msg}")

            time.sleep(3)

        # Mark execution
        state.mark_execution()

        logger.info("=" * 70)
        logger.info(f"DAY-1 BUILD COMPLETE")
        logger.info(f"Trades executed: {trades_executed}")
        logger.info(f"Trades remaining: {state.get_trades_remaining()}")
        logger.info("=" * 70)

        state.print_status()

    except Exception as e:
        logger.critical(f"CRITICAL ERROR in Day-1 build: {e}")
        import traceback
        logger.critical(traceback.format_exc())

    finally:
        if bot:
            input("\nPress Enter to close browser...")
            bot.close()


def health_check():
    """
    Periodic health check to verify bot is functioning.
    Called hourly by the scheduler.
    """
    logger.info("Health check...")

    state = StateManager()

    # Check state file
    trades_used = state.get_trades_used()
    positions = state.get_positions()

    logger.info(f"Trades: {trades_used}/80 | Positions: {len(positions)}")

    # Check if we're approaching limits
    if trades_used >= 75:
        logger.warning(f"ALERT: Approaching trade limit ({trades_used}/80)")

    if trades_used >= 80:
        logger.critical("TRADE LIMIT REACHED - Bot will not execute new trades")

    return True


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description='StockTrak Trading Bot - DeMiguel 1/N Methodology',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python daily_routine.py                  # Normal daily routine
  python daily_routine.py --day1           # Day-1 portfolio build
  python daily_routine.py --dry-run        # Test without submitting orders
  python daily_routine.py --safe-mode      # Max 5 shares, ETFs only
  python daily_routine.py --status         # Show bot status only
  python daily_routine.py --capital-test   # Test capital reading, no trading
        '''
    )

    parser.add_argument('--day1', action='store_true',
                        help='Execute Day-1 portfolio build (initial setup)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Navigate and fill orders but never submit (test mode)')
    parser.add_argument('--safe-mode', action='store_true',
                        help='Safe mode: max 5 shares per order, ETFs only, stop on any error')
    parser.add_argument('--status', action='store_true',
                        help='Show bot status and exit (no trading)')
    parser.add_argument('--capital-test', action='store_true',
                        help='Login and read capital from trade KPIs, then exit (no trading)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose/debug logging')

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Store flags globally for use by order functions
    import config
    config.DRY_RUN_MODE = args.dry_run
    config.SAFE_MODE = args.safe_mode

    if args.dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN MODE - Orders will NOT be submitted")
        logger.info("=" * 60)

    if args.safe_mode:
        logger.info("=" * 60)
        logger.info("SAFE MODE - Max 5 shares, ETFs only, fail on any error")
        logger.info("=" * 60)

    if args.status:
        # Just show status and exit
        state = StateManager()
        state.print_status()
        sys.exit(0)

    if args.capital_test:
        # Test capital reading from trade KPIs
        logger.info("=" * 60)
        logger.info("CAPITAL TEST - Reading from trade page KPIs")
        logger.info("=" * 60)
        bot = None
        try:
            bot = StockTrakBot()
            bot.start_browser(headless=False)  # Show browser for debugging

            if not bot.login():
                logger.error("Login failed")
                sys.exit(1)

            portfolio, cash, buying_power = bot.get_capital_from_trade_kpis("VOO")

            print("\n" + "=" * 60)
            print("CAPITAL TEST RESULTS")
            print("=" * 60)
            print(f"Portfolio Value:  ${portfolio:,.2f}")
            print(f"Cash Balance:     ${cash:,.2f}")
            print(f"Buying Power:     ${buying_power:,.2f}")
            print("=" * 60)
            logger.info("Capital test completed successfully")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Capital test failed: {e}")
            sys.exit(1)
        finally:
            if bot:
                bot.close()

    if args.day1:
        execute_day1_build()
    else:
        execute_daily_routine()
