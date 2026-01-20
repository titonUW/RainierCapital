"""
Market Data Collection for StockTrak Bot

Uses yfinance to fetch real-time and historical market data
for all tickers in the portfolio and candidate lists.
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import time

import yfinance as yf
import pandas as pd

from config import (
    CORE_POSITIONS, get_all_satellite_tickers, get_all_tickers
)

logger = logging.getLogger('stocktrak_bot.market_data')


class MarketDataCollector:
    """Collects market data for portfolio management"""

    def __init__(self):
        self.cache = {}
        self.cache_timestamp = None
        self.cache_duration = timedelta(minutes=5)

    def get_all_tickers(self) -> List[str]:
        """Get list of all tickers to monitor"""
        return get_all_tickers()

    def get_all_data(self, tickers: List[str] = None) -> Dict:
        """
        Fetch all required market data.

        Args:
            tickers: List of tickers to fetch (defaults to all)

        Returns:
            Dict with ticker data and VIX
        """
        if tickers is None:
            tickers = self.get_all_tickers()

        data = {}
        logger.info(f"Fetching market data for {len(tickers)} tickers...")

        # Get VIX first
        data['vix'] = self._get_vix()

        # Fetch data for all tickers
        success_count = 0
        fail_count = 0

        for ticker in tickers:
            try:
                ticker_data = self._get_ticker_data(ticker)
                if ticker_data:
                    data[ticker] = ticker_data
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Failed to get data for {ticker}: {e}")
                data[ticker] = None
                fail_count += 1

            # Rate limiting - be gentle with yfinance
            time.sleep(0.1)

        logger.info(f"Fetched data for {success_count} tickers, {fail_count} failed")
        return data

    def _get_vix(self) -> float:
        """
        Get current VIX level.

        Returns:
            VIX level or default of 18.0 if unavailable
        """
        try:
            vix = yf.Ticker('^VIX')
            hist = vix.history(period='5d')

            if len(hist) > 0:
                vix_level = hist['Close'].iloc[-1]
                logger.info(f"VIX: {vix_level:.2f}")
                return vix_level
            else:
                logger.warning("No VIX history available, using default")
                return 18.0

        except Exception as e:
            logger.error(f"VIX fetch error: {e}, using default 18.0")
            return 18.0

    def _get_ticker_data(self, ticker: str) -> Optional[Dict]:
        """
        Get comprehensive data for a single ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with price, SMAs, returns, volatility, etc.
        """
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='1y')

            if len(hist) < 50:
                logger.warning(f"Insufficient history for {ticker}: {len(hist)} days")
                return None

            current_price = hist['Close'].iloc[-1]

            # Calculate SMAs
            sma50 = hist['Close'].tail(50).mean()
            sma100 = hist['Close'].tail(100).mean() if len(hist) >= 100 else None
            sma200 = hist['Close'].tail(200).mean() if len(hist) >= 200 else sma100

            # Recent price data
            closes_7d = hist['Close'].tail(7).tolist()
            highs_7d = hist['High'].tail(7).tolist()
            lows_7d = hist['Low'].tail(7).tolist()

            # Calculate returns
            return_21d = self._calc_return(hist, 21)
            return_63d = self._calc_return(hist, 63)

            # Calculate volatility (standard deviation of daily returns)
            volatility_21d = hist['Close'].tail(21).pct_change().std()

            # Volume
            volume = hist['Volume'].iloc[-1]
            avg_volume_20d = hist['Volume'].tail(20).mean()

            return {
                'ticker': ticker,
                'price': current_price,
                'sma50': sma50,
                'sma100': sma100,
                'sma200': sma200,
                'closes_7d': closes_7d,
                'highs_7d': highs_7d,
                'lows_7d': lows_7d,
                'return_21d': return_21d,
                'return_63d': return_63d,
                'volatility_21d': volatility_21d,
                'volume': volume,
                'avg_volume_20d': avg_volume_20d,
                'last_updated': datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return None

    def _calc_return(self, hist: pd.DataFrame, days: int) -> Optional[float]:
        """
        Calculate N-day return.

        Args:
            hist: Price history DataFrame
            days: Number of days for return calculation

        Returns:
            Decimal return (e.g., 0.05 for 5%) or None if insufficient data
        """
        if len(hist) < days + 1:
            return None

        current = hist['Close'].iloc[-1]
        past = hist['Close'].iloc[-(days + 1)]

        if past <= 0:
            return None

        return (current - past) / past

    def get_single_ticker(self, ticker: str) -> Optional[Dict]:
        """
        Get data for a single ticker with caching.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Ticker data dict or None
        """
        return self._get_ticker_data(ticker)

    def get_current_price(self, ticker: str) -> Optional[float]:
        """
        Get just the current price for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Current price or None
        """
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='1d')
            if len(hist) > 0:
                return hist['Close'].iloc[-1]
            return None
        except Exception as e:
            logger.error(f"Error getting price for {ticker}: {e}")
            return None

    def get_batch_prices(self, tickers: List[str]) -> Dict[str, float]:
        """
        Get current prices for multiple tickers efficiently.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dict mapping ticker -> price
        """
        prices = {}
        ticker_str = ' '.join(tickers)

        try:
            data = yf.download(ticker_str, period='1d', group_by='ticker', progress=False)

            for ticker in tickers:
                try:
                    if len(tickers) == 1:
                        price = data['Close'].iloc[-1]
                    else:
                        price = data[ticker]['Close'].iloc[-1]
                    prices[ticker] = price
                except (KeyError, IndexError):
                    logger.warning(f"Could not get price for {ticker}")
                    prices[ticker] = None

        except Exception as e:
            logger.error(f"Batch price fetch error: {e}")
            # Fallback to individual fetches
            for ticker in tickers:
                prices[ticker] = self.get_current_price(ticker)

        return prices

    def validate_data_freshness(self, data: Dict) -> bool:
        """
        Check if market data is from today's trading session.

        Args:
            data: Market data dict

        Returns:
            True if data is fresh, False otherwise
        """
        for ticker in list(CORE_POSITIONS.keys()):
            ticker_data = data.get(ticker)
            if not ticker_data:
                return False

            last_updated = ticker_data.get('last_updated')
            if not last_updated:
                return False

            # Parse timestamp and check if it's today
            update_time = datetime.fromisoformat(last_updated)
            if update_time.date() != datetime.now().date():
                return False

        return True


def print_market_summary(data: Dict):
    """
    Print a summary of current market conditions.

    Args:
        data: Market data dict from get_all_data()
    """
    print("\n" + "=" * 60)
    print("MARKET SUMMARY")
    print("=" * 60)

    # VIX
    vix = data.get('vix', 0)
    if vix < 20:
        vix_status = "NORMAL"
    elif vix <= 30:
        vix_status = "CAUTION"
    else:
        vix_status = "SHOCK"
    print(f"VIX: {vix:.2f} ({vix_status})")

    # VOO trend check
    voo_data = data.get('VOO', {})
    if voo_data:
        voo_price = voo_data.get('price', 0)
        voo_sma200 = voo_data.get('sma200', 0)
        if voo_sma200:
            trend = "RISK-ON" if voo_price > voo_sma200 else "RISK-OFF"
            print(f"VOO: ${voo_price:.2f} vs SMA200 ${voo_sma200:.2f} ({trend})")

    # Core positions
    print("\nCORE POSITIONS:")
    for ticker in CORE_POSITIONS.keys():
        ticker_data = data.get(ticker, {})
        if ticker_data:
            price = ticker_data.get('price', 0)
            ret_21d = ticker_data.get('return_21d', 0) or 0
            print(f"  {ticker}: ${price:.2f} (21d: {ret_21d:.1%})")

    print("=" * 60)


if __name__ == "__main__":
    # Test data collection
    logging.basicConfig(level=logging.INFO)

    collector = MarketDataCollector()

    # Test single ticker
    voo_data = collector.get_single_ticker('VOO')
    print(f"VOO: {voo_data}")

    # Test batch prices
    prices = collector.get_batch_prices(['VOO', 'VTI', 'VEA'])
    print(f"Batch prices: {prices}")

    # Test full data collection (subset)
    data = collector.get_all_data(['VOO', 'VTI', 'SMH', 'ITA'])
    print_market_summary(data)
