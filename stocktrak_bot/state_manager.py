"""
State Management for StockTrak Bot

Handles persistence of bot state across restarts, including:
- Trade counts and limits
- Position tracking
- Weekly counters
- Transaction history
"""

import json
import os
import logging
from datetime import datetime, date
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import shutil

from config import (
    COMPETITION_START, COMPETITION_END, MAX_TRADES_TOTAL,
    STARTING_CAPITAL
)

logger = logging.getLogger('stocktrak_bot.state_manager')

STATE_FILE = 'bot_state.json'
STATE_BACKUP_FILE = 'bot_state_backup.json'


@dataclass
class Position:
    """Represents a portfolio position"""
    ticker: str
    shares: int
    entry_price: float
    entry_date: str
    bucket: Optional[str] = None
    current_price: Optional[float] = None
    pnl_pct: Optional[float] = None


@dataclass
class Trade:
    """Represents a completed trade"""
    timestamp: str
    ticker: str
    action: str  # 'BUY' or 'SELL'
    shares: int
    price: float
    reason: str
    trade_number: int


class StateManager:
    """Manages persistent state for the trading bot"""

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from disk or initialize fresh state"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                logger.info(f"Loaded state from {self.state_file}")
                return state
            else:
                logger.info("No existing state file, initializing fresh state")
                return self._initialize_state()
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading state: {e}")
            # Try backup
            if os.path.exists(STATE_BACKUP_FILE):
                logger.info("Attempting to load from backup...")
                try:
                    with open(STATE_BACKUP_FILE, 'r') as f:
                        return json.load(f)
                except:
                    pass
            return self._initialize_state()

    def _initialize_state(self) -> Dict:
        """Create fresh state for new bot instance"""
        return {
            'bot_version': '1.0.0',
            'created_at': datetime.now().isoformat(),
            'competition_start': COMPETITION_START,
            'competition_end': COMPETITION_END,
            'starting_capital': STARTING_CAPITAL,

            # Trade tracking
            'trades_used': 0,
            'trades_remaining': MAX_TRADES_TOTAL,

            # Weekly tracking
            'week_replacements': 0,
            'week_start_date': None,

            # Positions
            'positions': {},

            # Transaction log
            'trade_log': [],

            # Daily portfolio values
            'daily_values': [],

            # Execution tracking
            'last_execution_date': None,
            'last_execution_time': None,
            'execution_count': 0,

            # Error tracking
            'error_count': 0,
            'last_error': None,
        }

    def save(self):
        """Save current state to disk with backup"""
        try:
            # Create backup of existing state
            if os.path.exists(self.state_file):
                shutil.copy(self.state_file, STATE_BACKUP_FILE)

            # Update timestamp
            self.state['last_updated'] = datetime.now().isoformat()

            # Write new state
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2, default=str)

            logger.debug(f"State saved to {self.state_file}")

        except IOError as e:
            logger.error(f"Error saving state: {e}")
            raise

    def get_trades_used(self) -> int:
        """Get number of trades used"""
        return self.state.get('trades_used', 0)

    def get_trades_remaining(self) -> int:
        """Get number of trades remaining"""
        return MAX_TRADES_TOTAL - self.get_trades_used()

    def increment_trade_count(self, count: int = 1):
        """Increment trade counter"""
        self.state['trades_used'] += count
        self.state['trades_remaining'] = MAX_TRADES_TOTAL - self.state['trades_used']
        self.save()

    def get_week_replacements(self) -> int:
        """Get number of satellite replacements this week"""
        return self.state.get('week_replacements', 0)

    def increment_week_replacements(self, count: int = 1):
        """Increment weekly replacement counter"""
        self.state['week_replacements'] += count
        self.save()

    def reset_weekly_counters(self):
        """Reset weekly counters (called on Fridays)"""
        self.state['week_replacements'] = 0
        self.state['week_start_date'] = datetime.now().date().isoformat()
        self.save()
        logger.info("Weekly counters reset")

    def get_positions(self) -> Dict[str, Dict]:
        """Get all current positions"""
        return self.state.get('positions', {})

    def add_position(self, ticker: str, shares: int, price: float,
                     entry_date: str = None, bucket: str = None):
        """Add or update a position"""
        if entry_date is None:
            entry_date = datetime.now().date().isoformat()

        if ticker in self.state['positions']:
            # Update existing position (average cost)
            existing = self.state['positions'][ticker]
            old_shares = existing['shares']
            old_cost = existing['entry_price']
            new_total_shares = old_shares + shares
            new_avg_cost = ((old_shares * old_cost) + (shares * price)) / new_total_shares

            self.state['positions'][ticker] = {
                'shares': new_total_shares,
                'entry_price': new_avg_cost,
                'entry_date': existing['entry_date'],  # Keep original date
                'bucket': bucket or existing.get('bucket'),
            }
        else:
            # New position
            self.state['positions'][ticker] = {
                'shares': shares,
                'entry_price': price,
                'entry_date': entry_date,
                'bucket': bucket,
            }

        self.save()
        logger.info(f"Position added/updated: {ticker}")

    def remove_position(self, ticker: str):
        """Remove a position (after selling)"""
        if ticker in self.state['positions']:
            del self.state['positions'][ticker]
            self.save()
            logger.info(f"Position removed: {ticker}")

    def update_position_shares(self, ticker: str, new_shares: int):
        """Update shares for a position (partial sell)"""
        if ticker in self.state['positions']:
            if new_shares <= 0:
                self.remove_position(ticker)
            else:
                self.state['positions'][ticker]['shares'] = new_shares
                self.save()

    def log_trade(self, ticker: str, action: str, shares: int,
                  price: float, reason: str = ''):
        """Log a completed trade"""
        trade = {
            'timestamp': datetime.now().isoformat(),
            'ticker': ticker,
            'action': action,
            'shares': shares,
            'price': price,
            'reason': reason,
            'trade_number': self.get_trades_used(),
        }
        self.state['trade_log'].append(trade)
        self.save()
        logger.info(f"Trade logged: {action} {shares} {ticker} @ ${price:.2f}")

    def log_daily_value(self, portfolio_value: float, vix: float = None):
        """Log daily portfolio value"""
        entry = {
            'date': datetime.now().date().isoformat(),
            'value': portfolio_value,
            'vix': vix,
            'positions_count': len(self.get_positions()),
            'trades_used': self.get_trades_used(),
        }
        self.state['daily_values'].append(entry)
        self.save()

    def mark_execution(self):
        """Mark that daily execution was completed"""
        now = datetime.now()
        self.state['last_execution_date'] = now.date().isoformat()
        self.state['last_execution_time'] = now.time().isoformat()
        self.state['execution_count'] += 1
        self.save()

    def already_executed_today(self) -> bool:
        """Check if we already executed today"""
        today = datetime.now().date().isoformat()
        return self.state.get('last_execution_date') == today

    def already_submitted_today(self, ticker: str, action: str, shares: int, price: float) -> bool:
        """
        Check if an identical order was already submitted today.

        This prevents duplicate orders if the bot crashes and restarts.

        Args:
            ticker: Stock symbol
            action: 'BUY' or 'SELL'
            shares: Number of shares
            price: Limit price

        Returns:
            True if a matching order was already submitted today
        """
        today = datetime.now().date().isoformat()
        trade_log = self.state.get('trade_log', [])

        for trade in trade_log:
            # Check if trade is from today
            trade_date = trade.get('timestamp', '')[:10]  # YYYY-MM-DD
            if trade_date != today:
                continue

            # Check if it's the same order
            if (trade.get('ticker') == ticker and
                trade.get('action') == action and
                trade.get('shares') == shares and
                abs(trade.get('price', 0) - price) < 0.01):  # Price tolerance
                return True

        return False

    def get_orders_submitted_today(self) -> List[Dict]:
        """Get all orders submitted today for idempotency checking"""
        today = datetime.now().date().isoformat()
        trade_log = self.state.get('trade_log', [])

        return [
            trade for trade in trade_log
            if trade.get('timestamp', '')[:10] == today
        ]

    def log_error(self, error_msg: str):
        """Log an error occurrence"""
        self.state['error_count'] += 1
        self.state['last_error'] = {
            'timestamp': datetime.now().isoformat(),
            'message': error_msg,
        }
        self.save()

    def get_trade_log(self) -> List[Dict]:
        """Get full trade log"""
        return self.state.get('trade_log', [])

    def get_daily_values(self) -> List[Dict]:
        """Get daily portfolio value history"""
        return self.state.get('daily_values', [])

    def print_status(self):
        """Print current bot status"""
        print("\n" + "=" * 60)
        print("BOT STATUS")
        print("=" * 60)
        print(f"Trades Used: {self.get_trades_used()}/{MAX_TRADES_TOTAL}")
        print(f"Trades Remaining: {self.get_trades_remaining()}")
        print(f"Week Replacements: {self.get_week_replacements()}")
        print(f"Positions: {len(self.get_positions())}")
        print(f"Execution Count: {self.state.get('execution_count', 0)}")
        print(f"Last Execution: {self.state.get('last_execution_date')}")
        print(f"Error Count: {self.state.get('error_count', 0)}")
        print("=" * 60)

        # Print positions
        positions = self.get_positions()
        if positions:
            print("\nPOSITIONS:")
            for ticker, pos in positions.items():
                print(f"  {ticker}: {pos['shares']} shares @ ${pos['entry_price']:.2f} "
                      f"(Entry: {pos['entry_date']})")

        # Recent trades
        trades = self.get_trade_log()[-5:] if self.get_trade_log() else []
        if trades:
            print("\nRECENT TRADES:")
            for trade in trades:
                print(f"  #{trade['trade_number']}: {trade['action']} {trade['shares']} "
                      f"{trade['ticker']} @ ${trade['price']:.2f}")

        print()


