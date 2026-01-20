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

            value = bot.get_portfolio_value()
            if value:
                print(f"✓ Portfolio Value: ${value:,.2f}")
            else:
                print("✗ Could not fetch portfolio value")

            holdings = bot.get_current_holdings()
            print(f"✓ Holdings: {list(holdings.keys()) if holdings else 'None'}")

            trades = bot.get_transaction_count()
            print(f"✓ Trade Count: {trades}")

            cash = bot.get_cash_balance()
            if cash:
                print(f"✓ Cash Balance: ${cash:,.2f}")
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


def scheduler_mode():
    """Start the continuous scheduler."""
    from scheduler import run_with_auto_restart
    from config import COMPETITION_START, COMPETITION_END

    logger = logging.getLogger('stocktrak_bot')

    print("\n--- SCHEDULER MODE ---")
    print(f"Competition Period: {COMPETITION_START} to {COMPETITION_END}")
    print("The bot will execute daily at 3:55 PM ET on trading days.")
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
    python main.py --test       Test login and data fetching
    python main.py --day1       Build initial portfolio (Day 1 only)
    python main.py --manual     Run daily routine manually
    python main.py --status     Show current bot status
    python main.py --scores     Show satellite scoring report
    python main.py              Start continuous scheduler
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
    else:
        return "SCHEDULER"


if __name__ == "__main__":
    main()
