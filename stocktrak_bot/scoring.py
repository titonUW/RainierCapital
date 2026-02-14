"""
Satellite Selection Scoring System for StockTrak Bot

SPRINT MODE: Uses short-term momentum scoring for final week catch-up.
When SPRINT_MODE_ENABLED:
  - Primary rank: RelR3 (relative 3-day return vs VOO) - captures recent momentum
  - Tie-break #1: RelR10 (relative 10-day return vs VOO) - medium-term confirmation
  - Tie-break #2: VOL10 (10-day volatility) - ascending (lower is better)

Normal mode (DeMiguel et al. 1/N approach):
  1. Primary rank: RelR21 (relative 21-day return vs VOO) - descending
  2. Tie-break #1: RelR63 (relative 63-day return vs VOO) - descending
  3. Tie-break #2: VOL21 (21-day volatility) - ascending (lower is better)
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from config import (
    SATELLITE_BUCKETS, CORE_POSITIONS, MAX_PER_BUCKET,
    VOLATILITY_KILL_SWITCH_THRESHOLD, BUCKET_ETFS,
    get_bucket_for_ticker, get_all_satellite_tickers
)
from validators import (
    is_prohibited, validate_price, validate_uptrend,
    validate_double7_low, get_vix_regime, REGIME_PARAMS
)

logger = logging.getLogger('stocktrak_bot.scoring')


@dataclass
class ScoredCandidate:
    """
    Scored satellite candidate using parameter-free ranking.

    SPRINT MODE: Uses short-term momentum (3-day, 10-day) for final week catch-up.
    NORMAL MODE: Uses longer-term momentum (21-day, 63-day) per DeMiguel approach.
    """
    ticker: str
    bucket: str
    rel_r21: float       # Relative 21-day return vs VOO (normal mode primary)
    rel_r63: float       # Relative 63-day return vs VOO (normal mode tie-break)
    vol_21: float        # 21-day volatility (tie-break, lower is better)
    price: float
    is_qualified: bool
    is_etf: bool = False  # True if ticker is an ETF (for volatility kill-switch)
    disqualification_reason: Optional[str] = None
    # SPRINT MODE fields
    rel_r3: float = 0.0   # Relative 3-day return vs VOO (sprint primary)
    rel_r10: float = 0.0  # Relative 10-day return vs VOO (sprint tie-break)
    vol_10: float = 0.0   # 10-day volatility (sprint tie-break)
    momentum_score: float = 0.0  # Weighted score for sprint mode

    @property
    def rank_key(self) -> Tuple[float, float, float]:
        """
        Lexicographic sort key for parameter-free ranking.
        SPRINT MODE: Uses short-term momentum (-rel_r3, -rel_r10, vol_10)
        NORMAL MODE: Uses longer-term momentum (-rel_r21, -rel_r63, vol_21)
        """
        from config import SPRINT_MODE_ENABLED
        if SPRINT_MODE_ENABLED:
            # Sprint mode: prioritize short-term momentum
            return (-self.momentum_score, -self.rel_r3, self.vol_10)
        else:
            # Normal mode: DeMiguel-consistent ranking
            return (-self.rel_r21, -self.rel_r63, self.vol_21)


def is_bucket_etf(ticker: str, bucket: str) -> bool:
    """Check if ticker is an ETF within its bucket (for volatility kill-switch)."""
    if not bucket or bucket == "UNKNOWN":
        return False
    etfs = BUCKET_ETFS.get(bucket, [])
    return ticker in etfs


def calculate_candidate_metrics(ticker: str, market_data: Dict) -> Optional[ScoredCandidate]:
    """
    Calculate ranking metrics for a satellite candidate.

    SPRINT MODE: Uses short-term momentum (3-day, 10-day returns) with weighted scoring.
    NORMAL MODE: Uses DeMiguel et al. 1/N methodology with lexicographic ranking.

    Args:
        ticker: Stock ticker symbol
        market_data: Dict containing market data for all tickers

    Returns:
        ScoredCandidate object or None if data missing
    """
    from config import SPRINT_MODE_ENABLED

    ticker_data = market_data.get(ticker)
    voo_data = market_data.get('VOO')

    if not ticker_data or not voo_data:
        logger.warning(f"Missing data for {ticker} or VOO")
        return None

    # Get returns and volatility (long-term)
    ticker_ret_21d = ticker_data.get('return_21d')
    ticker_ret_63d = ticker_data.get('return_63d')
    ticker_vol_21d = ticker_data.get('volatility_21d')

    voo_ret_21d = voo_data.get('return_21d')
    voo_ret_63d = voo_data.get('return_63d')

    # Get short-term returns for SPRINT mode
    ticker_ret_3d = ticker_data.get('return_3d', 0) or 0
    ticker_ret_10d = ticker_data.get('return_10d', 0) or 0
    ticker_vol_10d = ticker_data.get('vol10', 0) or ticker_data.get('volatility_21d', 0.03) or 0.03

    voo_ret_3d = voo_data.get('return_3d', 0) or 0
    voo_ret_10d = voo_data.get('return_10d', 0) or 0

    # Fallback: estimate short-term from 21-day if not available
    if ticker_ret_3d == 0 and ticker_ret_21d:
        ticker_ret_3d = ticker_ret_21d * (3/21)
    if ticker_ret_10d == 0 and ticker_ret_21d:
        ticker_ret_10d = ticker_ret_21d * (10/21)
    if voo_ret_3d == 0 and voo_ret_21d:
        voo_ret_3d = voo_ret_21d * (3/21)
    if voo_ret_10d == 0 and voo_ret_21d:
        voo_ret_10d = voo_ret_21d * (10/21)

    # Check for missing data (need at least 21d for qualification)
    if None in [ticker_ret_21d, voo_ret_21d, ticker_vol_21d]:
        logger.warning(f"Missing return/volatility data for {ticker}")
        return None

    # Calculate relative metrics (long-term)
    rel_r21 = ticker_ret_21d - voo_ret_21d
    rel_r63 = (ticker_ret_63d - voo_ret_63d) if ticker_ret_63d and voo_ret_63d else 0.0
    vol_21 = ticker_vol_21d

    # Calculate relative metrics (short-term for SPRINT mode)
    rel_r3 = ticker_ret_3d - voo_ret_3d
    rel_r10 = ticker_ret_10d - voo_ret_10d
    vol_10 = ticker_vol_10d

    # SPRINT MODE: Calculate momentum score
    # Score = 0.55*rr3 + 0.35*rr10 - 0.25*vol10 (same as sprint3_strategy.py)
    momentum_score = 0.55 * rel_r3 + 0.35 * rel_r10 - 0.25 * vol_10

    # Check qualification criteria
    is_qualified = True
    disqualification_reason = None

    # Check prohibitions
    if is_prohibited(ticker):
        is_qualified = False
        disqualification_reason = "Prohibited security"

    # Check price
    price = ticker_data.get('price', 0)
    price_valid, price_reason = validate_price(ticker, price, is_buy=True)
    if not price_valid:
        is_qualified = False
        disqualification_reason = price_reason

    # Check uptrend - SPRINT MODE uses relaxed trend check
    if SPRINT_MODE_ENABLED:
        # Relaxed: just check price > SMA50 (allow more entries)
        sma50 = ticker_data.get('sma50', 0)
        if sma50 and price < sma50:
            is_qualified = False
            disqualification_reason = "Below SMA50"
    else:
        uptrend_valid, uptrend_reason = validate_uptrend(ticker_data)
        if not uptrend_valid:
            is_qualified = False
            disqualification_reason = uptrend_reason

    bucket = get_bucket_for_ticker(ticker)
    is_etf = is_bucket_etf(ticker, bucket)

    return ScoredCandidate(
        ticker=ticker,
        bucket=bucket or "UNKNOWN",
        rel_r21=rel_r21,
        rel_r63=rel_r63,
        vol_21=vol_21,
        price=price,
        is_qualified=is_qualified,
        is_etf=is_etf,
        disqualification_reason=disqualification_reason,
        rel_r3=rel_r3,
        rel_r10=rel_r10,
        vol_10=vol_10,
        momentum_score=momentum_score
    )


# Backward compatibility alias
def calculate_score(ticker: str, market_data: Dict) -> Optional[ScoredCandidate]:
    """Backward compatibility wrapper for calculate_candidate_metrics."""
    return calculate_candidate_metrics(ticker, market_data)


def score_all_satellites(market_data: Dict) -> List[ScoredCandidate]:
    """
    Rank all satellite candidates using parameter-free lexicographic sorting.

    Sorting order (DeMiguel-consistent):
      1. RelR21 descending (higher relative momentum = better)
      2. RelR63 descending (tie-break)
      3. VOL21 ascending (lower volatility = better)

    Args:
        market_data: Dict containing market data for all tickers

    Returns:
        List of ScoredCandidate objects, sorted by rank_key
    """
    candidates = []
    all_satellites = get_all_satellite_tickers()

    for ticker in all_satellites:
        scored = calculate_candidate_metrics(ticker, market_data)
        if scored:
            candidates.append(scored)
        else:
            logger.debug(f"Could not rank {ticker}")

    # Lexicographic sort using rank_key (parameter-free)
    candidates.sort(key=lambda x: x.rank_key)

    return candidates


def apply_volatility_kill_switch(
    candidate: ScoredCandidate,
    all_candidates: List[ScoredCandidate]
) -> Optional[ScoredCandidate]:
    """
    Apply volatility kill-switch: if single-name satellite has VOL21 > threshold,
    replace with the best ETF in that bucket.

    This is a risk control, not a forecasted weight scheme (DeMiguel-consistent).

    Args:
        candidate: The selected candidate
        all_candidates: All scored candidates for finding ETF replacement

    Returns:
        Original candidate if OK, or ETF replacement if kill-switch triggered
    """
    # Only apply to non-ETF (single-name stocks)
    if candidate.is_etf:
        return candidate

    # Check if volatility exceeds threshold
    if candidate.vol_21 <= VOLATILITY_KILL_SWITCH_THRESHOLD:
        return candidate

    # Find best ETF in same bucket
    bucket = candidate.bucket
    bucket_etfs = [c for c in all_candidates
                   if c.bucket == bucket and c.is_etf and c.is_qualified]

    if bucket_etfs:
        # Sort by rank_key and pick best
        bucket_etfs.sort(key=lambda x: x.rank_key)
        replacement = bucket_etfs[0]
        logger.info(f"VOLATILITY KILL-SWITCH: Replacing {candidate.ticker} "
                   f"(VOL21={candidate.vol_21:.4f}) with {replacement.ticker} "
                   f"(VOL21={replacement.vol_21:.4f})")
        return replacement

    # No ETF available, keep original with warning
    logger.warning(f"VOLATILITY WARNING: {candidate.ticker} has high volatility "
                  f"(VOL21={candidate.vol_21:.4f}) but no ETF replacement available")
    return candidate


def get_best_per_bucket(
    market_data: Dict,
    require_qualified: bool = True,
    apply_vol_killswitch: bool = True
) -> Dict[str, ScoredCandidate]:
    """
    Get the best candidate from each bucket (structural 1/N diversification).

    This is the core DeMiguel-aligned selection: instead of picking top N globally,
    we pick exactly 1 from each bucket to ensure structural diversification.

    Args:
        market_data: Market data dict
        require_qualified: If True, only consider qualified candidates
        apply_vol_killswitch: If True, apply volatility kill-switch for single names

    Returns:
        Dict mapping bucket name to best candidate
    """
    all_candidates = score_all_satellites(market_data)

    if require_qualified:
        candidates = [c for c in all_candidates if c.is_qualified]
    else:
        candidates = all_candidates

    best_per_bucket = {}

    for bucket_name in SATELLITE_BUCKETS.keys():
        # Get all candidates in this bucket
        bucket_candidates = [c for c in candidates if c.bucket == bucket_name]

        if not bucket_candidates:
            logger.warning(f"No qualified candidates in bucket {bucket_name}")
            continue

        # Sort by rank_key (parameter-free lexicographic)
        bucket_candidates.sort(key=lambda x: x.rank_key)
        best = bucket_candidates[0]

        # Apply volatility kill-switch if enabled
        if apply_vol_killswitch:
            best = apply_volatility_kill_switch(best, candidates)

        best_per_bucket[bucket_name] = best
        logger.debug(f"Best in {bucket_name}: {best.ticker} "
                    f"(RelR21={best.rel_r21:.4f}, RelR63={best.rel_r63:.4f}, "
                    f"VOL21={best.vol_21:.4f})")

    return best_per_bucket


def get_top_candidates(
    market_data: Dict,
    n: int = 8,
    require_qualified: bool = True
) -> List[ScoredCandidate]:
    """
    Get top candidates using structural 1/N bucket selection.

    CHANGED: Instead of picking top N globally, picks 1 from each bucket
    to ensure structural diversification across themes.

    Args:
        market_data: Market data dict
        n: Maximum candidates to return (default 8 = number of buckets)
        require_qualified: If True, only return qualified candidates

    Returns:
        List of top candidates (1 per bucket, up to n total)
    """
    best_per_bucket = get_best_per_bucket(market_data, require_qualified)

    # Convert to list and sort by rank_key
    candidates = list(best_per_bucket.values())
    candidates.sort(key=lambda x: x.rank_key)

    return candidates[:n]


def get_double7_buy_candidates(
    market_data: Dict,
    current_positions: Dict,
    vix_level: float
) -> List[ScoredCandidate]:
    """
    Get candidates that meet all buy criteria.

    SPRINT MODE: Skips Double-7 Low requirement - uses pure momentum ranking.
    NORMAL MODE: Requires Double-7 Low for entry confirmation.

    Entry conditions:
    - Price >= $6
    - Not prohibited
    - Uptrend check (relaxed in SPRINT mode)
    - Double-7 Low (NORMAL mode only)
    - Is best candidate in its bucket (1/N structural)
    - Bucket has room (MAX_PER_BUCKET varies by mode)

    Args:
        market_data: Market data dict
        current_positions: Current portfolio positions
        vix_level: Current VIX level

    Returns:
        List of candidates meeting all criteria
    """
    from config import SPRINT_MODE_ENABLED, MAX_PER_BUCKET

    # Get best candidate per bucket (structural 1/N)
    best_per_bucket = get_best_per_bucket(market_data, require_qualified=True)

    buy_candidates = []

    for bucket, candidate in best_per_bucket.items():
        ticker = candidate.ticker
        ticker_data = market_data.get(ticker, {})

        # SPRINT MODE: Skip Double-7 Low - use pure momentum
        if not SPRINT_MODE_ENABLED:
            # Normal mode: Check Double-7 Low
            double7_valid, double7_reason = validate_double7_low(ticker_data)
            if not double7_valid:
                continue

        # Check bucket capacity (MAX_PER_BUCKET is 2 in sprint mode)
        bucket_count = count_bucket_positions(bucket, current_positions)
        if bucket_count >= MAX_PER_BUCKET:
            logger.debug(f"{ticker}: Bucket {bucket} at capacity ({bucket_count}/{MAX_PER_BUCKET})")
            continue

        # Check we don't already hold this ticker
        if ticker in current_positions:
            logger.debug(f"{ticker}: Already held")
            continue

        buy_candidates.append(candidate)

    mode_str = "SPRINT momentum" if SPRINT_MODE_ENABLED else "Double-7"
    logger.info(f"Found {len(buy_candidates)} {mode_str} buy candidates (1/N bucket selection)")
    return buy_candidates


def get_double7_sell_candidates(
    market_data: Dict,
    current_positions: Dict
) -> List[Tuple[str, str]]:
    """
    Get positions that hit Double-7 High (profit-taking signal).

    Args:
        market_data: Market data dict
        current_positions: Current portfolio positions

    Returns:
        List of (ticker, reason) tuples for optional sells
    """
    sell_candidates = []

    for ticker, position in current_positions.items():
        # Skip core positions
        if ticker in CORE_POSITIONS:
            continue

        ticker_data = market_data.get(ticker, {})
        if not ticker_data:
            continue

        # Check Double-7 High
        price = ticker_data.get('price', 0)
        closes_7d = ticker_data.get('closes_7d', [])

        if len(closes_7d) >= 7 and price >= max(closes_7d):
            entry_price = position.get('entry_price', price)
            pnl_pct = (price - entry_price) / entry_price if entry_price > 0 else 0

            # Only suggest if profitable (>10%)
            if pnl_pct > 0.10:
                sell_candidates.append((ticker, f"Double-7 High with {pnl_pct:.1%} profit"))

    return sell_candidates


def count_bucket_positions(bucket: str, current_positions: Dict) -> int:
    """
    Count how many positions are in a given bucket.

    Args:
        bucket: Bucket name (e.g., 'A_SPACE')
        current_positions: Current portfolio positions

    Returns:
        Number of positions in that bucket
    """
    count = 0
    for ticker in current_positions.keys():
        if ticker in CORE_POSITIONS:
            continue
        if get_bucket_for_ticker(ticker) == bucket:
            count += 1
    return count


def get_represented_buckets(current_positions: Dict) -> List[str]:
    """
    Get list of buckets that have at least one position.

    Args:
        current_positions: Current portfolio positions

    Returns:
        List of bucket names with positions
    """
    buckets = set()
    for ticker in current_positions.keys():
        if ticker in CORE_POSITIONS:
            continue
        bucket = get_bucket_for_ticker(ticker)
        if bucket:
            buckets.add(bucket)
    return list(buckets)


def select_replacement_satellite(
    market_data: Dict,
    current_positions: Dict,
    vix_level: float,
    exclude_tickers: List[str] = None,
    for_bucket: str = None
) -> Optional[ScoredCandidate]:
    """
    Select the best replacement satellite when one needs to be replaced.

    UPDATED for structural 1/N: Prioritizes filling empty buckets to maintain
    diversification across all 8 themes.

    Args:
        market_data: Market data dict
        current_positions: Current portfolio positions
        vix_level: Current VIX level
        exclude_tickers: Tickers to exclude (e.g., just sold)
        for_bucket: If specified, only consider candidates from this bucket

    Returns:
        Best replacement candidate or None
    """
    if exclude_tickers is None:
        exclude_tickers = []

    regime = get_vix_regime(vix_level)
    max_satellites = REGIME_PARAMS[regime]['max_satellites']

    # Count current satellites
    current_satellites = sum(1 for t in current_positions if t not in CORE_POSITIONS)
    if current_satellites >= max_satellites:
        logger.info(f"Already at max satellites ({current_satellites}/{max_satellites}) for {regime} regime")
        return None

    # Get best per bucket
    best_per_bucket = get_best_per_bucket(market_data, require_qualified=True)

    # If specific bucket requested, try that first
    if for_bucket and for_bucket in best_per_bucket:
        candidate = best_per_bucket[for_bucket]
        if candidate.ticker not in exclude_tickers and candidate.ticker not in current_positions:
            bucket_count = count_bucket_positions(for_bucket, current_positions)
            if bucket_count < MAX_PER_BUCKET:
                logger.info(f"Selected replacement for {for_bucket}: {candidate.ticker} "
                           f"(RelR21={candidate.rel_r21:.4f})")
                return candidate

    # Find empty buckets first (maintain structural diversification)
    filled_buckets = set(get_represented_buckets(current_positions))
    empty_buckets = [b for b in SATELLITE_BUCKETS.keys() if b not in filled_buckets]

    # Prioritize filling empty buckets
    for bucket in empty_buckets:
        if bucket not in best_per_bucket:
            continue

        candidate = best_per_bucket[bucket]
        if candidate.ticker in exclude_tickers or candidate.ticker in current_positions:
            continue

        logger.info(f"Selected replacement (filling empty bucket {bucket}): {candidate.ticker} "
                   f"(RelR21={candidate.rel_r21:.4f})")
        return candidate

    # If H_MATERIALS bucket (DMAT only) fails eligibility, use strongest RelR21 from any bucket
    if 'H_MATERIALS' in empty_buckets and 'H_MATERIALS' not in best_per_bucket:
        # Find best candidate from any bucket with space
        all_candidates = list(best_per_bucket.values())
        all_candidates.sort(key=lambda x: x.rank_key)

        for candidate in all_candidates:
            if candidate.ticker in exclude_tickers or candidate.ticker in current_positions:
                continue
            bucket_count = count_bucket_positions(candidate.bucket, current_positions)
            if bucket_count < MAX_PER_BUCKET:
                logger.info(f"Selected replacement (MATERIALS fallback): {candidate.ticker} "
                           f"(RelR21={candidate.rel_r21:.4f}, bucket={candidate.bucket})")
                return candidate

    logger.warning("No suitable replacement found - all buckets filled or no qualified candidates")
    return None


def print_scoring_report(market_data: Dict, current_positions: Dict = None):
    """
    Print a detailed ranking report for all satellites.

    UPDATED: Shows parameter-free ranking metrics (no weighted score).

    Args:
        market_data: Market data dict
        current_positions: Current positions (optional)
    """
    all_ranked = score_all_satellites(market_data)
    best_per_bucket = get_best_per_bucket(market_data, require_qualified=True)
    best_tickers = {c.ticker for c in best_per_bucket.values()}

    print("\n" + "=" * 95)
    print("SATELLITE RANKING REPORT (Parameter-Free 1/N)")
    print("=" * 95)
    print(f"{'Rank':<5} {'Ticker':<8} {'Bucket':<12} {'RelR21':>10} {'RelR63':>10} "
          f"{'VOL21':>10} {'Price':>10} {'ETF':>5} {'Status':<18}")
    print("-" * 95)

    for i, candidate in enumerate(all_ranked, 1):
        status = "QUALIFIED" if candidate.is_qualified else candidate.disqualification_reason[:18] if candidate.disqualification_reason else "N/A"
        if current_positions and candidate.ticker in current_positions:
            status = "HELD"
        elif candidate.ticker in best_tickers:
            status = "BEST-IN-BUCKET"

        etf_flag = "Y" if candidate.is_etf else "N"

        # Highlight if volatility exceeds kill-switch threshold
        vol_str = f"{candidate.vol_21:>10.4f}"
        if candidate.vol_21 > VOLATILITY_KILL_SWITCH_THRESHOLD and not candidate.is_etf:
            vol_str = f"{candidate.vol_21:>9.4f}*"  # Asterisk for high vol

        print(f"{i:<5} {candidate.ticker:<8} {candidate.bucket:<12} {candidate.rel_r21:>10.4f} "
              f"{candidate.rel_r63:>10.4f} {vol_str:>10} {candidate.price:>10.2f} "
              f"{etf_flag:>5} {status:<18}")

    print("=" * 95)
    print("* = Volatility exceeds kill-switch threshold (6%)")

    # Best per bucket summary (structural 1/N)
    print(f"\nBEST PER BUCKET (Structural 1/N):")
    for bucket, candidate in sorted(best_per_bucket.items()):
        held_flag = " [HELD]" if current_positions and candidate.ticker in current_positions else ""
        print(f"  {bucket}: {candidate.ticker} (RelR21={candidate.rel_r21:.4f}){held_flag}")

    # Bucket distribution for current positions
    if current_positions:
        buckets = get_represented_buckets(current_positions)
        missing = [b for b in SATELLITE_BUCKETS.keys() if b not in buckets]
        print(f"\nCURRENT BUCKETS: {buckets}")
        if missing:
            print(f"MISSING BUCKETS: {missing}")
