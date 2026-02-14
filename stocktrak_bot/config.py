"""
StockTrak Bot Configuration - TEAM 9
Morgan Stanley UWT Milgard Competition 2026

SECURITY NOTE: Credentials should be set via environment variables:
    export STOCKTRAK_USERNAME="your_username"
    export STOCKTRAK_PASSWORD="your_password"
    export STOCKTRAK_SESSION_ID="your_session_id"

Fallback values are provided for development only.
"""

import os
from datetime import datetime

# =============================================================================
# SPRINT MODE FLAG - Enable for final week aggressive trading
# MUST BE DEFINED EARLY - used by other config values below
# =============================================================================
SPRINT_MODE_ENABLED = True  # ACTIVATED: Final week catch-up mode

# =============================================================================
# STOCKTRAK CREDENTIALS (from environment variables)
# =============================================================================
STOCKTRAK_URL = "https://app.stocktrak.com"
STOCKTRAK_LOGIN_URL = "https://app.stocktrak.com/login"
STOCKTRAK_DASHBOARD_URL = "https://app.stocktrak.com/dashboard/standard"
STOCKTRAK_TRADING_URL = "https://app.stocktrak.com/trading/equitiesaliases"
STOCKTRAK_TRADING_EQUITIES_URL = "https://app.stocktrak.com/trading/equities"
STOCKTRAK_TRANSACTION_HISTORY_URL = "https://app.stocktrak.com/portfolio/transactionhistory"
STOCKTRAK_ORDER_HISTORY_URL = "https://app.stocktrak.com/portfolio/orderhistory"

# Load credentials from environment variables with fallback for development
STOCKTRAK_USERNAME = os.environ.get("STOCKTRAK_USERNAME", "SMC Team 9")
STOCKTRAK_PASSWORD = os.environ.get("STOCKTRAK_PASSWORD", "T9bKx3")
SESSION_ID = os.environ.get("STOCKTRAK_SESSION_ID", "355677")

def validate_credentials():
    """Validate that credentials are configured properly."""
    if not STOCKTRAK_USERNAME or not STOCKTRAK_PASSWORD:
        raise ValueError(
            "StockTrak credentials not configured. Set environment variables:\n"
            "  export STOCKTRAK_USERNAME='your_username'\n"
            "  export STOCKTRAK_PASSWORD='your_password'"
        )
    return True

# =============================================================================
# COMPETITION SETTINGS
# =============================================================================
COMPETITION_START = "2026-01-20"
COMPETITION_END = "2026-02-20"
STARTING_CAPITAL = 1000000  # $1,000,000

# =============================================================================
# TRADING RULES (DO NOT MODIFY - COMPETITION RULES)
# =============================================================================
MAX_SINGLE_POSITION_PCT = 0.25  # 25% max per position at purchase time
MIN_HOLDINGS = 4                 # Must hold at least 4 securities
MAX_TRADES_TOTAL = 80           # Lifetime trade limit
HARD_STOP_TRADES = 70           # Stop new trades after 70
MIN_PRICE_AT_BUY = 5.00         # Cannot buy stocks below $5
SAFETY_BUFFER_PRICE = 6.00      # Only buy if price >= $6 (safety margin)
COMMISSION_PER_TRADE = 5.00     # $5 per trade
CASH_INTEREST_RATE = 0.01       # 1% annual on cash

# 24-hour minimum holding period (timestamp-based, NOT date-based)
# CRITICAL: The competition requires 24-hour hold, not T+2 trading days
MIN_HOLD_HOURS = 24
MIN_HOLD_SECONDS = MIN_HOLD_HOURS * 3600  # 86400 seconds

# Safety buffer so you never accidentally sell at 23:59:59
# Using 5 minutes (300s) for safety margin at 24h boundary
HOLD_BUFFER_SECONDS = 300  # 5 minutes

# =============================================================================
# HOLDING PERIOD ENFORCEMENT MODE
# =============================================================================
# LOT_FIFO: Each BUY creates a timestamped lot. SELLs consume eligible lots FIFO.
#           A lot is eligible when: now_utc >= buy_ts_utc + 24h + buffer
#           This allows selling old shares even after buying new shares.
#
# STRICT_TICKER: If ANY buy occurred within 24h, block ALL sells for that ticker.
#                Safer interpretation if judges are strict about the rule.
#
# Set HOLD_MODE to control which enforcement is used.
HOLD_MODE = "LOT_FIFO"  # Options: "LOT_FIFO" or "STRICT_TICKER"

