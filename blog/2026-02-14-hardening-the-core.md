# Hardening the Core: 12 Days of Battle-Testing Our Trading Bot

**Date:** February 14, 2026
**Author:** SMC Team 9 - UWT Milgard School of Business
**Competition:** Morgan Stanley Investment Challenge 2026

---

## Two Weeks In: The Bot Evolved

When we last wrote on February 2nd, we had just executed our first 6 trades. Today—Valentine's Day and exactly one week before the competition ends—our trading bot has transformed from a fragile prototype into a hardened production system.

The numbers tell the story:

```
BOT STATUS (Feb 14, 2026)
=========================
Trades Used: 10/80
Days Remaining: 6 trading days
Error Count: 0 unrecovered failures
```

But numbers don't capture what we learned. This post documents the critical vulnerabilities we discovered—and fixed—before they cost us the competition.

---

## The Five Critical Bugs That Almost Broke Us

After our February 2nd deployment, the bot ran daily without manual intervention. But as we reviewed logs and edge cases, we discovered five serious issues that could have caused compliance violations or duplicate trades. Last night, we deployed fixes for all of them.

### Bug #1: The Day-1 Continuation Loophole

**The Problem:**
Our "Day-1 continuation" logic was meant to let the bot finish building the initial portfolio if Day-1 execution was interrupted. But it had no expiration date—the bot would try to "continue" the Day-1 build *any* day it detected missing satellite buckets.

**Why It Matters:**
If we sold a satellite on Day 10 (creating an "empty bucket"), the bot would try to auto-fill it on Day 11—but we're supposed to only do discretionary buys on Fridays. This violated our own trading rules.

**The Fix:**
```python
# Now checks explicit Day-1 deadline
day1_deadline = datetime.fromisoformat(config.COMPETITION_START).date()
if today > day1_deadline:
    logger.warning("Past Day-1 deadline - will NOT auto-fill buckets")
    need_day1_continuation = False
```

Day-1 continuation now only activates on the actual first day of competition.

---

### Bug #2: Old Positions Became Unsellable on Restart

**The Problem:**
When the bot starts, it migrates position data to our new lot-based tracking system. For positions without historical buy timestamps, we created "synthetic lots"—and we timestamped them with `datetime.now()`.

This meant: restart the bot on Day 15, and positions you've held since Day 1 suddenly have a "buy timestamp" of today. The 24-hour hold rule kicks in, and you can't sell them for 24 hours.

**Why It Matters:**
A morning restart could block all our stop-loss exits for the entire trading day. In a volatile market, that's catastrophic.

**The Fix:**
```python
# Use entry_date + 25 hours (guarantees it's past 24h hold)
entry_dt = datetime.fromisoformat(entry_date_str)
synthetic_ts = (entry_dt + timedelta(hours=25)).isoformat()
```

Synthetic lots now use the position's original entry date plus 25 hours—guaranteeing they're immediately sellable.

---

### Bug #3: HOLD_MODE Configuration Mismatch

**The Problem:**
We support two holding period modes:
- `LOT_FIFO`: Each buy creates a timestamped lot; sells consume oldest lots first
- `STRICT_TICKER`: Any buy within 24h blocks ALL sells for that ticker

The config file could say `STRICT_TICKER` while the actual positions had multiple lots—a logical inconsistency that could cause unexpected behavior.

**Why It Matters:**
If we changed modes mid-competition, the validation logic and actual state would diverge. Sells might be allowed when they shouldn't be (or vice versa).

**The Fix:**
```python
def _validate_hold_mode_consistency(self):
    if HOLD_MODE == "STRICT_TICKER" and multi_lot_tickers:
        logger.warning("HOLD_MODE is STRICT_TICKER but positions have multiple lots")
        logger.warning("Consider switching to LOT_FIFO for proper per-lot tracking")
```

The bot now validates configuration consistency at startup and warns about mismatches.

---

### Bug #4: Race Condition on Simultaneous Execution

**The Problem:**
Our `already_executed_today()` check and `mark_execution()` were separate function calls:

```python
# BEFORE (vulnerable)
if state.already_executed_today():
    return  # Skip execution
# ... 200 lines of trading logic ...
state.mark_execution()  # Mark at the end
```

If two bot instances started within milliseconds of each other, both could pass the check before either marked execution. Result: duplicate trades.

**Why It Matters:**
Scheduled execution + manual execution on the same day could place duplicate orders. With an 80-trade lifetime limit, wasting trades on duplicates is unacceptable.

**The Fix:**
```python
def check_and_mark_execution(self) -> bool:
    """Atomically check and mark using file lock."""
    with _state_file_lock:
        if self.state.get('last_execution_date') == today:
            return False  # Already ran
        self.state['last_execution_date'] = today
        self.save()  # Save while holding lock
        return True
```

Check and mark are now atomic—one function, one lock, no race condition.

---

### Bug #5: Idempotency Relied Only on Local State

**The Problem:**
Our duplicate-order protection checked the local state file. But what if that file got corrupted between placing an order and recording it? The bot would re-place the same order.

**Why It Matters:**
State file corruption (disk error, power loss, crash mid-write) could cause a single trade to execute twice. StockTrak would process both. We'd burn two trades from our budget.

**The Fix:**
```python
# Get count BEFORE placing
pre_trade_count = bot.get_transaction_count()

# ... execute trade ...

# Verify count AFTER placing
post_trade_count = bot.get_transaction_count()
if post_trade_count != pre_trade_count + 1:
    logger.error("Transaction count mismatch - possible duplicate!")
```

We now cross-check against StockTrak's actual transaction history. Even if our state file burns to the ground, we can detect anomalies.

---

