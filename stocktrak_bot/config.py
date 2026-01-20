"""
StockTrak Bot Configuration - TEAM 9
Morgan Stanley UWT Milgard Competition 2026

WARNING: This file contains live credentials.
DO NOT COMMIT TO PUBLIC REPOSITORIES.
For production, consider using environment variables.
"""

from datetime import datetime

# =============================================================================
# STOCKTRAK CREDENTIALS
# =============================================================================
STOCKTRAK_URL = "https://app.stocktrak.com"
STOCKTRAK_LOGIN_URL = "https://app.stocktrak.com/login"
STOCKTRAK_USERNAME = "SMC Team 9"
STOCKTRAK_PASSWORD = "T9bKx3"
SESSION_ID = "355677"

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
MIN_HOLD_TRADING_DAYS = 2       # T+2 holding period (cannot sell until T+2)
CASH_INTEREST_RATE = 0.01       # 1% annual on cash

# =============================================================================
# MARKET HOURS (Eastern Time)
# =============================================================================
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"
EXECUTION_WINDOW_START = "15:50"  # 3:50 PM ET
EXECUTION_WINDOW_END = "15:59"    # 3:59 PM ET

# =============================================================================
# BOT SETTINGS
# =============================================================================
HEADLESS_MODE = True              # Set False to see browser during testing
EXECUTION_TIME = "15:55"          # 3:55 PM ET - primary execution time
DATA_COLLECTION_TIME = "15:30"    # 3:30 PM ET - data collection time
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
# PORTFOLIO ALLOCATION
# =============================================================================
# Core Holdings (always maintain these) - 60% total
CORE_POSITIONS = {
    'VOO': 0.35,  # Vanguard S&P 500 - 35%
    'VTI': 0.15,  # Vanguard Total Market - 15%
    'VEA': 0.10,  # Vanguard Developed Markets - 10%
}

# Satellite Buckets (pick 8 positions, ~5% each) - 40% total
SATELLITE_BUCKETS = {
    'A_SPACE': ['RKLB', 'PL', 'ASTS', 'LUNR', 'UFO', 'ROKT'],
    'B_DEFENSE': ['ITA', 'PPA', 'XAR', 'LMT', 'NOC', 'RTX', 'GD', 'KTOS', 'AVAV'],
    'C_SEMIS': ['SMH', 'SOXX', 'ASML', 'AMAT', 'LRCX', 'KLAC'],
    'D_BIOTECH': ['XBI', 'IDNA', 'CRSP', 'NTLA', 'BEAM'],
    'E_NUCLEAR': ['URA', 'URNM', 'NLR', 'CCJ'],
    'F_ENERGY': ['XLE', 'XOP', 'XOM', 'CVX'],
    'G_METALS': ['XME', 'COPX', 'PICK', 'FCX', 'SCCO'],
    'H_MATERIALS': ['DMAT'],
}

# Day-1 satellite lineup (pre-selected based on macro analysis)
DAY1_SATELLITES = [
    ('SMH', 'C_SEMIS'),
    ('ITA', 'B_DEFENSE'),
    ('URA', 'E_NUCLEAR'),
    ('COPX', 'G_METALS'),
    ('XLE', 'F_ENERGY'),
    ('RKLB', 'A_SPACE'),
    ('XBI', 'D_BIOTECH'),
    ('LMT', 'B_DEFENSE'),
]

SATELLITE_POSITION_SIZE = 0.05  # 5% per satellite
MAX_PER_BUCKET = 2              # Max 2 satellites from same bucket
MIN_BUCKETS = 3                 # Must have at least 3 different buckets

# =============================================================================
# VIX REGIME PARAMETERS
# =============================================================================
REGIME_PARAMS = {
    'NORMAL': {       # VIX < 20
        'max_satellites': 8,
        'weekly_replacement_cap': 2,
        'stop_loss_pct': 0.15,
        'max_satellite_pct': 0.40,
    },
    'CAUTION': {      # 20 <= VIX <= 30
        'max_satellites': 6,
        'weekly_replacement_cap': 1,
        'stop_loss_pct': 0.12,
        'max_satellite_pct': 0.30,
    },
    'SHOCK': {        # VIX > 30
        'max_satellites': 4,
        'weekly_replacement_cap': 0,  # No new buys
        'stop_loss_pct': 0.10,
        'max_satellite_pct': 0.20,
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
