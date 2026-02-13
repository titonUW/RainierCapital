"""
State Management for StockTrak Bot

Handles persistence of bot state across restarts, including:
- Trade counts and limits
- Position tracking
- Weekly counters
- Transaction history

Thread-safe: Uses file locking to prevent concurrent write corruption.
"""

import json
import os
import logging
import threading
import uuid
from datetime import datetime, date, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import shutil

from config import (
    COMPETITION_START, COMPETITION_END, MAX_TRADES_TOTAL,
    STARTING_CAPITAL
)

logger = logging.getLogger('stocktrak_bot.state_manager')

# Global lock for thread-safe state file access
_state_file_lock = threading.RLock()

STATE_FILE = 'bot_state.json'
STATE_BACKUP_FILE = 'bot_state_backup.json'
DASHBOARD_STATE_FILE = os.path.join(os.path.dirname(__file__), 'state', 'dashboard_state.json')

# Ensure state directory exists
os.makedirs(os.path.dirname(DASHBOARD_STATE_FILE), exist_ok=True)


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
        # Migrate positions to include timestamps (one-time migration)
        self._migrate_position_timestamps()

    def _load_state(self) -> Dict:
        """Load state from disk or initialize fresh state.

        Thread-safe: Uses file locking for consistent reads.
        """
        with _state_file_lock:
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
                    except (json.JSONDecodeError, IOError) as backup_error:
                        logger.critical(f"BOTH state files corrupted: primary={e}, backup={backup_error}")
                        logger.critical("INITIALIZING FRESH STATE - position data will be lost!")

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

            # SPRINT3 state tracking
            'sprint3': {
                'mode': None,           # 'SPRINT3' when active
                'sprint_day': 0,        # 1, 2, or 3
                'trades_used_sprint': 0,
                'last_run_time': None,
                'last_run_day': None,
                'satellites_held': [],  # List of satellite tickers
                'last_result': None,
                'last_error': None,
                'last_screenshot': None,
            },
        }

    def _parse_timestamp_utc(self, ts: str) -> Optional[datetime]:
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

    def _migrate_position_timestamps(self):
        """
        Migrate existing positions to include timestamp-based holding period fields
        AND lot-based tracking for proper FIFO compliance.

        This migration:
        1. Migrates legacy last_buy_timestamp if missing
        2. Migrates to lot-based structure if lots are missing
        3. Infers lots from trade_log BUY entries when possible
        4. Falls back to synthetic lot (fail-safe: won't violate 24h)

        Called automatically in __init__.
        """
        changed = False
        trade_log = self.state.get('trade_log', [])

        # Build lookup of BUY transactions per ticker from trade_log
        buys_by_ticker = {}
        for tr in trade_log:
            if tr.get('action') == 'BUY' and tr.get('ticker'):
                ticker = tr['ticker']
                if ticker not in buys_by_ticker:
                    buys_by_ticker[ticker] = []
                buys_by_ticker[ticker].append(tr)

        positions = self.state.get('positions', {})
        for ticker, pos in positions.items():
            # 1. Migrate last_buy_timestamp if missing (legacy support)
            if 'last_buy_timestamp' not in pos or not pos.get('last_buy_timestamp'):
                # Find last BUY from trade_log
                buys = buys_by_ticker.get(ticker, [])
                if buys:
                    last_buy = buys[-1]  # Most recent
                    pos['last_buy_timestamp'] = last_buy.get('timestamp')
                    logger.info(f"Migrated {ticker}: last_buy_timestamp from trade_log")
                else:
                    # Fail-safe: if we truly don't know, set to NOW so we DON'T violate 24h
                    pos['last_buy_timestamp'] = datetime.now(timezone.utc).isoformat()
                    pos['last_buy_timestamp_inferred'] = True
                    logger.warning(f"Migrated {ticker}: last_buy_timestamp inferred (set to now for safety)")
                changed = True

            # 2. Migrate entry_timestamp if missing
            if 'entry_timestamp' not in pos or not pos.get('entry_timestamp'):
                buys = buys_by_ticker.get(ticker, [])
                if buys:
                    first_buy = buys[0]  # Oldest
                    pos['entry_timestamp'] = first_buy.get('timestamp')
                else:
                    pos['entry_timestamp'] = pos.get('last_buy_timestamp')
                changed = True

            # 3. Migrate to lot-based structure if lots are missing
            if 'lots' not in pos or not pos.get('lots'):
                buys = buys_by_ticker.get(ticker, [])
                shares = pos.get('shares', 0)

                if buys and shares > 0:
                    # Try to infer lots from trade_log
                    lots = []
                    for buy in buys:
                        ts = buy.get('timestamp')
                        qty = buy.get('shares', 0)
                        price = buy.get('price')

                        if ts and qty > 0:
                            lot = {
                                'lot_id': f"MIG_{len(lots)+1}",
                                'qty': qty,
                                'buy_ts_utc': ts,
                            }
                            if price:
                                lot['buy_price'] = price
                            lots.append(lot)

                    # Verify total matches position shares
                    lot_total = sum(lot.get('qty', 0) for lot in lots)

                    if lots and abs(lot_total - shares) < shares * 0.1:  # Within 10%
                        # Adjust last lot to match position shares exactly
                        if lot_total != shares and lots:
                            diff = shares - lot_total
                            lots[-1]['qty'] += diff
                        pos['lots'] = lots
                        logger.info(f"Migrated {ticker}: {len(lots)} lots from trade_log")
                    else:
                        # Create synthetic lot (conservative: blocks sells for 24h)
                        pos['lots'] = [{
                            'lot_id': 'MIGRATED',
                            'qty': shares,
                            'buy_ts_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                            'synthetic': True
                        }]
                        logger.warning(
                            f"Migrated {ticker}: synthetic lot created (conservative - blocks sells for 24h). "
                            f"Trade log total: {lot_total}, position shares: {shares}"
                        )
                elif shares > 0:
                    # No trade_log entries, create synthetic lot
                    pos['lots'] = [{
                        'lot_id': 'MIGRATED',
                        'qty': shares,
                        'buy_ts_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        'synthetic': True
                    }]
                    logger.warning(f"Migrated {ticker}: synthetic lot created (no trade_log entries)")

                changed = True

        if changed:
            self.save()
            logger.info("Position timestamp and lot migration complete")

    def save(self):
        """Save current state to disk with backup.

        Thread-safe: Uses file locking to prevent concurrent write corruption.
        """
        with _state_file_lock:
            try:
                # Create backup of existing state
                if os.path.exists(self.state_file):
                    shutil.copy(self.state_file, STATE_BACKUP_FILE)

                # Update timestamp
                self.state['last_updated'] = datetime.now().isoformat()

                # Write new state atomically (write to temp, then rename)
                temp_file = self.state_file + '.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(self.state, f, indent=2, default=str)

                # Atomic rename (on POSIX systems)
                os.replace(temp_file, self.state_file)

                logger.debug(f"State saved to {self.state_file}")

            except IOError as e:
                logger.error(f"Error saving state: {e}")
                # Clean up temp file if it exists
                temp_file = self.state_file + '.tmp'
                if os.path.exists(temp_file):
                    os.remove(temp_file)
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
        """
        Add or update a position with timestamp tracking AND lot-based tracking.

        CRITICAL: This now uses the lot-based system for proper FIFO 24h hold compliance.
        Each call creates a new lot, allowing proper per-lot tracking.

        Also maintains legacy fields (entry_timestamp, last_buy_timestamp) for
        backwards compatibility.
        """
        now_utc = self._utc_now_iso()
        if entry_date is None:
            entry_date = datetime.now().date().isoformat()

        if ticker in self.state['positions']:
            # Update existing position - use add_buy_lot for proper lot tracking
            existing = self.state['positions'][ticker]

            # Create new lot
            lot = {
                'lot_id': self._generate_lot_id(),
                'qty': shares,
                'buy_ts_utc': now_utc,
            }
            if price is not None:
                lot['buy_price'] = price

            if 'lots' not in existing:
                existing['lots'] = []
            existing['lots'].append(lot)

            # Update average cost
            old_shares = existing.get('shares', 0)
            old_cost = existing.get('entry_price', 0)
            new_total_shares = old_shares + shares
            if old_shares > 0 and old_cost > 0:
                new_avg_cost = ((old_shares * old_cost) + (shares * price)) / new_total_shares
            else:
                new_avg_cost = price

            existing['shares'] = new_total_shares
            existing['entry_price'] = new_avg_cost
            # Keep original entry_date and entry_timestamp
            # CRITICAL: Update last_buy_timestamp on EVERY buy for 24h enforcement
            existing['last_buy_timestamp'] = now_utc
            if bucket and not existing.get('bucket'):
                existing['bucket'] = bucket
        else:
            # New position with first lot
            lot = {
                'lot_id': self._generate_lot_id(),
                'qty': shares,
                'buy_ts_utc': now_utc,
            }
            if price is not None:
                lot['buy_price'] = price

            self.state['positions'][ticker] = {
                'ticker': ticker,
                'lots': [lot],
                'shares': shares,
                'entry_price': price,
                'entry_date': entry_date,
                'entry_timestamp': now_utc,  # First buy timestamp
                'last_buy_timestamp': now_utc,  # Same as entry for new position
                'bucket': bucket,
            }

        self.save()
        logger.info(f"Position added/updated: {ticker} - {shares} shares, lot created at {now_utc}")

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

    # =========================================================================
    # LOT-BASED POSITION TRACKING (24-HOUR HOLD COMPLIANCE)
    # =========================================================================
    def _generate_lot_id(self) -> str:
        """Generate a short unique lot ID."""
        return str(uuid.uuid4())[:8]

    def _utc_now_iso(self) -> str:
        """Get current UTC time in ISO format with Z suffix."""
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    def get_total_shares(self, ticker: str) -> int:
        """
        Get total shares for a ticker by summing all lots.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Total shares held across all lots
        """
        pos = self.state.get('positions', {}).get(ticker)
        if not pos:
            return 0

        lots = pos.get('lots', [])
        if lots:
            return sum(lot.get('qty', 0) for lot in lots)

        # Fallback to legacy shares field if no lots
        return pos.get('shares', 0)

    def add_buy_lot(self, ticker: str, qty: int, ts_utc: str = None,
                    price: float = None, bucket: str = None):
        """
        Add a new lot for a BUY transaction.

        Each BUY creates its own timestamped lot for proper 24h hold tracking.

        Args:
            ticker: Stock ticker symbol
            qty: Number of shares purchased
            ts_utc: Buy timestamp in ISO UTC format (defaults to now)
            price: Buy price per share (optional)
            bucket: Thematic bucket (optional)
        """
        if ts_utc is None:
            ts_utc = self._utc_now_iso()

        lot = {
            'lot_id': self._generate_lot_id(),
            'qty': qty,
            'buy_ts_utc': ts_utc,
        }
        if price is not None:
            lot['buy_price'] = price

        if ticker not in self.state['positions']:
            # New position
            self.state['positions'][ticker] = {
                'ticker': ticker,
                'lots': [lot],
                'shares': qty,  # Keep legacy field in sync
                'entry_price': price or 0,
                'entry_date': datetime.now().date().isoformat(),
                'entry_timestamp': ts_utc,
                'last_buy_timestamp': ts_utc,
                'bucket': bucket,
            }
        else:
            # Add to existing position
            pos = self.state['positions'][ticker]
            if 'lots' not in pos:
                pos['lots'] = []
            pos['lots'].append(lot)

            # Update derived fields
            pos['shares'] = self.get_total_shares(ticker)
            pos['last_buy_timestamp'] = ts_utc

            # Update average cost if price provided
            if price is not None:
                old_shares = pos.get('shares', 0) - qty
                old_cost = pos.get('entry_price', 0)
                if old_shares > 0 and old_cost > 0:
                    pos['entry_price'] = ((old_shares * old_cost) + (qty * price)) / pos['shares']
                else:
                    pos['entry_price'] = price

            if bucket and not pos.get('bucket'):
                pos['bucket'] = bucket

        self.save()
        logger.info(f"Added buy lot for {ticker}: {qty} shares at {ts_utc}")

    def eligible_sell_qty(self, ticker: str, now_utc: datetime = None) -> int:
        """
        Get number of shares eligible to sell based on 24h + buffer hold.

        A lot is eligible when: now_utc >= buy_ts_utc + MIN_HOLD_SECONDS + BUFFER

        Args:
            ticker: Stock ticker symbol
            now_utc: Current UTC time (defaults to now, useful for testing)

        Returns:
            Number of shares eligible to sell
        """
        from config import MIN_HOLD_SECONDS, HOLD_BUFFER_SECONDS

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        pos = self.state.get('positions', {}).get(ticker)
        if not pos:
            return 0

        lots = pos.get('lots', [])
        if not lots:
            # Legacy position without lots - use last_buy_timestamp
            ts_str = pos.get('last_buy_timestamp') or pos.get('entry_timestamp')
            if not ts_str:
                return 0  # Fail-closed: no timestamp = no sell

            buy_ts = self._parse_timestamp_utc(ts_str)
            if not buy_ts:
                return 0

            required_hold = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS
            if (now_utc - buy_ts).total_seconds() >= required_hold:
                return pos.get('shares', 0)
            return 0

        # Sum eligible lots
        required_hold = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS
        eligible = 0

        for lot in lots:
            buy_ts = self._parse_timestamp_utc(lot.get('buy_ts_utc', ''))
            if not buy_ts:
                continue  # Skip lots without valid timestamps

            if (now_utc - buy_ts).total_seconds() >= required_hold:
                eligible += lot.get('qty', 0)

        return eligible

    def earliest_eligible_time(self, ticker: str, now_utc: datetime = None) -> Optional[str]:
        """
        Get the earliest time when ineligible shares become eligible.

        Useful for logging: "Blocked: 0 eligible shares. Earliest eligible at 10:12:31 ET."

        Args:
            ticker: Stock ticker symbol
            now_utc: Current UTC time (defaults to now)

        Returns:
            ISO timestamp of earliest eligible time, or None if all eligible/no position
        """
        from config import MIN_HOLD_SECONDS, HOLD_BUFFER_SECONDS

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        pos = self.state.get('positions', {}).get(ticker)
        if not pos:
            return None

        lots = pos.get('lots', [])
        if not lots:
            # Legacy position without lots
            ts_str = pos.get('last_buy_timestamp') or pos.get('entry_timestamp')
            if not ts_str:
                return None

            buy_ts = self._parse_timestamp_utc(ts_str)
            if not buy_ts:
                return None

            required_hold = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS
            eligible_time = buy_ts + timedelta(seconds=required_hold)

            if eligible_time > now_utc:
                return eligible_time.isoformat().replace('+00:00', 'Z')
            return None

        # Find earliest eligibility among ineligible lots
        required_hold = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS
        earliest = None

        for lot in lots:
            buy_ts = self._parse_timestamp_utc(lot.get('buy_ts_utc', ''))
            if not buy_ts:
                continue

            eligible_time = buy_ts + timedelta(seconds=required_hold)

            # Only consider future eligibility times
            if eligible_time > now_utc:
                if earliest is None or eligible_time < earliest:
                    earliest = eligible_time

        if earliest:
            return earliest.isoformat().replace('+00:00', 'Z')
        return None

    def consume_sell_fifo(self, ticker: str, sell_qty: int, now_utc: datetime = None) -> bool:
        """
        Consume shares from eligible lots in FIFO order for a SELL.

        CRITICAL: This function will RAISE an exception if insufficient eligible
        shares are available. This is fail-closed for compliance.

        Args:
            ticker: Stock ticker symbol
            sell_qty: Number of shares to sell
            now_utc: Current UTC time (defaults to now)

        Returns:
            True if successful

        Raises:
            ValueError: If insufficient eligible shares
        """
        from config import MIN_HOLD_SECONDS, HOLD_BUFFER_SECONDS

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        pos = self.state.get('positions', {}).get(ticker)
        if not pos:
            raise ValueError(f"No position found for {ticker}")

        lots = pos.get('lots', [])
        if not lots:
            # Legacy position - check if can sell all
            eligible = self.eligible_sell_qty(ticker, now_utc)
            if eligible < sell_qty:
                raise ValueError(
                    f"Insufficient eligible shares for {ticker}: "
                    f"need {sell_qty}, have {eligible} eligible"
                )
            # For legacy positions, just update shares
            pos['shares'] = pos.get('shares', 0) - sell_qty
            if pos['shares'] <= 0:
                del self.state['positions'][ticker]
            self.save()
            return True

        # Check total eligible
        eligible = self.eligible_sell_qty(ticker, now_utc)
        if eligible < sell_qty:
            earliest = self.earliest_eligible_time(ticker, now_utc)
            raise ValueError(
                f"Insufficient eligible shares for {ticker}: "
                f"need {sell_qty}, have {eligible} eligible. "
                f"Earliest eligible: {earliest}"
            )

        # Sort lots by buy timestamp (oldest first = FIFO)
        required_hold = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS
        eligible_lots = []

        for i, lot in enumerate(lots):
            buy_ts = self._parse_timestamp_utc(lot.get('buy_ts_utc', ''))
            if buy_ts and (now_utc - buy_ts).total_seconds() >= required_hold:
                eligible_lots.append((buy_ts, i, lot))

        # Sort by timestamp (oldest first)
        eligible_lots.sort(key=lambda x: x[0])

        # Consume FIFO
        remaining_to_sell = sell_qty
        lots_to_remove = []

        for _, idx, lot in eligible_lots:
            if remaining_to_sell <= 0:
                break

            lot_qty = lot.get('qty', 0)
            if lot_qty <= remaining_to_sell:
                # Consume entire lot
                remaining_to_sell -= lot_qty
                lots_to_remove.append(idx)
            else:
                # Partial consumption
                lot['qty'] = lot_qty - remaining_to_sell
                remaining_to_sell = 0

        # Remove fully consumed lots (in reverse order to preserve indices)
        for idx in sorted(lots_to_remove, reverse=True):
            lots.pop(idx)

        # Update derived fields
        pos['lots'] = lots
        pos['shares'] = sum(lot.get('qty', 0) for lot in lots)

        # Remove position if no shares remain
        if pos['shares'] <= 0:
            del self.state['positions'][ticker]

        self.save()
        logger.info(f"Consumed {sell_qty} shares from {ticker} (FIFO), {pos.get('shares', 0)} remaining")
        return True

    def get_lots(self, ticker: str) -> List[Dict]:
        """
        Get all lots for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            List of lot dictionaries
        """
        pos = self.state.get('positions', {}).get(ticker)
        if not pos:
            return []
        return pos.get('lots', [])

    def has_any_recent_buy(self, ticker: str, now_utc: datetime = None) -> Tuple[bool, str]:
        """
        Check if there was ANY buy within the hold period (STRICT_TICKER mode).

        In STRICT_TICKER mode, if any lot is younger than the hold threshold,
        ALL sells are blocked for that ticker.

        Args:
            ticker: Stock ticker symbol
            now_utc: Current UTC time (defaults to now)

        Returns:
            Tuple of (has_recent_buy, reason_string)
        """
        from config import MIN_HOLD_SECONDS, HOLD_BUFFER_SECONDS

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        pos = self.state.get('positions', {}).get(ticker)
        if not pos:
            return False, "No position"

        lots = pos.get('lots', [])
        required_hold = MIN_HOLD_SECONDS + HOLD_BUFFER_SECONDS

        if not lots:
            # Legacy position - use last_buy_timestamp
            ts_str = pos.get('last_buy_timestamp') or pos.get('entry_timestamp')
            if not ts_str:
                return True, "No buy timestamp (fail-closed)"

            buy_ts = self._parse_timestamp_utc(ts_str)
            if not buy_ts:
                return True, "Unparseable buy timestamp"

            elapsed = (now_utc - buy_ts).total_seconds()
            if elapsed < required_hold:
                remaining_hours = (required_hold - elapsed) / 3600
                return True, f"Last buy {remaining_hours:.1f}h ago (need 24h + buffer)"
            return False, "All buys older than 24h + buffer"

        # Check all lots
        youngest_elapsed = float('inf')
        for lot in lots:
            buy_ts = self._parse_timestamp_utc(lot.get('buy_ts_utc', ''))
            if not buy_ts:
                return True, "Lot with no timestamp (fail-closed)"

            elapsed = (now_utc - buy_ts).total_seconds()
            youngest_elapsed = min(youngest_elapsed, elapsed)

            if elapsed < required_hold:
                remaining_hours = (required_hold - elapsed) / 3600
                return True, f"Recent buy {remaining_hours:.1f}h ago (need 24h + buffer)"

        return False, "All lots older than 24h + buffer"

    def log_trade(self, ticker: str, action: str, shares: int,
                  price: float, reason: str = ''):
        """Log a completed trade with UTC timestamp for compliance."""
        trade = {
            'timestamp': datetime.now(timezone.utc).isoformat(),  # UTC for consistency
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

    def write_dashboard_state(self, running: bool = False, mode: str = "IDLE",
                               step: str = None, error: str = None,
                               regime: str = "UNKNOWN", vix: float = None,
                               last_screenshot: str = None, run_id: str = None):
        """
        Write a dashboard-friendly state file for the UI to read.

        This is called frequently during bot execution to give real-time visibility.

        Args:
            running: Is the bot currently executing?
            mode: Current mode (TEST, DRY-RUN, LIVE, IDLE)
            step: Current step name (LOGIN, NAVIGATE, FILL_ORDER, etc.)
            error: Last error message if any
            regime: Current market regime (RISK-ON, RISK-OFF, etc.)
            vix: Current VIX value
            last_screenshot: Path to most recent screenshot
            run_id: Unique run identifier
        """
        positions = self.get_positions()
        positions_list = [
            {
                "ticker": ticker,
                "shares": pos.get("shares", 0),
                "entry_price": pos.get("entry_price", 0),
                "bucket": pos.get("bucket"),
            }
            for ticker, pos in positions.items()
        ]

        recent_trades = self.get_trade_log()[-20:] if self.get_trade_log() else []

        dashboard_state = {
            "running": running,
            "mode": mode,
            "step": step,
            "run_id": run_id,
            "last_update": datetime.now().isoformat(),
            "last_result": "OK" if not error else "FAIL",
            "error": error,
            "trades_used": self.get_trades_used(),
            "trades_remaining": self.get_trades_remaining(),
            "regime": regime,
            "vix": vix,
            "positions_count": len(positions),
            "positions": positions_list,
            "recent_trades": recent_trades,
            "last_screenshot": last_screenshot,
            "last_execution_date": self.state.get("last_execution_date"),
            "last_execution_time": self.state.get("last_execution_time"),
            "error_count": self.state.get("error_count", 0),
        }

        try:
            with open(DASHBOARD_STATE_FILE, 'w') as f:
                json.dump(dashboard_state, f, indent=2, default=str)
            logger.debug(f"Dashboard state written to {DASHBOARD_STATE_FILE}")
        except Exception as e:
            logger.warning(f"Could not write dashboard state: {e}")

    # =========================================================================
    # SPRINT3 STATE MANAGEMENT
    # =========================================================================
    def get_sprint3_state(self) -> Dict:
        """Get sprint3 state dict."""
        # Initialize sprint3 state if not present
        if 'sprint3' not in self.state:
            self.state['sprint3'] = {
                'mode': None,
                'sprint_day': 0,
                'trades_used_sprint': 0,
                'last_run_time': None,
                'last_run_day': None,
                'satellites_held': [],
                'last_result': None,
                'last_error': None,
                'last_screenshot': None,
            }
        return self.state['sprint3']

    def update_sprint3_state(self, **kwargs):
        """Update sprint3 state fields."""
        sprint3 = self.get_sprint3_state()
        sprint3.update(kwargs)
        self.save()

    def is_sprint3_active(self) -> bool:
        """Check if sprint3 mode is active."""
        return self.get_sprint3_state().get('mode') == 'SPRINT3'

    def start_sprint3(self):
        """Initialize sprint3 mode."""
        self.update_sprint3_state(
            mode='SPRINT3',
            sprint_day=0,
            trades_used_sprint=0,
            last_run_time=datetime.now().isoformat(),
            satellites_held=[],
            last_result=None,
            last_error=None
        )
        logger.info("Sprint3 mode initialized")

    def reset_sprint3(self):
        """Reset sprint3 state."""
        self.state['sprint3'] = {
            'mode': None,
            'sprint_day': 0,
            'trades_used_sprint': 0,
            'last_run_time': None,
            'last_run_day': None,
            'satellites_held': [],
            'last_result': None,
            'last_error': None,
            'last_screenshot': None,
        }
        self.save()
        logger.info("Sprint3 state reset")

    def get_sprint3_trades_remaining(self) -> int:
        """Get remaining trades in sprint3 budget."""
        sprint3 = self.get_sprint3_state()
        SPRINT3_TRADE_CAP = 65
        SPRINT3_BUFFER = 5

        # Cap is either 65 or remaining trades - buffer, whichever is smaller
        trades_remaining = self.get_trades_remaining()
        effective_cap = min(SPRINT3_TRADE_CAP, trades_remaining - SPRINT3_BUFFER)
        sprint_used = sprint3.get('trades_used_sprint', 0)

        return max(0, effective_cap - sprint_used)

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

        # Print sprint3 status if active
        sprint3 = self.get_sprint3_state()
        if sprint3.get('mode') == 'SPRINT3':
            print("\nSPRINT3 STATUS:")
            print(f"  Sprint Day: {sprint3.get('sprint_day', 0)}/3")
            print(f"  Sprint Trades Used: {sprint3.get('trades_used_sprint', 0)}")
            print(f"  Sprint Trades Remaining: {self.get_sprint3_trades_remaining()}")
            print(f"  Satellites: {len(sprint3.get('satellites_held', []))}")
            print(f"  Last Run: {sprint3.get('last_run_time')}")
            if sprint3.get('last_error'):
                print(f"  Last Error: {sprint3.get('last_error')}")

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

    IMPORTANT SAFEGUARDS:
    - If stocktrak_holdings is empty but we have local positions AND trades,
      this is likely a scraping failure - DO NOT wipe positions.
    - Only remove positions if we're confident the data is valid.

    Args:
        state_manager: StateManager instance
        stocktrak_holdings: Holdings from StockTrak
        stocktrak_trade_count: Trade count from StockTrak
    """
    logger.info("Synchronizing state with StockTrak...")

    local_positions = state_manager.get_positions()
    local_trades = state_manager.get_trades_used()

    # SAFEGUARD: Detect scraping failures
    # If we have trades and local positions but StockTrak returns empty,
    # this is almost certainly a scraping error, not actual empty holdings
    if not stocktrak_holdings and local_positions and local_trades > 0:
        logger.warning("=" * 60)
        logger.warning("SAFEGUARD TRIGGERED: StockTrak returned 0 holdings")
        logger.warning(f"  Local positions: {len(local_positions)} ({list(local_positions.keys())})")
        logger.warning(f"  Local trades used: {local_trades}")
        logger.warning("  This is likely a scraping failure - NOT wiping positions")
        logger.warning("=" * 60)
        # DO NOT sync - keep local state intact
        return

    # Update trade count if StockTrak shows different
    if stocktrak_trade_count != state_manager.get_trades_used():
        logger.warning(f"Trade count mismatch: local={state_manager.get_trades_used()}, "
                       f"StockTrak={stocktrak_trade_count}")
        state_manager.state['trades_used'] = stocktrak_trade_count
        state_manager.state['trades_remaining'] = MAX_TRADES_TOTAL - stocktrak_trade_count

    # Check for positions that exist in StockTrak but not locally
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

    # Check for positions in local state but not in StockTrak
    # SAFEGUARD: Only remove if stocktrak_holdings has SOME data
    # (if it's empty, we can't trust it)
    if stocktrak_holdings:  # Only remove if we got valid data from StockTrak
        for ticker in list(local_positions.keys()):
            if ticker not in stocktrak_holdings:
                logger.warning(f"Position {ticker} in local state but not in StockTrak - removing")
                state_manager.remove_position(ticker)
    else:
        logger.info("Skipping position removal check - StockTrak holdings empty (may be scraping issue)")

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