# Legacy constant - kept for backwards compatibility but not used for compliance
MIN_HOLD_TRADING_DAYS = 2       # DEPRECATED: Use MIN_HOLD_SECONDS instead

# =============================================================================
# MARKET HOURS (Eastern Time)
# =============================================================================
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"
EXECUTION_WINDOW_START = "09:40"  # 9:40 AM ET (after initial volatility)
EXECUTION_WINDOW_END = "10:05"    # 10:05 AM ET

# =============================================================================
# BOT SETTINGS
# =============================================================================
HEADLESS_MODE = True              # Set False to see browser during testing
EXECUTION_TIME = "09:45"          # 9:45 AM ET - primary execution time (after initial volatility)
DATA_COLLECTION_TIME = "09:30"    # 9:30 AM ET - data collection time (market open)
LOG_LEVEL = "INFO"
SCREENSHOT_ON_ERROR = True
SCREENSHOT_ON_TRADE = True
SLOW_MO = 150                     # Milliseconds delay between actions

# =============================================================================
# TIMEOUTS
# =============================================================================
DEFAULT_TIMEOUT = 30000           # 30 seconds
PAGE_LOAD_TIMEOUT = 60000         # 60 seconds
ORDER_SUBMISSION_WAIT = 3         # Seconds to wait after order submission

# =============================================================================
# KEEP-AWAKE SETTINGS
# =============================================================================
PREVENT_SLEEP = True              # Prevent PC from sleeping
KEEP_AWAKE_INTERVAL = 60          # Seconds between keep-awake signals

# =============================================================================
# AUTO-RESTART SETTINGS
# =============================================================================
AUTO_RESTART_ON_CRASH = True
MAX_RESTART_ATTEMPTS = 5
RESTART_DELAY_SECONDS = 60

# =============================================================================
# ALERT SETTINGS (Optional)
# =============================================================================
ENABLE_EMAIL_ALERTS = False
ALERT_EMAIL_TO = ""
ALERT_EMAIL_FROM = ""
SMTP_SERVER = ""
SMTP_PORT = 587
SMTP_USERNAME = ""
SMTP_PASSWORD = ""

ENABLE_DISCORD_ALERTS = False
DISCORD_WEBHOOK_URL = ""

# =============================================================================
# PORTFOLIO ALLOCATION (1/N Strategy - DeMiguel et al.)
# =============================================================================
# Core Holdings - 60% total (max 25% per position)
# CRITICAL: No position may exceed 25% at time of purchase
CORE_POSITIONS = {
    'VOO': 0.25,  # Vanguard S&P 500 - 25% (max allowed)
    'VTI': 0.20,  # Vanguard Total Market - 20%
    'VEA': 0.15,  # Vanguard Developed Markets - 15%
}

# Satellite Buckets (8 buckets × 1 position each = 40% total)
# Structural diversification: exactly 1 slot per bucket (1/N across themes)
SATELLITE_BUCKETS = {
    'A_SPACE': ['ROKT', 'UFO', 'RKLB', 'PL', 'ASTS', 'LUNR', 'ONDS', 'RDW'],
    'B_DEFENSE': ['PPA', 'ITA', 'XAR', 'JEDI', 'LMT', 'NOC', 'RTX', 'GD', 'KTOS', 'AVAV'],
    'C_SEMIS': ['SMH', 'SOXX', 'ASML', 'AMAT', 'LRCX', 'KLAC', 'TER', 'ENTG', 'NVDA', 'AMD'],
    'D_BIOTECH': ['XBI', 'IDNA', 'CRSP', 'NTLA', 'BEAM', 'VRTX'],
    'E_NUCLEAR': ['URNM', 'URA', 'NLR', 'CCJ'],
    'F_ENERGY': ['XLE', 'XOP', 'XOM', 'CVX'],
    'G_METALS': ['COPX', 'XME', 'PICK', 'FCX', 'SCCO'],
    'H_MATERIALS': ['XLB', 'VAW', 'DMAT', 'LIN', 'APD', 'ECL'],  # EXPANDED: Added XLB, VAW, LIN, APD, ECL
}

