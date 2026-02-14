"""
Queue Manager for StockTrak Bot

Manages and organizes the queued buy list on StockTrak:
- Scrapes pending/queued orders from Order History
- Detects and removes duplicate orders
- Validates order queue against bot state
- Provides comprehensive queue auditing

AUTOMATION GOAL: No manual queue management needed.
"""

import logging
import time
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

from config import (
    STOCKTRAK_ORDER_HISTORY_URL,
    get_bucket_for_ticker,
    SATELLITE_BUCKETS,
    PROHIBITED_TICKERS
)

logger = logging.getLogger('stocktrak_bot.queue_manager')


@dataclass
class QueuedOrder:
    """Represents a queued/pending order on StockTrak."""
    ticker: str
    side: str  # "BUY" or "SELL"
    shares: int
    order_type: str  # "MARKET", "LIMIT", etc.
    status: str  # "Open", "Pending", "Queued", etc.
    order_date: str
    order_time: str = ""
    limit_price: Optional[float] = None
    order_id: Optional[str] = None
    row_index: int = 0  # For UI interaction

    def __hash__(self):
        return hash((self.ticker, self.side, self.shares))

    def __eq__(self, other):
        if not isinstance(other, QueuedOrder):
            return False
        return (self.ticker == other.ticker and
                self.side == other.side and
                self.shares == other.shares)


