"""
FINAL DAY BLITZ STRATEGY for StockTrak Bot
Morgan Stanley UWT Milgard Competition 2026 - TEAM 9

The last-day strategy for maximum profit:
1. Sell ALL eligible positions (held > 24h) to free up cash
2. Score an expanded universe of 100+ tickers for 1-day forward momentum
3. Redeploy into top picks for the final 24-hour hold (through competition end)

Key insight: Anything bought Thursday morning is held through Friday market close
(competition end). Feb 20 is monthly OpEx = higher volume + momentum potential.

Competition Constraints Enforced:
- Max 80 trades total (67 remaining)
- Max 25% per position (CRITICAL: enforced at purchase time)
- Min 4 holdings at all times
- BUY price >= $5 (use $6 safety buffer)
- 24-hour minimum hold (timestamp-based)
- No leveraged/inverse ETFs
- $5 commission per trade
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import pytz

from config import (
    CORE_POSITIONS, SATELLITE_BUCKETS, PROHIBITED_TICKERS,
    MAX_SINGLE_POSITION_PCT, MIN_HOLDINGS, MAX_TRADES_TOTAL,
    SAFETY_BUFFER_PRICE, COMMISSION_PER_TRADE,
    get_bucket_for_ticker, get_all_satellite_tickers
)
from state_manager import StateManager
from market_data import MarketDataCollector
from validators import is_prohibited, can_sell_with_lots
from utils import calculate_shares_for_allocation

logger = logging.getLogger('stocktrak_bot.final_day')

# =============================================================================
# FINAL DAY CONFIGURATION
# =============================================================================

# Expanded universe for final-day scanning
# Include all satellite tickers + watchlist + high-momentum large caps
FINAL_DAY_UNIVERSE = list(set(
    # Core ETFs (for benchmark and potential holds)
    ['VOO', 'VTI', 'VEA', 'QQQ', 'SPY', 'IWM', 'DIA'] +

    # All satellite ETFs
    ['SMH', 'SOXX', 'XLK', 'PPA', 'ITA', 'XAR',
     'XLE', 'XOP', 'COPX', 'XME', 'PICK',
     'URA', 'URNM', 'NLR', 'XBI', 'IDNA',
     'UFO', 'ROKT', 'XLB', 'VAW', 'DMAT'] +

    # All satellite stocks
    ['NVDA', 'AMD', 'AVGO', 'ASML', 'AMAT', 'LRCX', 'KLAC',
     'LMT', 'NOC', 'RTX', 'GD', 'KTOS', 'AVAV',
     'XOM', 'CVX', 'FCX', 'SCCO', 'CCJ',
     'CRSP', 'NTLA', 'BEAM', 'VRTX',
     'RKLB', 'ASTS', 'LUNR', 'RDW', 'PL'] +

    # High-momentum large caps (expanded universe for final day)
    ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA',
     'NFLX', 'CRM', 'ORCL', 'ADBE',
     'PLTR', 'COIN', 'HOOD', 'SQ', 'SHOP',
     'UBER', 'ABNB', 'DASH',
     'LLY', 'UNH', 'ABBV', 'MRK', 'JNJ',
     'JPM', 'GS', 'MS', 'V', 'MA',
     'CAT', 'DE', 'GE', 'HON',
     'BA', 'LMT', 'RTX',
     'COST', 'WMT', 'TGT', 'HD', 'LOW'] +

    # Sector ETFs for broad momentum capture
    ['XLF', 'XLI', 'XLY', 'XLC', 'XLP', 'XLU', 'XLV',
     'ARKK', 'ARKW', 'ARKG',
     'GDX', 'SLV', 'GLD', 'USO',
     'TLT', 'HYG', 'LQD',
     'EEM', 'EWJ', 'FXI', 'KWEB',
     'IBB', 'IGV', 'HACK', 'CIBR',
     'JETS', 'BLOK', 'BETZ'] +

    # Momentum / high-beta names
    ['MU', 'MRVL', 'ON', 'SMCI', 'ARM',
     'DDOG', 'SNOW', 'NET', 'CRWD', 'ZS', 'PANW',
     'NOW', 'TEAM', 'MDB', 'DKNG',
     'RDDT', 'VRT', 'STX', 'SYF',
     'IREN', 'NBIS']
))

# Position sizing for final day
FINAL_DAY_MAX_POSITIONS = 25       # Deploy across up to 25 positions
FINAL_DAY_CORE_PCT = 0.50         # Keep 50% in core (or sell if better options)
FINAL_DAY_SATELLITE_PCT = 0.04    # ~4% per satellite position
FINAL_DAY_MIN_PRICE = 6.00        # Safety buffer

# Execution window: wider for final day (full market hours)
FINAL_DAY_WINDOW_START = "09:35"
FINAL_DAY_WINDOW_END = "15:30"    # Allow trading until 3:30 PM

# Trade budget management
FINAL_DAY_RESERVE_TRADES = 2      # Reserve 2 trades for emergencies


# =============================================================================
# SCORING FOR 1-DAY FORWARD RETURNS
# =============================================================================

@dataclass
class FinalDayCandidate:
    """Scored candidate for final-day deployment."""
    ticker: str
    score: float
    r1: float           # 1-day return (yesterday)
    r3: float           # 3-day return
    r10: float          # 10-day return
    vol10: float        # 10-day volatility
    price: float
    sma20: float
    sma50: float
    volume_ratio: float  # Current volume vs 20-day average
    is_eligible: bool
    disqualify_reason: Optional[str] = None
    bucket: Optional[str] = None
    is_core: bool = False

    @property
    def is_etf(self) -> bool:
        return self.ticker in [
            'SMH', 'SOXX', 'XLK', 'PPA', 'ITA', 'XAR', 'XLE', 'XOP',
            'COPX', 'XME', 'PICK', 'URA', 'URNM', 'NLR', 'XBI', 'IDNA',
            'UFO', 'ROKT', 'XLB', 'VAW', 'DMAT', 'VOO', 'VTI', 'VEA',
            'QQQ', 'SPY', 'IWM', 'DIA', 'XLF', 'XLI', 'XLY', 'XLC',
            'XLP', 'XLU', 'XLV', 'ARKK', 'ARKW', 'ARKG', 'GDX', 'SLV',
            'GLD', 'USO', 'TLT', 'HYG', 'LQD', 'EEM', 'EWJ', 'FXI',
            'KWEB', 'IBB', 'IGV', 'HACK', 'CIBR', 'JETS', 'BLOK', 'BETZ'
        ]


def calculate_final_day_score(
    ticker_data: Dict,
    voo_data: Dict
) -> Optional[FinalDayCandidate]:
    """
    Calculate FinalDayScore for maximum 1-day forward return.

    Score = 0.50*r1 + 0.30*rr3 + 0.15*volume_signal - 0.20*vol10

    Where:
    - r1 = ticker's 1-day return (recent momentum = future momentum for 1 day)
    - rr3 = ticker's 3-day return relative to VOO (short-term alpha)
    - volume_signal = log(volume_ratio) capped at 0.1 (institutional interest)
    - vol10 = 10-day volatility penalty (avoid blowups)

    This score is tuned for 1-DAY holding period. Unlike sprint3's 3/10 day
    weighting, we emphasize the most recent price action (r1) since we only
    hold for ~24 hours.
    """
    if not ticker_data or not voo_data:
        return None

    ticker = ticker_data.get('ticker', 'UNKNOWN')
    price = ticker_data.get('price', 0)

    # Get returns
    r1 = ticker_data.get('return_1d', 0) or 0
    r3 = ticker_data.get('return_3d', 0) or 0
    r10 = ticker_data.get('return_10d', 0) or 0

    # VOO returns for relative calculation
    voo_r1 = voo_data.get('return_1d', 0) or 0
    voo_r3 = voo_data.get('return_3d', 0) or 0

    # Relative returns
    rr3 = r3 - voo_r3

    # Volume signal
    volume = ticker_data.get('volume', 0) or 0
    avg_volume = ticker_data.get('avg_volume_20d', 0) or 1
    volume_ratio = volume / avg_volume if avg_volume > 0 else 1.0

    import math
    volume_signal = min(math.log(max(volume_ratio, 0.1)), 0.10)

    # Volatility
    vol10 = ticker_data.get('vol10', None)
    if vol10 is None:
        vol10 = ticker_data.get('volatility_21d', 0.03) or 0.03

    # FinalDayScore: heavily weight recent momentum
    score = 0.50 * r1 + 0.30 * rr3 + 0.15 * volume_signal - 0.20 * vol10

    # Bonus for positive momentum across all timeframes (trend alignment)
    if r1 > 0 and r3 > 0 and r10 > 0:
        score += 0.005  # Small bonus for aligned momentum

    # SMAs for trend context
    sma20 = ticker_data.get('sma20', price) or price
    sma50 = ticker_data.get('sma50', 0) or 0

    # Eligibility
    is_eligible = True
    disqualify_reason = None

    if price < FINAL_DAY_MIN_PRICE:
        is_eligible = False
        disqualify_reason = f"Price ${price:.2f} < ${FINAL_DAY_MIN_PRICE}"

    if is_prohibited(ticker):
        is_eligible = False
        disqualify_reason = "Prohibited security"

    # For final day, we use a relaxed trend filter:
    # Just need price > SMA50 (don't require SMA20 > SMA50)
    # This allows more candidates for a 1-day hold
    if sma50 > 0 and price < sma50 * 0.95:
        # Only disqualify if significantly below SMA50 (5%+ below)
        is_eligible = False
        disqualify_reason = f"Price ${price:.2f} significantly below SMA50 ${sma50:.2f}"

    bucket = get_bucket_for_ticker(ticker)
    is_core = ticker in CORE_POSITIONS

    return FinalDayCandidate(
        ticker=ticker,
        score=score,
        r1=r1,
        r3=r3,
        r10=r10,
        vol10=vol10,
        price=price,
        sma20=sma20,
        sma50=sma50,
        volume_ratio=volume_ratio,
        is_eligible=is_eligible,
        disqualify_reason=disqualify_reason,
        bucket=bucket,
        is_core=is_core,
    )


def score_all_final_day(market_data: Dict) -> List[FinalDayCandidate]:
    """
    Score all candidates in the final-day universe.

    Returns candidates sorted by score (highest first).
    """
    voo_data = market_data.get('VOO')
    if not voo_data:
        logger.error("Cannot score candidates: VOO data missing")
        return []

    candidates = []

    for ticker in FINAL_DAY_UNIVERSE:
        ticker_data = market_data.get(ticker)
        if not ticker_data:
            continue

        ticker_data['ticker'] = ticker

        candidate = calculate_final_day_score(ticker_data, voo_data)
        if candidate:
            candidates.append(candidate)

    # Sort by score descending
    candidates.sort(key=lambda x: x.score, reverse=True)

    return candidates


def get_top_final_day_candidates(
    market_data: Dict,
    n: int = 25,
    exclude_tickers: List[str] = None,
    require_eligible: bool = True,
) -> List[FinalDayCandidate]:
    """
    Get top N candidates for final-day deployment.

    Args:
        market_data: Market data dict
        n: Number of candidates to return
        exclude_tickers: Tickers to exclude
        require_eligible: Only return eligible candidates

    Returns:
        Top N candidates by score
    """
    if exclude_tickers is None:
        exclude_tickers = []

    all_candidates = score_all_final_day(market_data)

    filtered = []
    for c in all_candidates:
        if c.ticker in exclude_tickers:
            continue
        if require_eligible and not c.is_eligible:
            continue
        filtered.append(c)

    return filtered[:n]


# =============================================================================
# FINAL DAY EXECUTION
# =============================================================================

class FinalDayExecutor:
    """
    Executes the final-day blitz strategy.

    Phase 1: Sell all eligible non-core positions (free cash)
    Phase 2: Optionally sell underperforming core positions
    Phase 3: Score expanded universe
    Phase 4: Buy top picks with all available capital
    """

    def __init__(self, bot, state: StateManager, dry_run: bool = False):
        self.bot = bot
        self.state = state
        self.dry_run = dry_run
        self.collector = MarketDataCollector()

    def get_trade_budget(self) -> Dict:
        """Calculate available trade budget."""
        trades_used = self.state.get_trades_used()
        trades_remaining = self.state.get_trades_remaining()
        usable = max(0, trades_remaining - FINAL_DAY_RESERVE_TRADES)

        return {
            'total_used': trades_used,
            'total_remaining': trades_remaining,
            'usable': usable,
        }

    def execute(self, sell_cores: bool = False, max_trades: int = None) -> Dict:
        """
        Execute the final-day blitz.

        Args:
            sell_cores: If True, also sell core positions for redeployment
            max_trades: Override max trades (None = use all available)

        Returns:
            Execution result dict
        """
        et = pytz.timezone('US/Eastern')
        now_et = datetime.now(et)

        logger.info("=" * 70)
        logger.info("FINAL DAY BLITZ - Maximum Profit Strategy")
        logger.info(f"Time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
        logger.info("=" * 70)

        # Check market hours (relaxed for final day)
        if now_et.weekday() > 4:
            if not self.dry_run:
                return {'success': False, 'error': 'Weekend - market closed',
                        'trades_executed': 0}

        budget = self.get_trade_budget()
        trade_limit = max_trades or budget['usable']

        if trade_limit <= 0 and not self.dry_run:
            return {'success': False, 'error': 'No trades remaining',
                    'trades_executed': 0}

        logger.info(f"Trade budget: {budget['usable']} usable "
                    f"({budget['total_remaining']} remaining, "
                    f"{FINAL_DAY_RESERVE_TRADES} reserved)")

        # =====================================================================
        # PHASE 1: Fetch market data for entire universe
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 1: MARKET DATA COLLECTION")
        logger.info("=" * 70)

        # Include current positions in the fetch list
        positions = self.state.get_positions()
        fetch_tickers = list(set(
            FINAL_DAY_UNIVERSE + list(positions.keys())
        ))

        market_data = self.collector.get_all_data(fetch_tickers)

        if not market_data.get('VOO'):
            return {'success': False, 'error': 'Could not fetch VOO data',
                    'trades_executed': 0}

        vix = market_data.get('vix')
        logger.info(f"VIX: {vix:.2f}" if vix else "VIX: N/A")
        logger.info(f"Data fetched for {len([t for t in market_data if market_data[t] is not None and t != 'vix'])} tickers")

        # Get portfolio capital
        if not self.dry_run:
            try:
                portfolio_value, cash, buying_power = self.bot.get_capital_from_trade_kpis("VOO")
            except Exception as e:
                return {'success': False, 'error': f'Could not get capital: {e}',
                        'trades_executed': 0}
        else:
            portfolio_value = 1000000
            cash = 100000
            buying_power = 1000000

        effective_capital = portfolio_value if portfolio_value > 1000 else buying_power
        logger.info(f"Portfolio: ${portfolio_value:,.2f}, Cash: ${cash:,.2f}, "
                    f"Buying Power: ${buying_power:,.2f}")

        # =====================================================================
        # PHASE 2: EVALUATE CURRENT POSITIONS
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 2: POSITION EVALUATION")
        logger.info("=" * 70)

        positions = self.state.get_positions()
        sellable = []
        unsellable = []
        now_utc = datetime.now(timezone.utc)

        for ticker, pos in positions.items():
            shares = pos.get('shares', 0)
            if shares <= 0:
                continue

            can_sell, allowed_qty, reason = can_sell_with_lots(
                ticker, shares, self.state, now_utc
            )

            is_core = ticker in CORE_POSITIONS

            # Get current market data for P&L
            td = market_data.get(ticker, {})
            current_price = td.get('price', 0) if td else 0
            entry_price = pos.get('entry_price', 0)
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

            info = {
                'ticker': ticker,
                'shares': shares,
                'allowed_qty': allowed_qty,
                'is_core': is_core,
                'entry_price': entry_price,
                'current_price': current_price,
                'pnl_pct': pnl_pct,
                'reason': reason,
            }

            if can_sell and allowed_qty > 0:
                if is_core and not sell_cores:
                    logger.info(f"  KEEP  {ticker}: Core position (P&L: {pnl_pct:+.2%})")
                    unsellable.append(info)
                else:
                    logger.info(f"  SELL  {ticker}: {allowed_qty} shares sellable "
                                f"(P&L: {pnl_pct:+.2%})")
                    sellable.append(info)
            else:
                logger.info(f"  HOLD  {ticker}: {reason}")
                unsellable.append(info)

        logger.info(f"\nSellable: {len(sellable)} positions")
        logger.info(f"Unsellable/Keep: {len(unsellable)} positions")

        # =====================================================================
        # PHASE 3: SELL ELIGIBLE POSITIONS
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 3: LIQUIDATION")
        logger.info("=" * 70)

        trades_executed = 0
        sells_executed = []
        sells_failed = []
        errors = []

        # Ensure we maintain minimum 4 holdings
        total_positions = len(positions)
        max_sells = max(0, total_positions - MIN_HOLDINGS)
        # But we're going to buy right after, so we can sell more
        # as long as we buy back before the session ends.
        # For safety, keep at least 4 positions at any point.

        # Sort: sell worst performers first (maximize benefit of redeployment)
        sellable.sort(key=lambda x: x['pnl_pct'])

        for pos_info in sellable:
            if trades_executed >= trade_limit:
                logger.warning("Trade limit reached during sells")
                break

            # Check minimum holdings constraint
            current_positions = self.state.get_positions()
            if len(current_positions) <= MIN_HOLDINGS:
                logger.warning(f"At minimum holdings ({MIN_HOLDINGS}), stopping sells")
                break

            ticker = pos_info['ticker']
            shares = pos_info['allowed_qty']

            result = self._execute_sell(ticker, shares, "FINAL_DAY_LIQUIDATE")
            if result['success']:
                trades_executed += 1
                self.state.remove_position(ticker)
                sells_executed.append(ticker)
                logger.info(f"  SOLD {shares} {ticker}")
            else:
                sells_failed.append(ticker)
                errors.append(f"SELL {ticker}: {result.get('error')}")
                logger.error(f"  FAIL {ticker}: {result.get('error')}")

            time.sleep(2)  # Brief pause between trades

        logger.info(f"\nSells completed: {len(sells_executed)} sold, "
                    f"{len(sells_failed)} failed")

        # Refresh capital after sells
        if not self.dry_run and sells_executed:
            try:
                time.sleep(3)  # Wait for settlement to reflect
                portfolio_value, cash, buying_power = self.bot.get_capital_from_trade_kpis("VOO")
                effective_capital = portfolio_value if portfolio_value > 1000 else buying_power
                logger.info(f"Updated capital: Portfolio=${portfolio_value:,.2f}, "
                            f"Cash=${cash:,.2f}, BP=${buying_power:,.2f}")
            except Exception as e:
                logger.warning(f"Could not refresh capital: {e}")

        # =====================================================================
        # PHASE 4: SCORE AND SELECT TOP PICKS
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 4: SCORING & SELECTION")
        logger.info("=" * 70)

        # Exclude positions we still hold
        current_positions = self.state.get_positions()
        exclude = list(current_positions.keys()) + sells_failed

        # Score all candidates
        all_scored = score_all_final_day(market_data)
        eligible_scored = [c for c in all_scored if c.is_eligible
                          and c.ticker not in exclude]

        # Print top 30 scoring report
        self._print_scoring_report(all_scored[:40], current_positions)

        # How many buy trades can we do?
        remaining_trades = trade_limit - trades_executed
        buy_slots = min(remaining_trades, FINAL_DAY_MAX_POSITIONS)

        if buy_slots <= 0:
            logger.warning("No trades remaining for buys")
            return {
                'success': True,
                'trades_executed': trades_executed,
                'sells': sells_executed,
                'buys': [],
                'errors': errors
            }

        # Select top candidates
        top_picks = eligible_scored[:buy_slots]

        logger.info(f"\nTop {len(top_picks)} picks selected for deployment:")
        for i, c in enumerate(top_picks, 1):
            logger.info(f"  {i:2d}. {c.ticker:<6s} Score={c.score:+.4f} "
                        f"R1={c.r1:+.2%} R3={c.r3:+.2%} "
                        f"Vol={c.vol10:.4f} ${c.price:.2f}")

        # =====================================================================
        # PHASE 5: DEPLOY CAPITAL
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("PHASE 5: CAPITAL DEPLOYMENT")
        logger.info("=" * 70)

        buys_executed = []
        buys_failed = []

        # Calculate position size
        # Distribute available capital equally across picks
        num_picks = len(top_picks)
        if num_picks == 0:
            logger.warning("No eligible picks to buy")
            return {
                'success': True,
                'trades_executed': trades_executed,
                'sells': sells_executed,
                'buys': [],
                'errors': errors
            }

        # Use equal weight, but cap at 25% per position
        target_pct_per_pick = min(1.0 / num_picks, MAX_SINGLE_POSITION_PCT)

        # Use cash/buying power for new buys, not total portfolio
        # This ensures we don't over-allocate
        deploy_capital = effective_capital

        for candidate in top_picks:
            if trades_executed >= trade_limit:
                logger.warning("Trade limit reached during buys")
                break

            # Calculate shares
            position_value = deploy_capital * target_pct_per_pick
            shares = int((position_value - COMMISSION_PER_TRADE) / candidate.price)

            if shares < 1:
                logger.debug(f"  SKIP {candidate.ticker}: 0 shares "
                             f"(${position_value:.0f} / ${candidate.price:.2f})")
                continue

            # Double-check 25% limit
            position_cost = shares * candidate.price
            if position_cost > effective_capital * MAX_SINGLE_POSITION_PCT:
                shares = int(
                    (effective_capital * MAX_SINGLE_POSITION_PCT - COMMISSION_PER_TRADE)
                    / candidate.price
                )
                if shares < 1:
                    continue

            result = self._execute_buy(
                candidate.ticker, shares,
                f"FINAL_DAY_BLITZ_SCORE_{candidate.score:+.4f}",
                candidate.price
            )

            if result['success']:
                trades_executed += 1
                bucket = candidate.bucket or 'FINAL_DAY'
                self.state.add_position(
                    candidate.ticker, shares, candidate.price, bucket=bucket
                )
                buys_executed.append(candidate.ticker)
                logger.info(f"  BOUGHT {shares} {candidate.ticker} @ ~${candidate.price:.2f}")
            else:
                buys_failed.append(candidate.ticker)
                errors.append(f"BUY {candidate.ticker}: {result.get('error')}")
                logger.error(f"  FAIL {candidate.ticker}: {result.get('error')}")

            time.sleep(2)  # Brief pause between trades

        # =====================================================================
        # PHASE 6: SUMMARY
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("FINAL DAY BLITZ COMPLETE")
        logger.info("=" * 70)
        logger.info(f"Total trades executed: {trades_executed}")
        logger.info(f"Sells: {len(sells_executed)} ({', '.join(sells_executed)})")
        logger.info(f"Buys: {len(buys_executed)} ({', '.join(buys_executed)})")
        if errors:
            logger.info(f"Errors: {len(errors)}")
            for err in errors:
                logger.info(f"  - {err}")

        # Print final portfolio
        final_positions = self.state.get_positions()
        logger.info(f"\nFinal portfolio: {len(final_positions)} positions")
        for ticker, pos in final_positions.items():
            td = market_data.get(ticker, {})
            curr_price = td.get('price', 0) if td else 0
            logger.info(f"  {ticker}: {pos.get('shares', 0)} shares @ "
                        f"${pos.get('entry_price', 0):.2f} "
                        f"(current: ${curr_price:.2f})")

        return {
            'success': True,
            'trades_executed': trades_executed,
            'sells': sells_executed,
            'buys': buys_executed,
            'errors': errors,
            'budget_remaining': trade_limit - trades_executed,
        }

    def _execute_buy(self, ticker: str, shares: int,
                     rationale: str, price: float) -> Dict:
        """Execute a buy order via the execution pipeline."""
        logger.info(f"BUY {shares} {ticker} @ ~${price:.2f} ({rationale})")

        if self.dry_run:
            logger.info(f"[DRY RUN] Would buy {shares} {ticker}")
            return {'success': True, 'dry_run': True}

        from execution_pipeline import ExecutionPipeline, TradeOrder

        order = TradeOrder(
            ticker=ticker,
            side="BUY",
            shares=shares,
            rationale=rationale,
            portfolio_pct=round(100 * shares * price / 1000000, 1)
        )

        pipeline = ExecutionPipeline(
            self.bot, state_manager=self.state, dry_run=self.dry_run
        )
        result = pipeline.execute(order)

        return {
            'success': result.success,
            'error': result.message if not result.success else None
        }

    def _execute_sell(self, ticker: str, shares: int, rationale: str) -> Dict:
        """Execute a sell order via the execution pipeline."""
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

        pipeline = ExecutionPipeline(
            self.bot, state_manager=self.state, dry_run=self.dry_run
        )
        result = pipeline.execute(order)

        return {
            'success': result.success,
            'error': result.message if not result.success else None
        }

    def _print_scoring_report(self, candidates: List[FinalDayCandidate],
                              current_positions: Dict):
        """Print scoring report for final-day candidates."""
        print("\n" + "=" * 110)
        print("FINAL DAY BLITZ - SCORING REPORT")
        print("=" * 110)
        print(f"{'Rank':<5} {'Ticker':<7} {'Score':>8} {'R1':>8} {'R3':>8} "
              f"{'R10':>8} {'Vol10':>8} {'Price':>10} {'VolRatio':>8} {'Status':<14}")
        print("-" * 110)

        for i, c in enumerate(candidates, 1):
            status = "ELIGIBLE" if c.is_eligible else (
                c.disqualify_reason[:14] if c.disqualify_reason else "N/A")
            if c.ticker in current_positions:
                status = "HELD"
            elif c.is_core:
                status = "CORE"

            print(f"{i:<5} {c.ticker:<7} {c.score:>+8.4f} {c.r1:>+8.2%} "
                  f"{c.r3:>+8.2%} {c.r10:>+8.2%} {c.vol10:>8.4f} "
                  f"${c.price:>9.2f} {c.volume_ratio:>8.2f} {status:<14}")

        print("=" * 110)

        # Print eligible summary
        eligible = [c for c in candidates if c.is_eligible
                    and c.ticker not in current_positions]
        if eligible:
            top5 = eligible[:5]
            print(f"\nTOP 5 PICKS: {', '.join(c.ticker for c in top5)}")
        print()


# =============================================================================
# DRY RUN / PLANNING
# =============================================================================

def plan_final_day(market_data: Dict, positions: Dict,
                   sell_cores: bool = False) -> Dict:
    """
    Plan final-day trades without executing.

    Args:
        market_data: Market data dict
        positions: Current positions
        sell_cores: Whether to plan selling cores

    Returns:
        Plan dict with proposed trades
    """
    plan = {
        'sells': [],
        'buys': [],
        'errors': [],
    }

    voo_data = market_data.get('VOO')
    if not voo_data:
        plan['errors'].append('VOO data missing')
        plan['total_trades'] = 0
        return plan

    now_utc = datetime.now(timezone.utc)

    # Plan sells
    for ticker, pos in positions.items():
        shares = pos.get('shares', 0)
        if shares <= 0:
            continue

        is_core = ticker in CORE_POSITIONS
        if is_core and not sell_cores:
            continue

        td = market_data.get(ticker, {})
        current_price = td.get('price', 0) if td else 0
        entry_price = pos.get('entry_price', 0)
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        plan['sells'].append({
            'ticker': ticker,
            'shares': shares,
            'is_core': is_core,
            'entry_price': entry_price,
            'current_price': current_price,
            'pnl_pct': pnl_pct,
        })

    # Plan buys
    exclude = list(positions.keys())
    candidates = get_top_final_day_candidates(
        market_data, n=FINAL_DAY_MAX_POSITIONS, exclude_tickers=exclude
    )

    for c in candidates:
        plan['buys'].append({
            'ticker': c.ticker,
            'score': c.score,
            'price': c.price,
            'r1': c.r1,
            'r3': c.r3,
            'vol10': c.vol10,
            'bucket': c.bucket,
        })

    plan['total_trades'] = len(plan['sells']) + len(plan['buys'])
    return plan


def print_final_day_plan(plan: Dict):
    """Print a final-day plan in readable format."""
    print("\n" + "=" * 70)
    print("FINAL DAY BLITZ - EXECUTION PLAN")
    print("=" * 70)

    print(f"\nSELLS ({len(plan['sells'])}):")
    if plan['sells']:
        for sell in plan['sells']:
            core_tag = " [CORE]" if sell.get('is_core') else ""
            print(f"  SELL {sell['ticker']}{core_tag}: {sell.get('shares', 'all')} shares "
                  f"@ ${sell.get('current_price', 0):.2f} "
                  f"(P&L: {sell.get('pnl_pct', 0):+.2%})")
    else:
        print("  None")

    print(f"\nBUYS ({len(plan['buys'])}):")
    if plan['buys']:
        for i, buy in enumerate(plan['buys'], 1):
            bucket_tag = f" [{buy.get('bucket', '')}]" if buy.get('bucket') else ""
            print(f"  {i:2d}. BUY {buy['ticker']}{bucket_tag}: "
                  f"Score={buy.get('score', 0):+.4f} "
                  f"R1={buy.get('r1', 0):+.2%} R3={buy.get('r3', 0):+.2%} "
                  f"@ ${buy.get('price', 0):.2f}")
    else:
        print("  None")

    print(f"\nTOTAL TRADES: {plan['total_trades']}")

    if plan['errors']:
        print(f"\nERRORS:")
        for err in plan['errors']:
            print(f"  - {err}")

    print("=" * 70)


def print_final_day_scoring_report(market_data: Dict):
    """Print scoring report for all final-day candidates."""
    candidates = score_all_final_day(market_data)

    print("\n" + "=" * 115)
    print("FINAL DAY BLITZ - FULL UNIVERSE SCORING")
    print("=" * 115)
    print(f"{'Rank':<5} {'Ticker':<7} {'Score':>8} {'R1':>8} {'R3':>8} "
          f"{'R10':>8} {'Vol10':>8} {'Price':>10} {'VolRatio':>8} "
          f"{'Trend':>7} {'Eligible':>10}")
    print("-" * 115)

    for i, c in enumerate(candidates, 1):
        trend = "OK" if c.price >= c.sma50 else "WEAK"
        eligible = "YES" if c.is_eligible else (
            c.disqualify_reason[:10] if c.disqualify_reason else "NO")
        print(f"{i:<5} {c.ticker:<7} {c.score:>+8.4f} {c.r1:>+8.2%} "
              f"{c.r3:>+8.2%} {c.r10:>+8.2%} {c.vol10:>8.4f} "
              f"${c.price:>9.2f} {c.volume_ratio:>8.2f} "
              f"{trend:>7} {eligible:>10}")

    print("=" * 115)

    # Top 25 eligible
    eligible = [c for c in candidates if c.is_eligible][:25]
    print(f"\nTOP 25 ELIGIBLE: {', '.join(c.ticker for c in eligible)}")
    print(f"Total scored: {len(candidates)}, Eligible: {len([c for c in candidates if c.is_eligible])}")