## The Lot-Based Holding System

The 24-hour minimum holding rule was our biggest compliance challenge. StockTrak requires that you hold a security for at least 24 hours before selling.

Our initial approach used `last_buy_timestamp`—but that's wrong. If you buy 100 shares Monday and 50 shares Tuesday, you should be able to sell the Monday shares on Tuesday afternoon. The Tuesday buy shouldn't block Monday's shares.

### Enter FIFO Lots

Each buy creates a timestamped "lot":

```python
lots = [
    {'lot_id': 'a1b2c3', 'qty': 100, 'buy_ts_utc': '2026-02-10T14:30:00Z'},
    {'lot_id': 'd4e5f6', 'qty': 50,  'buy_ts_utc': '2026-02-11T10:00:00Z'},
]
```

When selling, we consume from oldest lots first (FIFO):

```python
def eligible_sell_qty(self, ticker: str) -> int:
    """Return shares from lots older than 24h + buffer."""
    eligible = 0
    for lot in sorted_by_timestamp(lots):
        if lot_age >= 24_hours + buffer:
            eligible += lot['qty']
    return eligible
```

This lets us sell 100 shares on Feb 11th (the Monday lot) while the Tuesday lot remains locked.

---

## SPRINT3 Mode: The Catch-Up Strategy

With one week left and the competition heated, we built a high-intensity mode for the final days. SPRINT3 expands from 8 to 16 satellites—doubling our growth exposure for the home stretch.

Key features:
- Executes at market open (9:40 AM ET) for maximum volatility capture
- Uses momentum-weighted scoring instead of pure 1/N
- Aggressive position sizing (still within competition rules)
- Self-limiting trade budget to preserve end-of-competition exits

We haven't activated it yet. It's ready if we need to make up ground.

---

## The Execution Pipeline: Now 1,922 Lines

Our trade execution module has grown from 400 to nearly 2,000 lines. Every line addresses a real failure mode:

| Lines | Purpose |
|-------|---------|
| 1-130 | Circuit breaker pattern (stops execution after 3 consecutive failures) |
| 140-200 | State machine definitions (11 distinct states) |
| 200-420 | Main execute() flow with checkpoint verification |
| 420-600 | Step wrapper with retry logic and screenshot capture |
| 600-700 | Idempotency checks (local + external) |
| 700-1100 | Form filling with aggressive clearing and verification |
| 1100-1400 | JavaScript-based button clicking |
| 1400-1600 | Trade history verification |
| 1600-1922 | Utility functions, overlays, recovery |

Is it over-engineered? Maybe. But it hasn't failed yet.

---

## What We Learned (Part 2)

Building on our February 2nd lessons:

### 6. Timestamps are harder than they look

UTC vs local time. Naive vs aware datetimes. ISO format with 'Z' vs '+00:00'. We've been bitten by all of them. Every timestamp in our system now explicitly uses `timezone.utc`.

### 7. Atomic operations require actual atomicity

"Check then act" is never safe without a lock. Even single-user systems can have race conditions (scheduled tasks, manual runs, OS restarts).

### 8. External validation beats internal consistency

Your local state can lie. The source of truth is the external system. When possible, verify against it.

### 9. Migration code runs once but breaks forever

The lot migration ran once per position. We got it wrong initially, and positions were stuck with bad timestamps until we deployed the fix. Migration code needs the same rigor as core logic.

### 10. Circuit breakers save systems

After three consecutive failures, our bot stops trying and waits 5 minutes. This prevents runaway error loops that could blow through our trade budget on failed attempts.

---

## The Competition Home Stretch

Six trading days remain. Our position:

```
Portfolio Summary
=================
Core Holdings: ~$600,000 (VOO, VTI, VEA)
Satellites: ~$250,000 (8 positions across thematic buckets)
Cash: ~$150,000 (reserve for final adjustments)
Trade Budget: 70 trades remaining
```

### Our Final Week Strategy

1. **Minimal intervention** - Let the 1/N strategy ride
2. **Risk exits only** - Stop-losses fire automatically; no discretionary sells
3. **Friday rotation** - One final satellite optimization on Feb 21st (if needed)
4. **SPRINT3 activation** - Only if we're significantly behind with 2 days left

### The Rubric Focus

Raw returns matter, but so does process. Our automated trade notes document every decision:

> "XLE - Energy Select Sector SPDR Fund. Selling 87 shares (5.0% of portfolio). RISK EXIT: STOP_LOSS_15PCT triggered at -15.2% from entry."

Every trade has a rationale. Every exit has a documented trigger. This is what institutional trading looks like—and it should score well on qualitative factors.

---

## Final Thoughts

When we started, we thought the hard part would be building the trading strategy. We were wrong. The hard part was building a system that doesn't break at 3 AM on a Tuesday when StockTrak's servers are slow and Windows decides to install updates.

The bot we have today is unrecognizable from what we deployed two weeks ago:
- **Then:** 400-line execution pipeline, no lot tracking, manual restarts
- **Now:** 2,000-line hardened system, FIFO lot compliance, automatic recovery

Will we win? We don't know. But we've built something that works—reliably, autonomously, and correctly.

One week left. The bot runs at 9:30 AM. We watch the logs.

---

*Trust the system. Let the 1/N strategy compound.*

```
SYSTEM STATUS
=============
Execution Pipeline: OPERATIONAL
Circuit Breaker: CLOSED (0 consecutive failures)
HOLD_MODE: LOT_FIFO
Next Execution: 2026-02-18 09:30:00 ET (Monday - markets closed for holiday)
```

---

**Team 9 - University of Washington Tacoma**
*Milgard School of Business*
*Morgan Stanley Investment Challenge 2026*
