#!/usr/bin/env python3
"""
SPRINT MODE ACTIVATION SCRIPT
=============================
Run this to immediately deploy the aggressive final-week strategy.

What it does:
1. Validates SPRINT_MODE_ENABLED is True in config
2. Shows current portfolio status
3. Identifies cash that should be deployed
4. Shows top momentum candidates for immediate buys
5. Optionally executes trades (with confirmation)

Usage:
  python activate_sprint.py           # Show plan only
  python activate_sprint.py --execute # Execute trades (with confirmation)
  python activate_sprint.py --dry-run # Test execution without placing orders
"""

import argparse
import logging
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('activate_sprint')


def main():
    parser = argparse.ArgumentParser(description='Activate SPRINT mode for final week')
    parser.add_argument('--execute', action='store_true', help='Execute trades (with confirmation)')
    parser.add_argument('--dry-run', action='store_true', help='Test execution without placing orders')
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("SPRINT MODE ACTIVATION")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Check config
    from config import SPRINT_MODE_ENABLED, REGIME_PARAMS, MAX_PER_BUCKET

    print(f"\n[CONFIG CHECK]")
    print(f"  SPRINT_MODE_ENABLED: {SPRINT_MODE_ENABLED}")
    print(f"  MAX_PER_BUCKET: {MAX_PER_BUCKET}")
    print(f"  NORMAL max_satellites: {REGIME_PARAMS['NORMAL']['max_satellites']}")
    print(f"  NORMAL stop_loss_pct: {REGIME_PARAMS['NORMAL']['stop_loss_pct']}")
    print(f"  NORMAL weekly_replacement_cap: {REGIME_PARAMS['NORMAL']['weekly_replacement_cap']}")

    if not SPRINT_MODE_ENABLED:
        print("\n[ERROR] SPRINT_MODE_ENABLED is False in config.py")
        print("Set SPRINT_MODE_ENABLED = True and re-run")
        sys.exit(1)

    print("\n[OK] Sprint mode is ENABLED")

    # Load state
    from state_manager import StateManager
    state = StateManager()

    trades_used = state.get_trades_used()
    trades_remaining = state.get_trades_remaining()
    positions = state.get_positions()

    print(f"\n[PORTFOLIO STATUS]")
    print(f"  Trades used: {trades_used}/80")
    print(f"  Trades remaining: {trades_remaining}")
    print(f"  Current positions: {len(positions)}")

    # Get market data
    print("\n[FETCHING MARKET DATA]...")
    from market_data import MarketDataCollector
    collector = MarketDataCollector()
    market_data = collector.get_all_data()

    if not market_data.get('VOO'):
        print("[ERROR] Could not fetch market data")
        sys.exit(1)

    vix = market_data.get('vix', 0)
    print(f"  VIX: {vix:.2f}")

    # Score all candidates
    from scoring import score_all_satellites, get_best_per_bucket, count_bucket_positions
    from config import SATELLITE_BUCKETS, CORE_POSITIONS

    all_candidates = score_all_satellites(market_data)
    best_per_bucket = get_best_per_bucket(market_data, require_qualified=True)

    # Count current allocation
    core_tickers = list(CORE_POSITIONS.keys())
    satellite_tickers = [t for t in positions if t not in core_tickers]

    print(f"\n[CURRENT ALLOCATION]")
    print(f"  Core positions: {len([t for t in positions if t in core_tickers])}/3")
    print(f"  Satellite positions: {len(satellite_tickers)}")

    # Show bucket status
    print(f"\n[BUCKET STATUS]")
    for bucket in sorted(SATELLITE_BUCKETS.keys()):
        count = count_bucket_positions(bucket, positions)
        best = best_per_bucket.get(bucket)
        best_str = f"{best.ticker} (score={best.momentum_score:.4f})" if best else "N/A"
        status = "FILLED" if count >= MAX_PER_BUCKET else f"ROOM ({count}/{MAX_PER_BUCKET})"
        print(f"  {bucket}: {status} | Best candidate: {best_str}")

    # Find opportunities
    print(f"\n[TOP MOMENTUM CANDIDATES]")
    qualified = [c for c in all_candidates if c.is_qualified and c.ticker not in positions]
    qualified.sort(key=lambda x: -x.momentum_score)

    print(f"{'Rank':<5} {'Ticker':<8} {'Bucket':<12} {'MomScore':>10} {'RelR3':>10} {'RelR10':>10} {'Price':>10}")
    print("-" * 75)
    for i, c in enumerate(qualified[:20], 1):
        print(f"{i:<5} {c.ticker:<8} {c.bucket:<12} {c.momentum_score:>10.4f} {c.rel_r3:>10.4f} {c.rel_r10:>10.4f} {c.price:>10.2f}")

    # Calculate potential buys
    print(f"\n[RECOMMENDED ACTIONS]")

    buys_needed = []
    for bucket, best in best_per_bucket.items():
        if best.ticker in positions:
            continue
        count = count_bucket_positions(bucket, positions)
        if count < MAX_PER_BUCKET:
            buys_needed.append(best)

    buys_needed.sort(key=lambda x: -x.momentum_score)

    if buys_needed:
        print(f"  BUY {len(buys_needed)} satellites to fill buckets:")
        for c in buys_needed[:12]:  # Max 12 in NORMAL regime
            print(f"    - {c.ticker} ({c.bucket}): momentum_score={c.momentum_score:.4f}")
    else:
        print("  All buckets filled - consider rotating worst performers")

    # Execute?
    if args.execute or args.dry_run:
        print("\n" + "=" * 70)
        if args.dry_run:
            print("DRY RUN MODE - No actual trades will be placed")
        else:
            print("EXECUTE MODE - This will place real trades!")
        print("=" * 70)

        if not args.dry_run:
            confirm = input("\nType 'EXECUTE' to confirm: ")
            if confirm != 'EXECUTE':
                print("Aborted.")
                sys.exit(0)

        # Execute trades
        print("\n[EXECUTING TRADES]...")

        import config
        config.DRY_RUN_MODE = args.dry_run

        from stocktrak_bot import StockTrakBot
        from daily_routine import execute_trade_safely
        from utils import calculate_shares_for_allocation

        bot = StockTrakBot()
        bot.start_browser(headless=True)

        if not bot.login():
            print("[ERROR] Login failed")
            bot.close()
            sys.exit(1)

        # Get capital
        try:
            portfolio_value, cash, buying_power = bot.get_capital_from_trade_kpis("VOO")
            print(f"\n  Portfolio: ${portfolio_value:,.2f}")
            print(f"  Cash: ${cash:,.2f}")
            print(f"  Buying Power: ${buying_power:,.2f}")
        except Exception as e:
            print(f"[ERROR] Could not get capital: {e}")
            bot.close()
            sys.exit(1)

        # Execute buys
        from config import SATELLITE_POSITION_SIZE
        trades_executed = 0

        for candidate in buys_needed[:10]:  # Limit to 10 trades per run
            if trades_remaining - trades_executed < 5:
                print(f"  [STOP] Trade budget buffer reached")
                break

            shares = calculate_shares_for_allocation(
                portfolio_value, SATELLITE_POSITION_SIZE, candidate.price
            )

            if shares < 1:
                continue

            print(f"\n  Buying {candidate.ticker}: {shares} shares @ ~${candidate.price:.2f}")

            success, msg = execute_trade_safely(
                bot, state, candidate.ticker, "BUY", shares,
                rationale=f"SPRINT_ACTIVATION_SCORE_{candidate.momentum_score:.4f}",
                dry_run=args.dry_run,
                portfolio_pct=SATELLITE_POSITION_SIZE * 100
            )

            if success:
                print(f"  [OK] {msg}")
                state.add_position(candidate.ticker, shares, candidate.price, bucket=candidate.bucket)
                trades_executed += 1
            else:
                print(f"  [FAIL] {msg}")

            import time
            time.sleep(3)

        print(f"\n[COMPLETE] Executed {trades_executed} trades")
        bot.close()

    else:
        print("\n" + "-" * 70)
        print("To execute these trades, run:")
        print("  python activate_sprint.py --dry-run   # Test first")
        print("  python activate_sprint.py --execute   # Execute for real")
        print("-" * 70)


if __name__ == "__main__":
    main()