# ETFs per bucket (for volatility kill-switch fallback)
BUCKET_ETFS = {
    'A_SPACE': ['ROKT', 'UFO'],
    'B_DEFENSE': ['PPA', 'ITA', 'XAR'],
    'C_SEMIS': ['SMH', 'SOXX'],
    'D_BIOTECH': ['XBI', 'IDNA'],
    'E_NUCLEAR': ['URNM', 'URA', 'NLR'],
    'F_ENERGY': ['XLE', 'XOP'],
    'G_METALS': ['COPX', 'XME', 'PICK'],
    'H_MATERIALS': ['XLB', 'VAW', 'DMAT'],  # EXPANDED: XLB and VAW are liquid ETFs
}

# Day-1 satellite lineup (1 per bucket - structural diversification)
DAY1_SATELLITES = [
    ('ROKT', 'A_SPACE'),      # Space ETF
    ('PPA', 'B_DEFENSE'),     # Defense ETF
    ('SMH', 'C_SEMIS'),       # Semiconductors ETF
    ('XBI', 'D_BIOTECH'),     # Biotech ETF
    ('URNM', 'E_NUCLEAR'),    # Nuclear ETF
    ('XLE', 'F_ENERGY'),      # Energy ETF
    ('COPX', 'G_METALS'),     # Metals ETF
    ('DMAT', 'H_MATERIALS'),  # Materials ETF
]

# SPRINT MODE: Increase satellite allocation to deploy cash faster
SATELLITE_POSITION_SIZE = 0.05 if not SPRINT_MODE_ENABLED else 0.04  # 4% in sprint (allows more positions)
MAX_PER_BUCKET = 1 if not SPRINT_MODE_ENABLED else 2  # Allow 2 per bucket in sprint mode
MIN_BUCKETS = 8                 # Must have all 8 buckets represented

# Volatility kill-switch threshold (DeMiguel-consistent risk control)
# If single-name satellite VOL21 > 6% daily stdev, replace with bucket ETF
VOLATILITY_KILL_SWITCH_THRESHOLD = 0.06

# =============================================================================
# VIX REGIME PARAMETERS
# =============================================================================
REGIME_PARAMS = {
    'NORMAL': {       # VIX < 20
        'max_satellites': 12 if SPRINT_MODE_ENABLED else 8,  # More satellites in sprint
        'weekly_replacement_cap': 99 if SPRINT_MODE_ENABLED else 2,  # Unlimited in sprint
        'stop_loss_pct': 0.10,  # TIGHTENED: 10% stop-loss (was 15%)
        'max_satellite_pct': 0.50 if SPRINT_MODE_ENABLED else 0.40,  # Higher allocation
    },
    'CAUTION': {      # 20 <= VIX <= 30
        'max_satellites': 8 if SPRINT_MODE_ENABLED else 6,
        'weekly_replacement_cap': 99 if SPRINT_MODE_ENABLED else 1,
        'stop_loss_pct': 0.10,  # TIGHTENED: 10% (was 12%)
        'max_satellite_pct': 0.40 if SPRINT_MODE_ENABLED else 0.30,
    },
    'SHOCK': {        # VIX > 30
        'max_satellites': 6 if SPRINT_MODE_ENABLED else 4,
        'weekly_replacement_cap': 2 if SPRINT_MODE_ENABLED else 0,  # Allow some buys
        'stop_loss_pct': 0.08,  # TIGHTENED: 8% (was 10%)
        'max_satellite_pct': 0.30 if SPRINT_MODE_ENABLED else 0.20,
    },
}

# =============================================================================
# EVENT FREEZE DATES (No new positions during high-volatility events)
# =============================================================================
EVENT_FREEZE_DATES = [
    datetime(2026, 1, 27).date(),  # FOMC Day 1
    datetime(2026, 1, 28).date(),  # FOMC Day 2
    datetime(2026, 1, 29).date(),  # Post-FOMC
]

# =============================================================================
# PROHIBITED SECURITIES (NEVER TRADE THESE)
# =============================================================================
PROHIBITED_TICKERS = [
    # Leveraged ETFs (2x, 3x)
    'TQQQ', 'SQQQ', 'UPRO', 'SPXU', 'SOXL', 'SOXS', 'LABU', 'LABD',
    'FNGU', 'FNGD', 'TECL', 'TECS', 'FAS', 'FAZ', 'TNA', 'TZA',
    'UDOW', 'SDOW', 'URTY', 'SRTY', 'UCO', 'SCO', 'BOIL', 'KOLD',
    # Inverse ETFs
    'SH', 'PSQ', 'DOG', 'RWM', 'SDS', 'QID', 'DXD',
    # Crypto ETFs
    'BITO', 'GBTC', 'ETHE', 'ARKB', 'IBIT', 'FBTC',
]