@dataclass
class QueueAuditResult:
    """Results from a queue audit."""
    total_orders: int = 0
    buy_orders: int = 0
    sell_orders: int = 0
    duplicate_orders: List[QueuedOrder] = field(default_factory=list)
    invalid_orders: List[Tuple[QueuedOrder, str]] = field(default_factory=list)
    orders_by_ticker: Dict[str, List[QueuedOrder]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    is_healthy: bool = True


class QueueManager:
    """
    Manages the queued order list on StockTrak.

    Provides automated:
    - Queue scraping and parsing
    - Duplicate detection and removal
    - Order validation
    - Queue organization and cleanup
    """

    def __init__(self, bot, state_manager=None):
        """
        Initialize QueueManager.

        Args:
            bot: StockTrakBot instance (for page access)
            state_manager: Optional StateManager for cross-checking
        """
        self.bot = bot
        self.state = state_manager
        self._cached_orders = None
        self._cache_time = None
        self._cache_ttl = 30  # Cache for 30 seconds

    def get_pending_orders(self, force_refresh: bool = False) -> List[QueuedOrder]:
        """
        Scrape all pending/queued orders from StockTrak Order History.

        Args:
            force_refresh: Bypass cache and re-scrape

        Returns:
            List of QueuedOrder objects
        """
        # Check cache
        if not force_refresh and self._cached_orders is not None:
            if self._cache_time and (datetime.now() - self._cache_time).seconds < self._cache_ttl:
                logger.debug("Using cached order queue")
                return self._cached_orders

        logger.info("Scraping pending orders from Order History...")
        orders = []

        try:
            # Navigate to Order History
            self.bot.page.goto(STOCKTRAK_ORDER_HISTORY_URL, timeout=30000)
            self.bot.page.wait_for_load_state("networkidle")

            # Dismiss any overlays
            from stocktrak_bot import dismiss_stocktrak_overlays
            dismiss_stocktrak_overlays(self.bot.page, total_ms=5000)

            time.sleep(1)

            # Find the orders table
            # StockTrak typically has a table with columns:
            # Symbol, Action, Quantity, Order Type, Price, Status, Date, etc.
            table = self.bot.page.locator("table").first
            if not table.is_visible(timeout=5000):
                logger.warning("Order History table not found")
                return orders

            # Get all rows (skip header)
            rows = self.bot.page.locator("table tbody tr")
            row_count = rows.count()
            logger.info(f"Found {row_count} rows in Order History")

            for i in range(row_count):
                try:
                    row = rows.nth(i)
                    row_text = row.text_content() or ""

                    # Skip if row is empty or just headers
                    if not row_text.strip() or "Symbol" in row_text:
                        continue

                    # Parse the row - look for pending/open orders
                    order = self._parse_order_row(row, row_text, i)
                    if order and order.status.lower() in ['open', 'pending', 'queued', 'working']:
                        orders.append(order)
                        logger.debug(f"Found queued order: {order.side} {order.shares} {order.ticker}")

                except Exception as e:
                    logger.debug(f"Error parsing row {i}: {e}")
                    continue

            # Update cache
            self._cached_orders = orders
            self._cache_time = datetime.now()

            logger.info(f"Found {len(orders)} pending/queued orders")
            return orders

        except Exception as e:
            logger.error(f"Error scraping pending orders: {e}")
            from stocktrak_bot import take_debug_screenshot
            take_debug_screenshot(self.bot.page, 'queue_scrape_error')
            return orders

    def _parse_order_row(self, row, row_text: str, row_index: int) -> Optional[QueuedOrder]:
        """
        Parse a single order row from the Order History table.

        Args:
            row: Playwright locator for the row
            row_text: Full text content of the row
            row_index: Index of the row in the table

        Returns:
            QueuedOrder if parseable, None otherwise
        """
        try:
            cells = row.locator("td")
            cell_count = cells.count()

            if cell_count < 4:
                return None

            # Try to get cell values
            # Typical order: Symbol, Action, Qty, Type, Price, Status, Date
            cell_values = []
            for j in range(cell_count):
                try:
                    cell_values.append(cells.nth(j).text_content().strip())
                except:
                    cell_values.append("")

            # Find ticker (usually first cell, all caps 1-5 chars)
            ticker = None
            side = None
            shares = None
            status = None
            order_type = "MARKET"
            order_date = ""
            limit_price = None

            for val in cell_values:
                val_upper = val.upper()

                # Detect ticker
                if re.match(r'^[A-Z]{1,5}$', val_upper) and not ticker:
                    ticker = val_upper
                    continue

                # Detect side
                if val_upper in ['BUY', 'SELL'] and not side:
                    side = val_upper
                    continue

                # Detect shares (numeric)
                if re.match(r'^\d+$', val) and not shares:
                    shares = int(val)
                    continue

                # Detect status
                if val_upper in ['OPEN', 'PENDING', 'QUEUED', 'WORKING', 'FILLED', 'CANCELLED', 'REJECTED', 'EXECUTED']:
                    status = val_upper
                    continue

                # Detect order type
                if val_upper in ['MARKET', 'LIMIT', 'STOP', 'STOP LIMIT']:
                    order_type = val_upper
                    continue

                # Detect date (MM/DD/YYYY or YYYY-MM-DD)
                if re.match(r'\d{1,2}/\d{1,2}/\d{2,4}', val) or re.match(r'\d{4}-\d{2}-\d{2}', val):
                    order_date = val
                    continue

                # Detect price (with $ or decimal)
                if re.match(r'\$?[\d,]+\.?\d*$', val.replace(',', '')):
                    try:
                        limit_price = float(val.replace('$', '').replace(',', ''))
                    except:
                        pass

            # Validate we got required fields
            if not ticker or not side or not shares:
                return None

            # Default status if not found
            if not status:
                # Check row text for status keywords
                row_upper = row_text.upper()
                if 'PENDING' in row_upper or 'OPEN' in row_upper or 'QUEUED' in row_upper:
                    status = 'OPEN'
                elif 'FILLED' in row_upper or 'EXECUTED' in row_upper:
                    status = 'FILLED'
                elif 'CANCEL' in row_upper:
                    status = 'CANCELLED'
                else:
                    status = 'UNKNOWN'

            return QueuedOrder(
                ticker=ticker,
                side=side,
                shares=shares,
                order_type=order_type,
                status=status,
                order_date=order_date,
                limit_price=limit_price,
                row_index=row_index
            )

        except Exception as e:
            logger.debug(f"Error parsing order row: {e}")
            return None

    def find_duplicates(self, orders: List[QueuedOrder] = None) -> List[List[QueuedOrder]]:
        """
        Find duplicate orders in the queue.

        Two orders are duplicates if they have the same:
        - Ticker
        - Side (BUY/SELL)
        - Shares

        Args:
            orders: List of orders to check (fetches if not provided)

        Returns:
            List of duplicate groups (each group has 2+ identical orders)
        """
        if orders is None:
            orders = self.get_pending_orders()

        # Group by (ticker, side, shares)
        groups = {}
        for order in orders:
            key = (order.ticker, order.side, order.shares)
            if key not in groups:
                groups[key] = []
            groups[key].append(order)

        # Return only groups with duplicates (2+)
        duplicates = [group for group in groups.values() if len(group) > 1]

        if duplicates:
            logger.warning(f"Found {len(duplicates)} duplicate order groups")
            for group in duplicates:
                logger.warning(f"  Duplicate: {group[0].side} {group[0].shares} {group[0].ticker} x{len(group)}")

        return duplicates

    def find_invalid_orders(self, orders: List[QueuedOrder] = None) -> List[Tuple[QueuedOrder, str]]:
        """
        Find invalid orders in the queue.

        Invalid orders include:
        - Prohibited tickers (leveraged ETFs, crypto, etc.)
        - Tickers not in allowed buckets
        - Orders that violate trading rules

        Args:
            orders: List of orders to check (fetches if not provided)

        Returns:
            List of (order, reason) tuples
        """
        if orders is None:
            orders = self.get_pending_orders()

        invalid = []

        for order in orders:
            # Check prohibited tickers
            if order.ticker in PROHIBITED_TICKERS:
                invalid.append((order, f"Prohibited ticker: {order.ticker}"))
                continue

            # Check if ticker is in any bucket (for BUY orders)
            if order.side == "BUY":
                bucket = get_bucket_for_ticker(order.ticker)
                if not bucket:
                    # Not in buckets - might be a core position or watchlist
                    from config import CORE_POSITIONS, WATCHLIST_ALL
                    if order.ticker not in CORE_POSITIONS and order.ticker not in WATCHLIST_ALL:
                        invalid.append((order, f"Ticker not in allowed universe: {order.ticker}"))
                        continue

            # Check for unreasonable share counts
            if order.shares <= 0:
                invalid.append((order, f"Invalid share count: {order.shares}"))
                continue

            if order.shares > 10000:
                invalid.append((order, f"Suspiciously high share count: {order.shares}"))
                continue

        if invalid:
            logger.warning(f"Found {len(invalid)} invalid orders")
            for order, reason in invalid:
                logger.warning(f"  Invalid: {order.side} {order.shares} {order.ticker} - {reason}")

        return invalid

    def cancel_order(self, order: QueuedOrder) -> Tuple[bool, str]:
        """
        Cancel a specific queued order.

        Args:
            order: The QueuedOrder to cancel

        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Attempting to cancel order: {order.side} {order.shares} {order.ticker}")

        try:
            # Navigate to Order History if not already there
            if 'orderhistory' not in self.bot.page.url.lower():
                self.bot.page.goto(STOCKTRAK_ORDER_HISTORY_URL, timeout=30000)
                self.bot.page.wait_for_load_state("networkidle")

                from stocktrak_bot import dismiss_stocktrak_overlays
                dismiss_stocktrak_overlays(self.bot.page, total_ms=3000)
                time.sleep(1)

            # Find the row matching this order
            rows = self.bot.page.locator("table tbody tr")
            row_count = rows.count()

            for i in range(row_count):
                row = rows.nth(i)
                row_text = row.text_content() or ""

                # Check if this row matches our order
                if (order.ticker in row_text.upper() and
                    order.side in row_text.upper() and
                    str(order.shares) in row_text):

                    # Look for Cancel button/link in this row
                    cancel_btn = row.locator("button, a").filter(has_text=re.compile("cancel", re.I))

                    if cancel_btn.count() > 0:
                        cancel_btn.first.click()
                        time.sleep(1)

                        # Handle confirmation dialog if present
                        try:
                            confirm_btn = self.bot.page.locator("button").filter(
                                has_text=re.compile("yes|confirm|ok", re.I)
                            )
                            if confirm_btn.count() > 0 and confirm_btn.first.is_visible(timeout=3000):
                                confirm_btn.first.click()
                                time.sleep(1)
                        except:
                            pass

                        # Verify cancellation
                        self.bot.page.reload()
                        self.bot.page.wait_for_load_state("networkidle")
                        time.sleep(1)

                        # Check if order is gone or status changed to cancelled
                        new_rows = self.bot.page.locator("table tbody tr")
                        for j in range(new_rows.count()):
                            new_row_text = new_rows.nth(j).text_content() or ""
                            if (order.ticker in new_row_text.upper() and
                                order.side in new_row_text.upper() and
                                str(order.shares) in new_row_text):
                                if 'CANCEL' in new_row_text.upper():
                                    logger.info(f"Order cancelled successfully: {order.ticker}")
                                    return True, "Order cancelled"
                                else:
                                    # Order still exists and not cancelled
                                    return False, "Order still pending after cancel attempt"

                        # Order not found - probably cancelled
                        logger.info(f"Order cancelled (no longer in list): {order.ticker}")
                        return True, "Order cancelled (removed from list)"

                    else:
                        return False, "No cancel button found for order"

            return False, "Order not found in Order History"

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            from stocktrak_bot import take_debug_screenshot
            take_debug_screenshot(self.bot.page, f'cancel_order_error_{order.ticker}')
            return False, f"Error: {e}"

    def cancel_duplicates(self) -> Tuple[int, List[str]]:
        """
        Find and cancel all duplicate orders, keeping only one of each.

        Returns:
            Tuple of (num_cancelled, list of messages)
        """
        duplicates = self.find_duplicates()
        if not duplicates:
            logger.info("No duplicate orders to cancel")
            return 0, ["No duplicates found"]

        cancelled = 0
        messages = []

        for group in duplicates:
            # Keep the first one, cancel the rest
            to_cancel = group[1:]  # Skip the first order

            for order in to_cancel:
                success, msg = self.cancel_order(order)
                if success:
                    cancelled += 1
                    messages.append(f"Cancelled duplicate: {order.side} {order.shares} {order.ticker}")
                else:
                    messages.append(f"Failed to cancel: {order.side} {order.shares} {order.ticker} - {msg}")

            # Small delay between cancellations
            time.sleep(1)

        # Clear cache after modifications
        self._cached_orders = None

        logger.info(f"Cancelled {cancelled} duplicate orders")
        return cancelled, messages

    def audit_queue(self) -> QueueAuditResult:
        """
        Perform a comprehensive audit of the order queue.

        Checks for:
        - Total pending orders
        - Duplicate orders
        - Invalid orders
        - Orders by ticker
        - Potential issues

        Returns:
            QueueAuditResult with detailed findings
        """
        logger.info("=" * 60)
        logger.info("QUEUE AUDIT STARTING")
        logger.info("=" * 60)

        result = QueueAuditResult()

        # Get all pending orders
        orders = self.get_pending_orders(force_refresh=True)
        result.total_orders = len(orders)

        # Count by side
        result.buy_orders = sum(1 for o in orders if o.side == "BUY")
        result.sell_orders = sum(1 for o in orders if o.side == "SELL")

        # Group by ticker
        for order in orders:
            if order.ticker not in result.orders_by_ticker:
                result.orders_by_ticker[order.ticker] = []
            result.orders_by_ticker[order.ticker].append(order)

        # Find duplicates
        duplicate_groups = self.find_duplicates(orders)
        for group in duplicate_groups:
            result.duplicate_orders.extend(group[1:])  # All but first are duplicates

        if result.duplicate_orders:
            result.warnings.append(f"Found {len(result.duplicate_orders)} duplicate orders")
            result.is_healthy = False

        # Find invalid orders
        result.invalid_orders = self.find_invalid_orders(orders)
        if result.invalid_orders:
            result.warnings.append(f"Found {len(result.invalid_orders)} invalid orders")
            result.is_healthy = False

        # Check for multiple orders of same ticker
        for ticker, ticker_orders in result.orders_by_ticker.items():
            if len(ticker_orders) > 1:
                result.warnings.append(
                    f"Multiple orders for {ticker}: {len(ticker_orders)} orders"
                )

        # Cross-check with bot state if available
        if self.state:
            positions = self.state.get_positions()

            # Check if any BUY orders are for tickers we already hold
            for order in orders:
                if order.side == "BUY" and order.ticker in positions:
                    existing = positions[order.ticker]
                    result.warnings.append(
                        f"BUY order for {order.ticker} but already holding {existing.get('shares', 0)} shares"
                    )

            # Check if any SELL orders are for tickers we don't hold
            for order in orders:
                if order.side == "SELL" and order.ticker not in positions:
                    result.warnings.append(
                        f"SELL order for {order.ticker} but no position found in state"
                    )

        # Log results
        logger.info(f"Total pending orders: {result.total_orders}")
        logger.info(f"  BUY orders: {result.buy_orders}")
        logger.info(f"  SELL orders: {result.sell_orders}")
        logger.info(f"  Unique tickers: {len(result.orders_by_ticker)}")
        logger.info(f"  Duplicate orders: {len(result.duplicate_orders)}")
        logger.info(f"  Invalid orders: {len(result.invalid_orders)}")
        logger.info(f"Queue healthy: {result.is_healthy}")

        if result.warnings:
            logger.warning("Warnings:")
            for warning in result.warnings:
                logger.warning(f"  - {warning}")

        logger.info("=" * 60)
        logger.info("QUEUE AUDIT COMPLETE")
        logger.info("=" * 60)

        return result

    def organize_queue(self, auto_cancel_duplicates: bool = True,
                       auto_cancel_invalid: bool = False) -> Tuple[bool, QueueAuditResult]:
        """
        Organize and clean up the order queue.

        This is the main automation entry point. It:
        1. Audits the queue
        2. Optionally cancels duplicate orders
        3. Optionally cancels invalid orders
        4. Returns the cleaned-up state

        Args:
            auto_cancel_duplicates: If True, cancel duplicate orders
            auto_cancel_invalid: If True, cancel invalid orders (use with caution)

        Returns:
            Tuple of (success, QueueAuditResult)
        """
        logger.info("=" * 60)
        logger.info("ORGANIZING ORDER QUEUE")
        logger.info("=" * 60)

        # First, audit
        audit = self.audit_queue()

        if audit.is_healthy and not audit.duplicate_orders and not audit.invalid_orders:
            logger.info("Queue is healthy - no cleanup needed")
            return True, audit

        # Cancel duplicates if requested
        if auto_cancel_duplicates and audit.duplicate_orders:
            logger.info(f"Cancelling {len(audit.duplicate_orders)} duplicate orders...")
            cancelled, messages = self.cancel_duplicates()
            for msg in messages:
                logger.info(f"  {msg}")

        # Cancel invalid if requested (more dangerous)
        if auto_cancel_invalid and audit.invalid_orders:
            logger.info(f"Cancelling {len(audit.invalid_orders)} invalid orders...")
            for order, reason in audit.invalid_orders:
                success, msg = self.cancel_order(order)
                if success:
                    logger.info(f"  Cancelled invalid: {order.ticker} ({reason})")
                else:
                    logger.warning(f"  Failed to cancel invalid: {order.ticker} - {msg}")
                time.sleep(1)

        # Re-audit after cleanup
        final_audit = self.audit_queue()

        logger.info("=" * 60)
        logger.info("QUEUE ORGANIZATION COMPLETE")
        logger.info(f"Final state: {final_audit.total_orders} orders, healthy={final_audit.is_healthy}")
        logger.info("=" * 60)

        return final_audit.is_healthy, final_audit

    def print_queue_summary(self, orders: List[QueuedOrder] = None):
        """
        Print a formatted summary of the order queue.

        Args:
            orders: List of orders (fetches if not provided)
        """
        if orders is None:
            orders = self.get_pending_orders()

        print("\n" + "=" * 60)
        print("PENDING ORDER QUEUE")
        print("=" * 60)
        print(f"Total orders: {len(orders)}")
        print("-" * 60)

        if not orders:
            print("  (No pending orders)")
        else:
            # Sort by ticker
            sorted_orders = sorted(orders, key=lambda o: (o.ticker, o.side))

            for order in sorted_orders:
                price_str = f"@ ${order.limit_price:.2f}" if order.limit_price else "(MARKET)"
                print(f"  {order.side:4} {order.shares:5} {order.ticker:5} {order.order_type:6} {price_str} [{order.status}]")

        print("=" * 60)

        # Print warnings
        duplicates = self.find_duplicates(orders)
        if duplicates:
            print("\nWARNINGS:")
            for group in duplicates:
                print(f"  DUPLICATE: {group[0].side} {group[0].shares} {group[0].ticker} appears {len(group)} times")

        invalid = self.find_invalid_orders(orders)
        if invalid:
            for order, reason in invalid:
                print(f"  INVALID: {order.ticker} - {reason}")

        print()


def run_queue_audit(bot, state_manager=None) -> QueueAuditResult:
    """
    Convenience function to run a queue audit.

    Args:
        bot: StockTrakBot instance
        state_manager: Optional StateManager

    Returns:
        QueueAuditResult
    """
    manager = QueueManager(bot, state_manager)
    return manager.audit_queue()


def organize_order_queue(bot, state_manager=None,
                         cancel_duplicates: bool = True) -> Tuple[bool, QueueAuditResult]:
    """
    Convenience function to organize the order queue.

    Args:
        bot: StockTrakBot instance
        state_manager: Optional StateManager
        cancel_duplicates: If True, automatically cancel duplicate orders

    Returns:
        Tuple of (success, QueueAuditResult)
    """
    manager = QueueManager(bot, state_manager)
    return manager.organize_queue(auto_cancel_duplicates=cancel_duplicates)
