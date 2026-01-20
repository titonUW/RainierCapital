# StockTrak Automated Trading Bot - Team 9

Morgan Stanley UWT Milgard Stock Market Competition 2026

## Overview

This bot automates trading on app.stocktrak.com according to a rules-based algorithmic strategy. It runs from January 20 to February 20, 2026.

## Quick Start

```bash
# 1. Navigate to the bot directory
cd stocktrak_bot

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 4. Test the bot (verify login works)
python main.py --test

# 5. On January 20 at 3:50 PM ET - Build initial portfolio
python main.py --day1

# 6. Start continuous operation
python main.py
```

## Commands

| Command | Description |
|---------|-------------|
| `python main.py` | Start scheduler (normal operation) |
| `python main.py --test` | Test login and data fetching |
| `python main.py --day1` | Execute Day-1 portfolio build |
| `python main.py --manual` | Run daily routine manually |
| `python main.py --status` | Show current bot status |
| `python main.py --scores` | Show satellite scoring report |

## Competition Rules (Hard Constraints)

- **Max 80 trades total** for the entire competition
- **Max 25% per position** at time of purchase
- **Min 4 holdings** at all times
- **No stocks below $5**
- **T+2 holding period** (can't sell until 2 trading days after purchase)
- **No leveraged/inverse/crypto ETFs**

## Strategy

### Portfolio Structure (60/40)
- **Core (60%)**: VOO (35%), VTI (15%), VEA (10%)
- **Satellites (40%)**: 8 positions at ~5% each from thematic buckets

### Thematic Buckets
- Space (RKLB, PL, ASTS, LUNR)
- Defense (ITA, LMT, NOC, RTX)
- Semiconductors (SMH, SOXX, ASML)
- Biotech (XBI, CRSP, NTLA)
- Nuclear (URA, URNM, CCJ)
- Energy (XLE, XOP, XOM)
- Metals (XME, COPX, FCX)

### Entry/Exit Rules
- **Entry**: Uptrend + Double-7 Low + Top 12 momentum score
- **Exit**: Stop-loss (10-15%), trend break, or profit-taking at Double-7 High

### Regime Detection
- **VIX < 20**: Normal mode (8 satellites, 15% stops)
- **VIX 20-30**: Caution mode (6 satellites, 12% stops)
- **VIX > 30**: Shock mode (4 satellites, 10% stops, no new buys)

## Files

```
stocktrak_bot/
├── main.py           # Entry point
├── config.py         # Credentials and settings
├── stocktrak_bot.py  # Browser automation
├── market_data.py    # yfinance integration
├── state_manager.py  # State persistence
├── scheduler.py      # Task scheduling
├── daily_routine.py  # Trading logic
├── scoring.py        # Satellite selection
├── validators.py     # Pre-trade validation
├── utils.py          # Helper functions
├── requirements.txt  # Dependencies
└── logs/             # Logs and screenshots
```

## Important Dates

- **Jan 20, 2026**: Competition starts - execute Day-1 build
- **Jan 27-29, 2026**: FOMC meeting - no new positions (event freeze)
- **Feb 20, 2026**: Competition ends

## Monitoring

- Check `logs/trading_bot.log` for daily activity
- Check `logs/trades.log` for trade history
- Run `python main.py --status` to see current state
- Screenshots are saved to `logs/` for debugging

## Troubleshooting

1. **Login fails**: Check screenshots in `logs/`, verify credentials in `config.py`
2. **Market data unavailable**: yfinance may be rate-limited, wait and retry
3. **Bot not executing**: Verify PC isn't sleeping, check scheduler logs
4. **Trade rejected**: Check screenshots, verify position limits

## Safety Features

- Hard stop at 70 trades (only emergency exits after)
- Automatic state backup before each save
- Screenshot capture on every trade attempt
- Comprehensive logging
- Sync with StockTrak data at each execution

---

**Team 9 - Good luck!**
