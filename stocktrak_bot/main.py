#!/usr/bin/env python3
"""
StockTrak Automated Trading Bot
Morgan Stanley UWT Milgard Competition 2026
TEAM 9

This bot automates trading on app.stocktrak.com according to a rules-based
algorithmic strategy. It runs continuously from January 20 to February 20, 2026.

Usage:
    python main.py              # Start scheduler (normal operation)
    python main.py --test       # Test login and data fetch
    python main.py --day1       # Execute Day-1 portfolio build
    python main.py --manual     # Manual execution of daily routine
    python main.py --status     # Show current bot status
    python main.py --scores     # Show satellite scoring report
    python main.py --preflight  # Test trade flow UI without executing
    python main.py --queue      # Manage pending order queue (dedupe, validate)
    python main.py --queue-auto # Auto-clean duplicate orders

Competition Rules Summary:
    - Max 80 trades total
    - Max 25% in single position
    - Min 4 holdings at all times
    - No stocks below $5
    - T+2 holding period
    - No leveraged/inverse/crypto ETFs
"""

import argparse
import logging
import sys
import os
from datetime import datetime

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def setup_logging(log_level='INFO'):
    """Configure logging for the bot."""
    os.makedirs('logs', exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/trading_bot.log'),
            logging.StreamHandler()
        ]
    )

    # Trade-specific logger
    trade_logger = logging.getLogger('trades')
    trade_handler = logging.FileHandler('logs/trades.log')
    trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    trade_logger.addHandler(trade_handler)

    return logging.getLogger('stocktrak_bot')