PROHIBITED_SUFFIXES = ['.PK', '.OB', '.TO', '.L', '.AX']  # OTC/Foreign

# =============================================================================
# WATCHLIST / UNIVERSE (Canonical, deduplicated)
# =============================================================================
# These define the allowed universe for satellite positions.
# A ticker must be in WATCHLIST AND pass regime + SMA + volatility + bucket rules.
# Keep equities and ETFs separate to prevent logic bugs.

WATCHLIST_EQUITIES = [
    "AMD",      # AI / Semis
    "AMZN",     # Mega-cap / Growth
    "ASTS",     # Space / High-beta
    "DDOG",     # AI / Growth
    "GOOGL",    # Mega-cap (Class A, more liquid)
    "HOOD",     # Fintech
    "IREN",     # Speculative / Narrative
    "LLY",      # Healthcare / Defensive
    "META",     # Mega-cap / Growth
    "MSFT",     # Mega-cap / AI
    "MU",       # AI / Semis
    "NBIS",     # Speculative / Narrative
    "NVO",      # Healthcare / Defensive
    "NVDA",     # AI / Semis
    "PLTR",     # AI / High-beta
    "RKLB",     # Space / High-beta
    "RDDT",     # Momentum / High-beta
    "STX",      # Tech / Storage
    "SYF",      # Financials
    "TSLA",     # Momentum / High-beta
    "UNH",      # Healthcare / Defensive
    "VRT",      # AI / Infra
    "VRTX",     # Biotech / Defensive
    "WMT",      # Mega-cap / Stabilizer
]

WATCHLIST_ETFS = [
    "QQQ",      # Nasdaq 100
    "VOO",      # S&P 500
    "VT",       # Total World
    "XLV",      # Healthcare Sector
]

# Combined watchlist (for simple membership checks)
WATCHLIST_ALL = WATCHLIST_EQUITIES + WATCHLIST_ETFS

# =============================================================================
# ALLOWED EXCHANGES
# =============================================================================
ALLOWED_EXCHANGES = ['NYSE', 'NASDAQ', 'AMEX']

# =============================================================================
# DERIVED VALUES (Computed from above)
# =============================================================================
def get_all_satellite_tickers():
    """Get all possible satellite tickers from all buckets"""
    tickers = []
    for bucket_tickers in SATELLITE_BUCKETS.values():
        tickers.extend(bucket_tickers)
    return list(set(tickers))

def get_all_tickers():
    """Get all tickers we need to monitor"""
    return list(CORE_POSITIONS.keys()) + get_all_satellite_tickers()

def get_bucket_for_ticker(ticker):
    """Find which bucket a ticker belongs to"""
    for bucket_name, bucket_tickers in SATELLITE_BUCKETS.items():
        if ticker in bucket_tickers:
            return bucket_name
    return None


def is_in_watchlist(ticker: str, equity_only: bool = False) -> bool:
    """Check if ticker is in the approved watchlist.

    Args:
        ticker: The ticker symbol to check
        equity_only: If True, only check WATCHLIST_EQUITIES (exclude ETFs)

    Returns:
        True if ticker is in the watchlist, False otherwise
    """
    if equity_only:
        return ticker.upper() in WATCHLIST_EQUITIES
    return ticker.upper() in WATCHLIST_ALL


def is_watchlist_etf(ticker: str) -> bool:
    """Check if ticker is specifically a watchlist ETF."""
    return ticker.upper() in WATCHLIST_ETFS


# =============================================================================
# RUNTIME MODE FLAGS (Set by CLI, not directly)
# =============================================================================
DRY_RUN_MODE = False    # If True, never submit orders (test mode)
SAFE_MODE = False       # If True, max 5 shares, ETFs only, fail on any error
SAFE_MODE_MAX_SHARES = 5
SAFE_MODE_ETF_WHITELIST = [
    'VOO', 'VTI', 'VEA',  # Core ETFs
    'ROKT', 'UFO',        # Space ETFs
    'PPA', 'ITA', 'XAR',  # Defense ETFs
    'SMH', 'SOXX',        # Semiconductor ETFs
    'XBI', 'IDNA',        # Biotech ETFs
    'URNM', 'URA', 'NLR', # Nuclear ETFs
    'XLE', 'XOP',         # Energy ETFs
    'COPX', 'XME', 'PICK', # Metals ETFs
    'DMAT',               # Materials ETF
    'SPY', 'QQQ', 'IWM',  # Additional liquid ETFs
]
