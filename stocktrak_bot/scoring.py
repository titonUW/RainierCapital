"""
Satellite Selection Scoring System for StockTrak Bot

Implements cross-sectional momentum scoring with volatility penalty
to rank satellite candidates for the portfolio.
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from config import (
    SATELLITE_BUCKETS, CORE_POSITIONS, MAX_PER_BUCKET,
    get_bucket_for_ticker, get_all_satellite_tickers
)
from validators import (
    is_prohibited, validate_price, validate_uptrend,
    validate_double7_low, get_vix_regime, REGIME_PARAMS
)

logger = logging.getLogger('stocktrak_bot.scoring')


@dataclass
class ScoredCandidate:
    """Scored satellite candidate"""
    ticker: str
    bucket: str
    score: float
    rel_mom_21: float
    rel_mom_63: float
    vol_penalty: float
    price: float
    is_qualified: bool
    disqualification_reason: Optional[str] = None


def calculate_score(ticker: str, market_data: Dict) -> Optional[ScoredCandidate]:
    """
    Calculate cross-sectional momentum score with volatility penalty.

    SCORE = RelMom21 + (RelMom63 * 0.5) - (VolPenalty * 2)

    Where:
    - RelMom21 = ticker's 21-day return - VOO's 21-day return
    - RelMom63 = ticker's 63-day return - VOO's 63-day return
    - VolPenalty = ticker's 21-day volatility - VOO's 21-day volatility

    Higher score = better candidate.

    Args:
        ticker: Stock ticker symbol
        market_data: Dict containing market data for all tickers

    Returns:
        ScoredCandidate object or None if data missing
    """
    ticker_data = market_data.get(ticker)
    voo_data = market_data.get('VOO')

    if not ticker_data or not voo_data:
        logger.warning(f"Missing data for {ticker} or VOO")
        return None

    # Get returns and volatility
    ticker_ret_21d = ticker_data.get('return_21d')
    ticker_ret_63d = ticker_data.get('return_63d')
    ticker_vol_21d = ticker_data.get('volatility_21d')

    voo_ret_21d = voo_data.get('return_21d')
    voo_ret_63d = voo_data.get('return_63d')
    voo_vol_21d = voo_data.get('volatility_21d')

    # Check for missing data
    if None in [ticker_ret_21d, voo_ret_21d, ticker_vol_21d, voo_vol_21d]:
        logger.warning(f"Missing return/volatility data for {ticker}")
        return None

    # Calculate relative metrics
    rel_mom_21 = ticker_ret_21d - voo_ret_21d
    rel_mom_63 = (ticker_ret_63d - voo_ret_63d) if ticker_ret_63d and voo_ret_63d else 0
    vol_penalty = ticker_vol_21d - voo_vol_21d

    # Calculate score
    score = rel_mom_21 + (rel_mom_63 * 0.5) - (vol_penalty * 2)

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

    # Check uptrend
    uptrend_valid, uptrend_reason = validate_uptrend(ticker_data)
    if not uptrend_valid:
        is_qualified = False
        disqualification_reason = uptrend_reason

    bucket = get_bucket_for_ticker(ticker)

    return ScoredCandidate(
        ticker=ticker,
        bucket=bucket or "UNKNOWN",
        score=score,
        rel_mom_21=rel_mom_21,
        rel_mom_63=rel_mom_63,
        vol_penalty=vol_penalty,
        price=price,
        is_qualified=is_qualified,
        disqualification_reason=disqualification_reason
    )


def score_all_satellites(market_data: Dict) -> List[ScoredCandidate]:
    """
    Score all satellite candidates.

    Args:
        market_data: Dict containing market data for all tickers

    Returns:
        List of ScoredCandidate objects, sorted by score descending
    """
    candidates = []
    all_satellites = get_all_satellite_tickers()

    for ticker in all_satellites:
        scored = calculate_score(ticker, market_data)
        if scored:
            candidates.append(scored)
        else:
            logger.debug(f"Could not score {ticker}")

    # Sort by score descending
    candidates.sort(key=lambda x: x.score, reverse=True)

    return candidates


def get_top_candidates(
    market_data: Dict,
    n: int = 12,
    require_qualified: bool = True
) -> List[ScoredCandidate]:
    """
    Get top N scoring candidates.

    Args:
        market_data: Market data dict
        n: Number of candidates to return
        require_qualified: If True, only return qualified candidates

    Returns:
        List of top N ScoredCandidate objects
    """
    all_scored = score_all_satellites(market_data)

    if require_qualified:
        qualified = [c for c in all_scored if c.is_qualified]
    else:
        qualified = all_scored

    return qualified[:n]


def get_double7_buy_candidates(
    market_data: Dict,
    current_positions: Dict,
    vix_level: float
) -> List[ScoredCandidate]:
    """
    Get candidates that meet all buy criteria including Double-7 Low.

    Entry conditions (ALL must be TRUE):
    - Price >= $6
    - Not prohibited
    - Uptrend (Close > SMA50 > SMA200)
    - Double-7 Low (today's close is 7-day low)
    - In top 12 scores
    - Bucket has space (< 2 positions from same bucket)

    Args:
        market_data: Market data dict
        current_positions: Current portfolio positions
        vix_level: Current VIX level

    Returns:
        List of candidates meeting all criteria
    """
    # Get top 12 qualified candidates
    top_12 = get_top_candidates(market_data, n=12, require_qualified=True)
    top_12_tickers = {c.ticker for c in top_12}

    buy_candidates = []

    for candidate in top_12:
        ticker = candidate.ticker
        ticker_data = market_data.get(ticker, {})

        # Check Double-7 Low
        double7_valid, double7_reason = validate_double7_low(ticker_data)
        if not double7_valid:
            continue

        # Check bucket limits
        bucket = candidate.bucket
        bucket_count = count_bucket_positions(bucket, current_positions)
        if bucket_count >= MAX_PER_BUCKET:
            logger.debug(f"{ticker}: Bucket {bucket} full ({bucket_count}/{MAX_PER_BUCKET})")
            continue

        buy_candidates.append(candidate)

    logger.info(f"Found {len(buy_candidates)} Double-7 buy candidates")
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
    exclude_tickers: List[str] = None
) -> Optional[ScoredCandidate]:
    """
    Select the best replacement satellite when one needs to be replaced.

    Considers:
    - Top 12 scoring requirement
    - Bucket diversification
    - Regime-based constraints

    Args:
        market_data: Market data dict
        current_positions: Current portfolio positions
        vix_level: Current VIX level
        exclude_tickers: Tickers to exclude (e.g., just sold)

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

    # Get candidates
    top_candidates = get_top_candidates(market_data, n=12, require_qualified=True)

    for candidate in top_candidates:
        ticker = candidate.ticker

        # Skip excluded
        if ticker in exclude_tickers:
            continue

        # Skip already held
        if ticker in current_positions:
            continue

        # Check bucket space
        bucket = candidate.bucket
        bucket_count = count_bucket_positions(bucket, current_positions)
        if bucket_count >= MAX_PER_BUCKET:
            continue

        logger.info(f"Selected replacement: {ticker} (score={candidate.score:.4f}, bucket={bucket})")
        return candidate

    logger.warning("No suitable replacement found")
    return None


def print_scoring_report(market_data: Dict, current_positions: Dict = None):
    """
    Print a detailed scoring report for all satellites.

    Args:
        market_data: Market data dict
        current_positions: Current positions (optional)
    """
    all_scored = score_all_satellites(market_data)

    print("\n" + "=" * 80)
    print("SATELLITE SCORING REPORT")
    print("=" * 80)
    print(f"{'Rank':<5} {'Ticker':<8} {'Bucket':<12} {'Score':>10} {'RelMom21':>10} "
          f"{'RelMom63':>10} {'VolPen':>10} {'Price':>10} {'Status':<15}")
    print("-" * 80)

    for i, candidate in enumerate(all_scored, 1):
        status = "QUALIFIED" if candidate.is_qualified else candidate.disqualification_reason[:15]
        if current_positions and candidate.ticker in current_positions:
            status = "HELD"

        print(f"{i:<5} {candidate.ticker:<8} {candidate.bucket:<12} {candidate.score:>10.4f} "
              f"{candidate.rel_mom_21:>10.4f} {candidate.rel_mom_63:>10.4f} "
              f"{candidate.vol_penalty:>10.4f} {candidate.price:>10.2f} {status:<15}")

    print("=" * 80)

    # Top 12 summary
    top_12 = get_top_candidates(market_data, n=12, require_qualified=True)
    print(f"\nTOP 12 QUALIFIED: {[c.ticker for c in top_12]}")

    # Bucket distribution
    if current_positions:
        buckets = get_represented_buckets(current_positions)
        print(f"REPRESENTED BUCKETS: {buckets}")