def print_banner():
    """Print startup banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║          STOCKTRAK AUTOMATED TRADING BOT - TEAM 9             ║
    ║      Morgan Stanley UWT Milgard Competition 2026              ║
    ╠═══════════════════════════════════════════════════════════════╣
    ║  Competition: January 20 - February 20, 2026                  ║
    ║  Trade Limit: 80 trades maximum                               ║
    ║  Strategy: Core/Satellite with Momentum Scoring               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def test_mode():
    """Test login and data fetching."""
    from stocktrak_bot import StockTrakBot
    from market_data import MarketDataCollector, print_market_summary

    logger = logging.getLogger('stocktrak_bot')
    logger.info("TEST MODE - Verifying bot functionality")

    print("\n--- Testing StockTrak Login ---")
    bot = StockTrakBot()
    bot.start_browser(headless=False)

    try:
        if bot.login():
            print("✓ Login SUCCESS")

            # Use robust KPI-based capital reading
            try:
                portfolio, cash, buying_power = bot.get_capital_from_trade_kpis("VOO")
                print(f"✓ Portfolio Value: ${portfolio:,.2f}")
                print(f"✓ Cash Balance: ${cash:,.2f}")
                print(f"✓ Buying Power: ${buying_power:,.2f}")
            except Exception as e:
                print(f"✗ Could not fetch capital: {e}")

            holdings = bot.get_current_holdings()
            print(f"✓ Holdings: {list(holdings.keys()) if holdings else 'None'}")

            trades = bot.get_transaction_count()
            print(f"✓ Trade Count: {trades}")
        else:
            print("✗ Login FAILED - check credentials and screenshots")
            return

    finally:
        bot.close()

    print("\n--- Testing Market Data ---")
    collector = MarketDataCollector()

    # Test VIX
    data = collector.get_all_data(['VOO', 'VTI', 'VEA', 'SMH'])
    print_market_summary(data)

    print("\n--- Test Complete ---")
    print("If all checks passed, the bot is ready for operation.")


def day1_mode():
    """Execute Day-1 portfolio build."""
    from daily_routine import execute_day1_build

    print("\n" + "!" * 60)
    print("DAY-1 PORTFOLIO BUILD")
    print("This will execute 11 trades to build the initial portfolio.")
    print("!" * 60)

    confirm = input("\nType 'BUILD' to confirm: ")
    if confirm != 'BUILD':
        print("Cancelled.")
        return

    execute_day1_build()


def manual_mode():
    """Manual execution of daily routine."""
    from daily_routine import execute_daily_routine

    print("\n--- Manual Execution Mode ---")
    confirm = input("Execute daily routine now? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return

    execute_daily_routine()


def status_mode():
    """Show current bot status."""
    from state_manager import StateManager

    state = StateManager()
    state.print_status()


def scores_mode():
    """Show satellite scoring report."""
    from market_data import MarketDataCollector
    from scoring import print_scoring_report
    from state_manager import StateManager

    print("\n--- Fetching Market Data ---")
    collector = MarketDataCollector()
    data = collector.get_all_data()

    state = StateManager()
    positions = state.get_positions()

    print_scoring_report(data, positions)


def preflight_mode():
    """
    UI Preflight Check - Test trade flow without executing.

    This tests the full trade flow UI to catch button/selector issues early:
    1. Login
    2. Navigate to trade page
    3. Fill form (1 share of VOO - minimal test)
    4. Click Review/Preview
    5. Verify "Confirm Order" button is found
    6. DO NOT click Confirm - just verify it exists

    This helps detect StockTrak UI changes before they cause real trade failures.
    """
    from stocktrak_bot import StockTrakBot, take_debug_screenshot

    logger = logging.getLogger('stocktrak_bot')
    logger.info("PREFLIGHT MODE - Testing trade flow UI without executing")

    print("\n" + "=" * 60)
    print("UI PREFLIGHT CHECK")
    print("Testing trade flow UI without executing any trades")
    print("=" * 60)

    bot = StockTrakBot()
    bot.start_browser(headless=False)  # Show browser for preflight

    results = {
        'login': False,
        'navigate': False,
        'fill_form': False,
        'preview': False,
        'confirm_button_found': False
    }

    try:
        # Step 1: Login
        print("\n[1/5] Testing login...")
        if bot.login():
            print("✓ Login SUCCESS")
            results['login'] = True
        else:
            print("✗ Login FAILED")
            return results

        # Step 2: Navigate to trade page
        print("\n[2/5] Testing trade page navigation...")
        trade_url = f"{bot.base_url}/trading/equitiesaliases"
        try:
            bot.page.goto(trade_url, wait_until="domcontentloaded", timeout=30000)
            import time
            time.sleep(2)
            take_debug_screenshot(bot.page, 'preflight_trade_page')
            print(f"✓ Navigated to trade page: {trade_url}")
            results['navigate'] = True
        except Exception as e:
            print(f"✗ Navigation failed: {e}")
            return results

        # Step 3: Fill form (1 share of VOO as test)
        print("\n[3/5] Testing form fill...")
        try:
            # Ticker
            ticker_input = bot.page.locator('input[name="symbol"], input[id="symbol"], input[placeholder*="ticker" i], input[placeholder*="symbol" i]').first
            ticker_input.fill("VOO")
            time.sleep(0.5)

            # Quantity (1 share - minimal)
            qty_input = bot.page.locator('input[name="quantity"], input[id="quantity"], input[placeholder*="quantity" i], input[placeholder*="shares" i]').first
            qty_input.fill("1")
            time.sleep(0.5)

            take_debug_screenshot(bot.page, 'preflight_form_filled')
            print("✓ Form filled (VOO, 1 share)")
            results['fill_form'] = True
        except Exception as e:
            print(f"✗ Form fill failed: {e}")
            return results

        # Step 4: Click Preview/Review
        print("\n[4/5] Testing preview button...")
        try:
            # Try to find and click Review Order button
            preview_js = """
            (function() {
                const buttons = document.querySelectorAll('button, input[type="submit"], a');
                for (const btn of buttons) {
                    const text = btn.textContent.toLowerCase();
                    if (text.includes('review') || text.includes('preview')) {
                        btn.scrollIntoView({behavior: 'instant', block: 'center'});
                        btn.click();
                        return {success: true, text: btn.textContent.trim()};
                    }
                }
                return {success: false, error: 'No preview button found'};
            })();
            """
            result = bot.page.evaluate(preview_js)
            if result.get('success'):
                print(f"✓ Preview button clicked: '{result.get('text')}'")
                time.sleep(3)  # Wait for preview to load
                take_debug_screenshot(bot.page, 'preflight_preview')
                results['preview'] = True
            else:
                print(f"✗ Preview failed: {result.get('error')}")
                return results
        except Exception as e:
            print(f"✗ Preview failed: {e}")
            return results

        # Step 5: Verify Confirm Order button exists (DO NOT CLICK!)
        print("\n[5/5] Verifying Confirm Order button exists...")
        try:
            confirm_js = """
            (function() {
                const clickables = document.querySelectorAll('button, a, [role="button"], input[type="submit"]');
                for (const el of clickables) {
                    const text = el.textContent.toLowerCase();
                    if (text.includes('confirm') && (text.includes('order') || text.includes('trade'))) {
                        return {
                            found: true,
                            text: el.textContent.trim(),
                            tag: el.tagName,
                            visible: el.offsetParent !== null
                        };
                    }
                }
                return {found: false};
            })();
            """
            result = bot.page.evaluate(confirm_js)
            if result.get('found'):
                print(f"✓ Confirm button FOUND: '{result.get('text')}' ({result.get('tag')})")
                print(f"  Visible: {result.get('visible')}")
                results['confirm_button_found'] = True
            else:
                print("✗ Confirm button NOT FOUND - this would cause trade failures!")
                take_debug_screenshot(bot.page, 'preflight_no_confirm')
        except Exception as e:
            print(f"✗ Confirm check failed: {e}")

        # Summary
        print("\n" + "=" * 60)
        print("PREFLIGHT RESULTS")
        print("=" * 60)
        all_passed = all(results.values())
        for check, passed in results.items():
            status = "✓" if passed else "✗"
            print(f"  {status} {check}")

        if all_passed:
            print("\n✓ ALL CHECKS PASSED - Trade flow UI is working!")
            print("  The bot should be able to execute trades successfully.")
        else:
            print("\n✗ SOME CHECKS FAILED - Trade flow may have issues!")
            print("  Review screenshots in screenshots/ directory for debugging.")

        return results

    except Exception as e:
        print(f"\n✗ Preflight error: {e}")
        import traceback
        traceback.print_exc()
        return results

    finally:
        print("\nClosing browser (no trades were executed)...")
        bot.close()


def sprint3_mode(dry_run: bool = False, force_day: int = None):
    """
    Execute SPRINT3 trading mode.

    Sprint3 is a high-intensity 3-day trading strategy for end-of-competition catch-up:
    - Day 1: Build core positions (60%) + 16 satellite positions (40%)
    - Day 2: Rotate all 16 satellites
    - Day 3: Rotate remaining budget trades

    Args:
        dry_run: If True, print planned trades without executing
        force_day: Force a specific sprint day (1, 2, or 3)
    """
    from sprint3_strategy import (
        Sprint3Executor, plan_sprint3, print_sprint3_plan, print_sprint3_scoring_report,
        is_market_open, is_in_execution_window, SPRINT3_SATELLITE_UNIVERSE, SPRINT3_CORE
    )
    from market_data import MarketDataCollector
    from stocktrak_bot import StockTrakBot
    from state_manager import StateManager

    logger = logging.getLogger('stocktrak_bot')

    print("\n" + "!" * 70)
    print("SPRINT3 MODE - High Intensity End-of-Competition Trading")
    print("!" * 70)

    state = StateManager()

    # Check market status
    market_open, market_reason = is_market_open()
    in_window, window_reason = is_in_execution_window()

    print(f"\nMarket: {market_reason}")
    print(f"Execution Window: {window_reason}")

    if not market_open and not dry_run:
        print("\nERROR: Market is closed. Sprint3 must be run during market hours (3:55-4:00 PM ET).")
        print("Use --sprint3-dry-run to test without executing trades.")
        return

    if not in_window and not dry_run:
        print("\nWARNING: Outside optimal execution window (3:55-4:00 PM ET).")
        print("Trading outside this window may cause 24h hold violations.")
        confirm = input("Continue anyway? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Cancelled.")
            return

    if dry_run:
        print("\n--- DRY RUN MODE ---")
        print("Will print planned trades without executing.\n")

        # Fetch market data
        collector = MarketDataCollector()
        all_tickers = list(SPRINT3_CORE.keys()) + SPRINT3_SATELLITE_UNIVERSE
        market_data = collector.get_all_data(all_tickers)

        # Print scoring report
        print_sprint3_scoring_report(market_data)

        # Plan for each day
        positions = state.get_positions()
        sprint_state = state.get_sprint3_state()
        current_day = sprint_state.get('sprint_day', 0)

        plan_day = force_day or (current_day + 1 if current_day < 3 else 1)
        plan = plan_sprint3(market_data, positions, sprint_day=plan_day)
        print_sprint3_plan(plan)

        return

    # Initialize sprint if not already active
    if not state.is_sprint3_active():
        print("\nInitializing SPRINT3 mode...")
        state.start_sprint3()

    # Confirm execution
    sprint_state = state.get_sprint3_state()
    next_day = force_day or (sprint_state.get('sprint_day', 0) + 1)

    if next_day > 3:
        print("\nSPRINT3 already complete (all 3 days executed).")
        print("Use --sprint3-reset to start a new sprint.")
        return

    print(f"\nAbout to execute SPRINT3 Day {next_day}")
    print(f"Trades used: {state.get_trades_used()}/80")
    print(f"Sprint trades remaining: {state.get_sprint3_trades_remaining()}")

    confirm = input(f"\nType 'SPRINT{next_day}' to execute: ")
    if confirm != f'SPRINT{next_day}':
        print("Cancelled.")
        return

    # Execute
    bot = None
    try:
        bot = StockTrakBot()
        bot.start_browser(headless=False)  # Show browser for sprint

        if not bot.login():
            raise Exception("Login failed")

        executor = Sprint3Executor(bot, state, dry_run=False)
        result = executor.execute_sprint_day(force_day=force_day)

        print("\n" + "=" * 70)
        if result['success']:
            print(f"SPRINT3 DAY {next_day} COMPLETED!")
            print(f"Trades executed: {result.get('trades_executed', 0)}")
        else:
            print(f"SPRINT3 DAY {next_day} FAILED!")
            print(f"Error: {result.get('error')}")
        print("=" * 70)

        # Print updated status
        executor.print_status()

    except Exception as e:
        logger.critical(f"SPRINT3 ERROR: {e}")
        import traceback
        traceback.print_exc()
        state.update_sprint3_state(last_error=str(e))

    finally:
        if bot:
            input("\nPress Enter to close browser...")
            bot.close()


def sprint3_status_mode():
    """Show current SPRINT3 status."""
    from sprint3_strategy import is_market_open, is_in_execution_window
    from state_manager import StateManager

    state = StateManager()
    sprint3 = state.get_sprint3_state()

    print("\n" + "=" * 70)
    print("SPRINT3 STATUS")
    print("=" * 70)

    # Mode status
    if sprint3.get('mode') == 'SPRINT3':
        print(f"Mode:              SPRINT3 (ACTIVE)")
    else:
        print(f"Mode:              Not active")

    print(f"Sprint Day:        {sprint3.get('sprint_day', 0)}/3")
    print(f"Sprint Trades Used: {sprint3.get('trades_used_sprint', 0)}")
    print(f"Sprint Remaining:  {state.get_sprint3_trades_remaining()}")
    print(f"Last Run:          {sprint3.get('last_run_time') or 'Never'}")
    print(f"Last Run Day:      {sprint3.get('last_run_day') or 'Never'}")

    print("-" * 70)
    print(f"Total Trades Used: {state.get_trades_used()}/80")
    print(f"Total Remaining:   {state.get_trades_remaining()}")

    # Market status
    market_open, market_reason = is_market_open()
    in_window, window_reason = is_in_execution_window()

    print("-" * 70)
    print(f"Market:            {market_reason}")
    print(f"Execution Window:  {window_reason}")

    # Satellites
    satellites = sprint3.get('satellites_held', [])
    print("-" * 70)
    print(f"Satellites Held:   {len(satellites)}")
    if satellites:
        print(f"  {', '.join(satellites)}")

    # Last error
    if sprint3.get('last_error'):
        print("-" * 70)
        print(f"Last Error:        {sprint3.get('last_error')}")

    print("=" * 70)


def sprint3_reset_mode():
    """Reset SPRINT3 state."""
    from state_manager import StateManager

    state = StateManager()

    print("\n--- SPRINT3 RESET ---")
    print("This will reset all sprint3 state (day counter, satellites, etc.)")
    print("Trade count will NOT be reset (use StockTrak admin for that).")

    confirm = input("\nType 'RESET' to confirm: ")
    if confirm != 'RESET':
        print("Cancelled.")
        return

    state.reset_sprint3()
    print("Sprint3 state reset successfully.")


def queue_mode(audit_only: bool = False, cancel_duplicates: bool = False):
    """
    Queue management mode - audit and organize pending orders.

    This automates queue management:
    - Scrapes pending orders from StockTrak Order History
    - Detects duplicate orders
    - Detects invalid orders
    - Optionally cancels duplicates

    Args:
        audit_only: If True, only audit (don't cancel anything)
        cancel_duplicates: If True, automatically cancel duplicate orders
    """
    from stocktrak_bot import StockTrakBot
    from state_manager import StateManager
    from queue_manager import QueueManager

    logger = logging.getLogger('stocktrak_bot')
    logger.info("QUEUE MODE - Managing pending orders")

    print("\n" + "=" * 60)
    print("QUEUE MANAGEMENT")
    print("=" * 60)

    if audit_only:
        print("Mode: AUDIT ONLY (no changes will be made)")
    else:
        print("Mode: ORGANIZE (will clean up duplicates)")
    print("=" * 60)

    state = StateManager()
    bot = None

    try:
        # Start browser and login
        print("\n[1/3] Starting browser and logging in...")
        bot = StockTrakBot()
        bot.start_browser(headless=False)  # Show browser for queue management

        if not bot.login():
            print("ERROR: Login failed")
            return

        print("Login successful")

        # Create queue manager
        print("\n[2/3] Fetching pending orders...")
        queue_manager = QueueManager(bot, state)

        if audit_only:
            # Just audit
            audit_result = queue_manager.audit_queue()
            queue_manager.print_queue_summary()

            print("\n" + "=" * 60)
            print("AUDIT COMPLETE")
            print("=" * 60)
            print(f"Total orders: {audit_result.total_orders}")
            print(f"BUY orders: {audit_result.buy_orders}")
            print(f"SELL orders: {audit_result.sell_orders}")
            print(f"Duplicates: {len(audit_result.duplicate_orders)}")
            print(f"Invalid: {len(audit_result.invalid_orders)}")
            print(f"Queue healthy: {audit_result.is_healthy}")

            if audit_result.warnings:
                print("\nWarnings:")
                for warning in audit_result.warnings:
                    print(f"  - {warning}")
        else:
            # Organize (audit + cleanup)
            print("\n[3/3] Organizing queue...")

            if not cancel_duplicates:
                # Ask for confirmation
                orders = queue_manager.get_pending_orders()
                queue_manager.print_queue_summary(orders)

                duplicates = queue_manager.find_duplicates(orders)
                if duplicates:
                    print(f"\nFound {len(duplicates)} duplicate groups to clean up.")
                    confirm = input("Cancel duplicates? (yes/no): ")
                    if confirm.lower() != 'yes':
                        print("Cancelled. No changes made.")
                        return
                    cancel_duplicates = True
                else:
                    print("\nNo duplicates found. Queue is clean.")
                    return

            # Execute cleanup
            success, audit_result = queue_manager.organize_queue(
                auto_cancel_duplicates=cancel_duplicates,
                auto_cancel_invalid=False  # Never auto-cancel invalid (too dangerous)
            )

            queue_manager.print_queue_summary()

            print("\n" + "=" * 60)
            print("ORGANIZATION COMPLETE")
            print("=" * 60)
            print(f"Final state: {audit_result.total_orders} pending orders")
            print(f"Queue healthy: {audit_result.is_healthy}")

            if audit_result.duplicate_orders:
                print(f"Duplicates cancelled: {len(audit_result.duplicate_orders)}")

    except Exception as e:
        logger.error(f"Queue management error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        if bot:
            input("\nPress Enter to close browser...")
            bot.close()
        print("Done.")


def hold_test_mode():
    """
    Test lot-based 24-hour holding compliance (offline, no Playwright).

    This test verifies:
    1. Lot creation with timestamps
    2. Eligible quantity calculation
    3. FIFO consumption on partial sells
    4. Sell blocking for insufficient eligible shares
    5. Both LOT_FIFO and STRICT_TICKER modes

    Returns exit code 0 on PASS, 1 on FAIL.
    """
    import sys
    import os
    import tempfile
    from datetime import datetime, timezone, timedelta
    from state_manager import StateManager
    from validators import can_sell_with_lots
    from config import MIN_HOLD_SECONDS, HOLD_BUFFER_SECONDS, HOLD_MODE

    print("\n" + "=" * 70)
    print("LOT-BASED 24-HOUR HOLDING COMPLIANCE TEST")
    print("=" * 70)
    print(f"HOLD_MODE: {HOLD_MODE}")
    print(f"MIN_HOLD_SECONDS: {MIN_HOLD_SECONDS} ({MIN_HOLD_SECONDS / 3600:.1f} hours)")
    print(f"HOLD_BUFFER_SECONDS: {HOLD_BUFFER_SECONDS} ({HOLD_BUFFER_SECONDS / 60:.1f} minutes)")
    print("=" * 70)

    # Use temporary state file for testing
    test_state_file = tempfile.mktemp(suffix='_hold_test.json')
    tests_passed = 0
    tests_failed = 0

    try:
        # Initialize fresh state manager with temp file
        state = StateManager(state_file=test_state_file)

        # =========================================================================
        # TEST 1: Create position with 2 lots (one 23h old, one 25h old)
        # =========================================================================
        print("\n--- TEST 1: Create position with 2 lots ---")
        ticker = "TEST_LOT"
        now = datetime.now(timezone.utc)

        # Lot 1: 25 hours old (should be eligible)
        lot1_time = now - timedelta(hours=25)
        lot1_ts = lot1_time.isoformat().replace('+00:00', 'Z')

        # Lot 2: 23 hours old (should NOT be eligible)
        lot2_time = now - timedelta(hours=23)
        lot2_ts = lot2_time.isoformat().replace('+00:00', 'Z')

        # Create position manually for precise control
        state.state['positions'][ticker] = {
            'ticker': ticker,
            'lots': [
                {'lot_id': 'LOT1', 'qty': 100, 'buy_ts_utc': lot1_ts, 'buy_price': 50.00},
                {'lot_id': 'LOT2', 'qty': 50, 'buy_ts_utc': lot2_ts, 'buy_price': 51.00},
            ],
            'shares': 150,
            'entry_price': 50.33,
            'entry_date': now.date().isoformat(),
            'entry_timestamp': lot1_ts,
            'last_buy_timestamp': lot2_ts,
            'bucket': 'TEST',
        }
        state.save()

        total = state.get_total_shares(ticker)
        if total == 150:
            print(f"  PASS: get_total_shares() = {total}")
            tests_passed += 1
        else:
            print(f"  FAIL: get_total_shares() = {total}, expected 150")
            tests_failed += 1

        # =========================================================================
        # TEST 2: Verify eligible quantity (LOT_FIFO mode)
        # =========================================================================
        print("\n--- TEST 2: Verify eligible quantity ---")
        eligible = state.eligible_sell_qty(ticker, now)
        # Only lot1 (100 shares) should be eligible (25h old > 24h + 5min)
        if eligible == 100:
            print(f"  PASS: eligible_sell_qty() = {eligible} (lot1 only)")
            tests_passed += 1
        else:
            print(f"  FAIL: eligible_sell_qty() = {eligible}, expected 100")
            tests_failed += 1

        # =========================================================================
        # TEST 3: Verify can_sell_with_lots returns correct values
        # =========================================================================
        print("\n--- TEST 3: Verify can_sell_with_lots() ---")
        can_sell, allowed, reason = can_sell_with_lots(ticker, 150, state, now)

        if HOLD_MODE == "LOT_FIFO":
            # Should allow partial (100 of 150)
            if can_sell and allowed == 100:
                print(f"  PASS: can_sell={can_sell}, allowed={allowed}")
                print(f"        Reason: {reason}")
                tests_passed += 1
            else:
                print(f"  FAIL: can_sell={can_sell}, allowed={allowed}, expected True/100")
                tests_failed += 1
        else:  # STRICT_TICKER
            # Should block all (lot2 is too young)
            if not can_sell and allowed == 0:
                print(f"  PASS: can_sell={can_sell}, allowed={allowed} (blocked)")
                print(f"        Reason: {reason}")
                tests_passed += 1
            else:
                print(f"  FAIL: can_sell={can_sell}, allowed={allowed}, expected False/0")
                tests_failed += 1

        # =========================================================================
        # TEST 4: Verify sell of more than eligible is blocked (LOT_FIFO)
        # =========================================================================
        print("\n--- TEST 4: Verify oversell is handled correctly ---")
        if HOLD_MODE == "LOT_FIFO":
            can_sell, allowed, reason = can_sell_with_lots(ticker, 120, state, now)
            # Should reduce to 100 (eligible amount)
            if can_sell and allowed == 100:
                print(f"  PASS: Requested 120, allowed {allowed} (reduced)")
                tests_passed += 1
            else:
                print(f"  FAIL: can_sell={can_sell}, allowed={allowed}, expected True/100")
                tests_failed += 1
        else:
            print("  SKIP: Not applicable in STRICT_TICKER mode")
            tests_passed += 1

        # =========================================================================
        # TEST 5: Verify consume_sell_fifo works correctly
        # =========================================================================
        print("\n--- TEST 5: Verify FIFO consumption ---")
        if HOLD_MODE == "LOT_FIFO":
            # Sell 60 shares (should come from lot1)
            try:
                state.consume_sell_fifo(ticker, 60, now)

                # Check lots after consumption
                lots = state.get_lots(ticker)
                remaining_shares = state.get_total_shares(ticker)

                if remaining_shares == 90:  # 150 - 60 = 90
                    print(f"  PASS: Total shares after sell = {remaining_shares}")
                    tests_passed += 1
                else:
                    print(f"  FAIL: Total shares = {remaining_shares}, expected 90")
                    tests_failed += 1

                # Lot1 should have 40 remaining (100 - 60)
                lot1 = next((l for l in lots if l['lot_id'] == 'LOT1'), None)
                if lot1 and lot1['qty'] == 40:
                    print(f"  PASS: Lot1 (oldest) reduced to {lot1['qty']} shares (FIFO)")
                    tests_passed += 1
                else:
                    print(f"  FAIL: Lot1 qty = {lot1['qty'] if lot1 else 'missing'}, expected 40")
                    tests_failed += 1

                # Lot2 should be unchanged (50)
                lot2 = next((l for l in lots if l['lot_id'] == 'LOT2'), None)
                if lot2 and lot2['qty'] == 50:
                    print(f"  PASS: Lot2 (newer) unchanged at {lot2['qty']} shares")
                    tests_passed += 1
                else:
                    print(f"  FAIL: Lot2 qty = {lot2['qty'] if lot2 else 'missing'}, expected 50")
                    tests_failed += 1

            except ValueError as e:
                print(f"  FAIL: consume_sell_fifo raised: {e}")
                tests_failed += 1
        else:
            print("  SKIP: Not applicable in STRICT_TICKER mode")
            tests_passed += 3

        # =========================================================================
        # TEST 6: Verify sell blocked when insufficient eligible (LOT_FIFO)
        # =========================================================================
        print("\n--- TEST 6: Verify insufficient eligible blocking ---")
        # Remaining: lot1=40 (eligible), lot2=50 (not eligible) = 40 eligible
        eligible_now = state.eligible_sell_qty(ticker, now)
        print(f"  Current eligible: {eligible_now} shares")

        try:
            # Try to sell 50 when only 40 eligible
            state.consume_sell_fifo(ticker, 50, now)
            print(f"  FAIL: Should have raised ValueError for insufficient eligible")
            tests_failed += 1
        except ValueError as e:
            print(f"  PASS: Correctly raised ValueError: {str(e)[:60]}...")
            tests_passed += 1

        # =========================================================================
        # TEST 7: Verify earliest_eligible_time
        # =========================================================================
        print("\n--- TEST 7: Verify earliest_eligible_time ---")
        earliest = state.earliest_eligible_time(ticker, now)
        if earliest:
            print(f"  PASS: earliest_eligible_time = {earliest}")
            tests_passed += 1
        else:
            print(f"  FAIL: earliest_eligible_time returned None")
            tests_failed += 1

        # =========================================================================
        # TEST 8: Verify has_any_recent_buy (STRICT_TICKER helper)
        # =========================================================================
        print("\n--- TEST 8: Verify has_any_recent_buy ---")
        has_recent, reason = state.has_any_recent_buy(ticker, now)
        if has_recent:
            print(f"  PASS: has_any_recent_buy = True (lot2 is young)")
            print(f"        Reason: {reason}")
            tests_passed += 1
        else:
            print(f"  FAIL: has_any_recent_buy = False, expected True")
            tests_failed += 1

        # =========================================================================
        # SUMMARY
        # =========================================================================
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)
        print(f"PASSED: {tests_passed}")
        print(f"FAILED: {tests_failed}")
        print("=" * 70)

        if tests_failed == 0:
            print("\n*** ALL TESTS PASSED ***\n")
            return 0
        else:
            print(f"\n*** {tests_failed} TEST(S) FAILED ***\n")
            return 1

    except Exception as e:
        print(f"\n*** TEST ERROR: {e} ***")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # Cleanup temp file
        if os.path.exists(test_state_file):
            os.remove(test_state_file)
        backup = test_state_file.replace('.json', '_backup.json')
        if os.path.exists(backup):
            os.remove(backup)


def scheduler_mode():
    """Start the continuous scheduler."""
    from scheduler import run_with_auto_restart
    from config import COMPETITION_START, COMPETITION_END

    logger = logging.getLogger('stocktrak_bot')

    print("\n--- SCHEDULER MODE ---")
    print(f"Competition Period: {COMPETITION_START} to {COMPETITION_END}")
    print("The bot will execute daily at 9:30 AM ET on trading days.")
    print("Press Ctrl+C to stop.\n")

    logger.info("Starting scheduler...")
    run_with_auto_restart()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='StockTrak Trading Bot - Team 9',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py --test           Test login and data fetching
    python main.py --day1           Build initial portfolio (Day 1 only)
    python main.py --manual         Run daily routine manually
    python main.py --status         Show current bot status
    python main.py --scores         Show satellite scoring report
    python main.py --preflight      Test trade flow UI without executing
    python main.py --queue          Audit and clean up pending order queue
    python main.py --queue-audit    Audit pending orders (no changes)
    python main.py --queue-auto     Auto-cancel duplicate orders (no confirmation)
    python main.py --sprint3        Execute SPRINT3 mode (3-day high-intensity trading)
    python main.py --sprint3-status Show SPRINT3 status
    python main.py --sprint3-dry-run Plan SPRINT3 trades without executing
    python main.py                  Start continuous scheduler
        """
    )

    parser.add_argument('--test', action='store_true',
                        help='Test mode - verify login and data')
    parser.add_argument('--day1', action='store_true',
                        help='Execute Day-1 portfolio build')
    parser.add_argument('--manual', action='store_true',
                        help='Manual execution of daily routine')
    parser.add_argument('--status', action='store_true',
                        help='Show current bot status')
    parser.add_argument('--scores', action='store_true',
                        help='Show satellite scoring report')
    parser.add_argument('--preflight', action='store_true',
                        help='UI preflight check - test trade flow without executing')
    parser.add_argument('--hold-test', action='store_true',
                        help='Test lot-based 24h holding compliance (offline, no Playwright)')

    # SPRINT3 options
    parser.add_argument('--sprint3', action='store_true',
                        help='Execute SPRINT3 mode (3-day high-intensity trading)')
    parser.add_argument('--sprint3-status', action='store_true',
                        help='Show SPRINT3 status')
    parser.add_argument('--sprint3-dry-run', action='store_true',
                        help='Plan SPRINT3 trades without executing')
    parser.add_argument('--sprint3-reset', action='store_true',
                        help='Reset SPRINT3 state')
    parser.add_argument('--sprint3-day', type=int, choices=[1, 2, 3],
                        help='Force specific sprint day (1, 2, or 3)')

    # Queue management options
    parser.add_argument('--queue', action='store_true',
                        help='Queue management - audit and clean up pending orders')
    parser.add_argument('--queue-audit', action='store_true',
                        help='Audit pending orders without making changes')
    parser.add_argument('--queue-auto', action='store_true',
                        help='Automatically clean up duplicate orders (no confirmation)')

    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Logging level (default: INFO)')

    args = parser.parse_args()

    # Setup logging
    logger = setup_logging(args.log_level)

    # Print banner
    print_banner()

    logger.info("=" * 60)
    logger.info("STOCKTRAK BOT - TEAM 9")
    logger.info(f"Started: {datetime.now()}")
    logger.info(f"Mode: {get_mode_name(args)}")
    logger.info("=" * 60)

    # Execute appropriate mode
    try:
        if args.test:
            test_mode()
        elif args.day1:
            day1_mode()
        elif args.manual:
            manual_mode()
        elif args.status:
            status_mode()
        elif args.scores:
            scores_mode()
        elif args.preflight:
            preflight_mode()
        elif args.hold_test:
            exit_code = hold_test_mode()
            sys.exit(exit_code)
        elif args.sprint3:
            sprint3_mode(dry_run=False, force_day=args.sprint3_day)
        elif args.sprint3_status:
            sprint3_status_mode()
        elif args.sprint3_dry_run:
            sprint3_mode(dry_run=True, force_day=args.sprint3_day)
        elif args.sprint3_reset:
            sprint3_reset_mode()
        elif args.queue:
            queue_mode(audit_only=False, cancel_duplicates=False)
        elif args.queue_audit:
            queue_mode(audit_only=True, cancel_duplicates=False)
        elif args.queue_auto:
            queue_mode(audit_only=False, cancel_duplicates=True)
        else:
            scheduler_mode()

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\nBot stopped.")

    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        import traceback
        logger.critical(traceback.format_exc())
        sys.exit(1)


def get_mode_name(args):
    """Get human-readable mode name."""
    if args.test:
        return "TEST"
    elif args.day1:
        return "DAY-1 BUILD"
    elif args.manual:
        return "MANUAL"
    elif args.status:
        return "STATUS"
    elif args.scores:
        return "SCORES"
    elif args.preflight:
        return "PREFLIGHT"
    elif args.hold_test:
        return "HOLD-TEST"
    elif args.sprint3:
        return "SPRINT3"
    elif args.sprint3_status:
        return "SPRINT3-STATUS"
    elif args.sprint3_dry_run:
        return "SPRINT3-DRY-RUN"
    elif args.sprint3_reset:
        return "SPRINT3-RESET"
    elif args.queue:
        return "QUEUE"
    elif args.queue_audit:
        return "QUEUE-AUDIT"
    elif args.queue_auto:
        return "QUEUE-AUTO"
    else:
        return "SCHEDULER"


if __name__ == "__main__":
    main()
