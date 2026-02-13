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

# 5. On January 20 at 9:45 AM ET - Build initial portfolio
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
| `python main.py --sprint3` | Execute SPRINT3 mode (high-intensity catch-up) |
| `python main.py --sprint3-status` | Show SPRINT3 status |
| `python main.py --sprint3-dry-run` | Plan SPRINT3 trades without executing |
| `python main.py --sprint3-reset` | Reset SPRINT3 state |

## Competition Rules (Hard Constraints)

- **Max 80 trades total** for the entire competition
- **Max 25% per position** at time of purchase (CRITICAL: No position may exceed 25% at buy time)
- **Min 4 holdings** at all times
- **No stocks below $5**
- **24-hour minimum holding period** (cannot sell until 24h after buy)
- **No leveraged/inverse/crypto ETFs**

## Strategy

### Portfolio Structure (60/40)
- **Core (60%)**: VOO (25%), VTI (20%), VEA (15%)
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

## SPRINT3 Mode - End-of-Competition Catch-Up Strategy

SPRINT3 is a high-intensity 3-day trading mode designed for end-of-competition catch-up scenarios. It uses up to 65 trades to aggressively rotate a momentum-based satellite portfolio.

### SPRINT3 Strategy

**Portfolio Structure:**
- **Core (60%)**: VOO 25%, VTI 20%, VEA 15% - stable, low turnover
- **Satellites (40%)**: 16 positions at 2.5% each - rotated daily for momentum capture

**Key Constraints (enforced):**
- Max 65 trades in sprint (leaves 5 trade buffer)
- 24-hour minimum hold period (enforced with 2-minute buffer)
- Market hours only (9:30 AM - 4:00 PM ET)
- BUY price >= $5 (use $6 safety buffer)
- Max 25% per position
- Min 4 holdings at all times

### SPRINT3 Execution Plan

| Day | Action | Trades |
|-----|--------|--------|
| Day 1 | Build core + 16 satellites | ~19 trades |
| Day 2 | Rotate ALL 16 satellites | 32 trades |
| Day 3 | Rotate remaining budget | Up to 14 trades |

**CRITICAL: Execute only in the 9:40-10:05 AM ET window!**

Trading in a consistent morning window (09:40-10:05 AM ET) provides stable execution.
The bot enforces 24-hour + buffer holding period using actual timestamps.

### SPRINT3 Scoring Algorithm

For each satellite candidate, we compute:

```
ForecastScore = 0.55 * rr3 + 0.35 * rr10 - 0.25 * vol10

Where:
- rr3 = 3-day return relative to VOO
- rr10 = 10-day return relative to VOO
- vol10 = 10-day volatility (standard deviation)
```

**Trend Filter**: Only buy if Close > SMA20 AND SMA20 > SMA50

**Universe**: SMH, SOXX, XLK, NVDA, AMD, AVGO, ASML, AMAT, LRCX, KLAC, PPA, ITA, XAR, LMT, NOC, RTX, GD, KTOS, AVAV, XLE, XOP, XOM, CVX, COPX, XME, PICK, FCX, SCCO, URA, URNM, NLR, CCJ, XBI, IDNA, CRSP, NTLA, BEAM, UFO, ROKT, RKLB, ASTS, LUNR, RDW

### SPRINT3 Runbook

**Day 1 (Build):**
```bash
# 1. Check status first
python main.py --sprint3-status

# 2. Dry run to see planned trades
python main.py --sprint3-dry-run

# 3. At 9:45 AM ET, execute
python main.py --sprint3

# 4. Type "SPRINT1" to confirm
```

**Day 2 (Full Rotation):**
```bash
# At 9:45 AM ET (24h+ after Day 1)
python main.py --sprint3

# Type "SPRINT2" to confirm
```

**Day 3 (Final Rotation):**
```bash
# At 9:45 AM ET (24h+ after Day 2)
python main.py --sprint3

# Type "SPRINT3" to confirm
```

### SPRINT3 Safety Checks

Before executing any sprint day:
1. Verify market is OPEN (check for "market closed" banner)
2. Verify you're in the 9:40-10:05 AM ET window
3. Run `--sprint3-dry-run` to review planned trades
4. Confirm trade count is under budget

If something goes wrong:
- Use `--sprint3-reset` to reset sprint state
- Trades already made will NOT be reversed
- State file is backed up before each save

### SPRINT3 Files

```
stocktrak_bot/
├── sprint3_strategy.py  # Sprint3 algorithm and execution
├── state/
│   └── bot_state.json   # Includes sprint3 state
└── logs/
    └── sprint3_*.png    # Sprint execution screenshots
```

---

**Team 9 - Good luck!**