def sync_state_with_stocktrak(state_manager: StateManager,
                               stocktrak_holdings: Dict,
                               stocktrak_trade_count: int):
    """
    Synchronize local state with actual StockTrak data.

    Called at the start of each execution to ensure state matches reality.

    Args:
        state_manager: StateManager instance
        stocktrak_holdings: Holdings from StockTrak
        stocktrak_trade_count: Trade count from StockTrak
    """
    logger.info("Synchronizing state with StockTrak...")

    # Update trade count if StockTrak shows different
    if stocktrak_trade_count != state_manager.get_trades_used():
        logger.warning(f"Trade count mismatch: local={state_manager.get_trades_used()}, "
                       f"StockTrak={stocktrak_trade_count}")
        state_manager.state['trades_used'] = stocktrak_trade_count
        state_manager.state['trades_remaining'] = MAX_TRADES_TOTAL - stocktrak_trade_count

    # Check for positions that exist in StockTrak but not locally
    local_positions = state_manager.get_positions()
    for ticker in stocktrak_holdings:
        if ticker not in local_positions:
            logger.warning(f"Position {ticker} found in StockTrak but not in local state")
            # We can't know entry price/date, so mark as unknown
            state_manager.add_position(
                ticker=ticker,
                shares=stocktrak_holdings[ticker].get('shares', 0),
                price=0,  # Unknown
                entry_date='2026-01-20',  # Assume Day 1
                bucket=None
            )

    # Check for positions in local state but not in StockTrak (sold externally?)
    for ticker in list(local_positions.keys()):
        if ticker not in stocktrak_holdings:
            logger.warning(f"Position {ticker} in local state but not in StockTrak - removing")
            state_manager.remove_position(ticker)

    state_manager.save()
    logger.info("State synchronization complete")


if __name__ == "__main__":
    # Test state management
    logging.basicConfig(level=logging.INFO)

    # Create test state manager
    sm = StateManager('test_state.json')
    sm.print_status()

    # Test adding position
    sm.add_position('VOO', 100, 450.00, bucket='CORE')
    sm.add_position('SMH', 50, 250.00, bucket='C_SEMIS')
    sm.log_trade('VOO', 'BUY', 100, 450.00, 'Day-1 build')
    sm.increment_trade_count()

    sm.print_status()

    # Clean up test file
    if os.path.exists('test_state.json'):
        os.remove('test_state.json')
    if os.path.exists('bot_state_backup.json'):
        os.remove('bot_state_backup.json')
