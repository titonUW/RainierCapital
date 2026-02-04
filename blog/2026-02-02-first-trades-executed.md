# Building an Automated Trading Bot: Lessons from the Trenches

**Date:** February 2, 2026
**Author:** SMC Team 9 - UWT Milgard School of Business
**Competition:** Morgan Stanley Investment Challenge 2026

---

## The Moment of Truth

At 6:59 PM on February 2nd, 2026, after countless debugging sessions and three complete rewrites of our trade execution pipeline, we watched our terminal display the words we'd been waiting for:

```
DAY-1 BUILD COMPLETE
Trades executed: 6
Trades remaining: 74
```

Our portfolio now holds six positions totaling approximately $500,000 in assets. The automated trading bot we spent weeks building had finally executed its first real trades.

---

## What We Built

Our team set out to build an automated trading system for the Morgan Stanley Investment Challenge—a four-week competition where university teams manage a simulated $1,000,000 portfolio on StockTrak. Our approach was ambitious: rather than manually execute trades, we would build a fully automated system that could:

- **Analyze market conditions** (VIX regime, trend indicators, price momentum)
- **Manage a diversified portfolio** following the DeMiguel 1/N equal-weight strategy
- **Execute trades autonomously** through browser automation
- **Maintain detailed logs** for compliance and rubric scoring

The technical stack includes:
- **Python** for core logic
- **Playwright** for browser automation
- **yfinance** for market data
- **A custom state machine** for stall-proof trade execution

### Our Portfolio Strategy

We implemented a "Core + Satellite" approach:
- **60% Core Holdings**: VOO (S&P 500), VTI (Total Market), VEA (Developed Markets)
- **40% Satellite Holdings**: Thematic ETFs across 8 buckets (Space, Defense, Semiconductors, Biotech, Nuclear, Energy, Metals, Materials)

Today's successful trades established our initial positions:

| Ticker | Shares | Entry Price | Allocation |
|--------|--------|-------------|------------|
| VOO    | 156    | $640.96     | ~20%       |
| VTI    | 292    | $343.16     | ~20%       |
| VEA    | 1505   | $66.59      | ~20%       |
| ROKT   | 251    | $99.78      | ~5%        |
| PPA    | 143    | $174.08     | ~5%        |
| SMH    | 61     | $408.79     | ~5%        |

---

## What Went Well

### 1. The Architecture Paid Off

Our decision to build a finite state machine for trade execution proved invaluable. Every trade moves through defined states:

```
INIT → LOGGED_IN → ON_TRADE_PAGE → FORM_FILLED → PREVIEWED → PLACED → VERIFIED
```

When something fails (and things *always* fail), the system knows exactly where it stopped and can recover intelligently.

### 2. Comprehensive Logging

Every action, every screenshot, every error is logged. When debugging why "Confirm Order" wasn't being clicked, we had complete visibility into what the bot was seeing. This turned multi-hour debugging sessions into 15-minute fixes.

### 3. Idempotency Protection

Our state manager prevents double-orders. Even if the bot crashes mid-execution and restarts, it won't re-place an order that already went through. In a competition with an 80-trade lifetime limit, this protection is crucial.

### 4. Automated Trade Notes

StockTrak requires trade notes for every order. Our bot automatically generates them:

> "VOO - Vanguard S&P 500 ETF. Buying 156 shares (20.0% of portfolio). Day-1 core position build per 1/N strategy."

This satisfies the compliance requirement while documenting our investment rationale for the rubric.

---

## The Struggles

### The Button That Wouldn't Click

Our biggest challenge? Clicking a button. Specifically, the "Confirm Order" button.

StockTrak's web interface is... challenging for automation:
- Dynamic elements that appear and disappear
- Overlay ads that block clicks
- Buttons that exist in the DOM but aren't visible
- Network requests that never complete (breaking `wait_for_load_state`)

We tried:
- **Playwright's built-in selectors** → Buttons weren't found
- **XPath queries** → Found pagination buttons instead
- **Role-based selection** → Timeouts
- **Force clicks** → Clicked invisible elements

The solution? **Raw JavaScript injection**:

```javascript
const clickables = document.querySelectorAll('button, a, [role="button"]');
for (const element of clickables) {
    const text = element.textContent.toLowerCase();
    if (text.includes('confirm order')) {
        element.scrollIntoView({behavior: 'instant', block: 'center'});
        element.click();
        return {success: true};
    }
}
```

By bypassing Playwright's selector engine entirely and using native DOM methods, we finally achieved reliable button clicks.

### The Hang After Success

After successfully placing our first order (VOO, 156 shares), the bot hung. The order went through—we could see it in the portfolio—but our execution pipeline froze at "verify_history."

The culprit: navigation via hover menus. StockTrak's dropdown menus don't work reliably with automation. The fix was simple but non-obvious: use direct URLs instead of clicking through menus:

```python
# Instead of hovering "My Portfolio" and clicking "Transaction History"
url = "https://app.stocktrak.com/portfolio/transactionhistory"
self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
```

### The Trade Notes Field

Midway through testing, StockTrak started requiring trade notes for every order. Our bot would fill the form, click "Review Order," but then fail at "Confirm Order" because a required field was empty.

We added `_fill_trade_notes()` to programmatically populate the notes textarea before confirmation.

### DMAT: The One That Got Away

Our logs show repeated failures trying to buy DMAT (the Materials ETF). After three attempts, the pipeline correctly aborted to prevent resource exhaustion. We'll investigate whether this is a ticker-specific issue or a timing problem.

---

## Obstacles We Overcame

| Problem | Solution |
|---------|----------|
| `networkidle` never triggers | Switch to `domcontentloaded` |
| Overlay ads block clicks | Aggressive overlay dismissal with `force=True` |
| Buttons not in viewport | `scrollIntoView()` before every click |
| Session expiration mid-trade | Auto-detect login redirects and re-authenticate |
| Hover menus unreliable | Direct URL navigation |
| Trade notes required | Auto-generate notes with ticker description |
| Double-order risk | Idempotency checks via StateManager |

---

## Predicted Future Challenges

### 1. Market Volatility Response

With VIX currently elevated and oil/gas showing significant moves (Nat Gas -24.85% as of today), our regime detection system will be tested. We need to ensure:
- Stop-loss triggers fire correctly
- VIX regime transitions (NORMAL → CAUTION → SHOCK) are smooth
- Position sizing adjusts appropriately

### 2. Trade Budget Management

We've used 6 of our 80 lifetime trades. With 14 trading days remaining:
- **Budget**: 74 trades ÷ 14 days = ~5.3 trades/day maximum
- **Reality**: We need to be conservative. Our strategy calls for minimal turnover—risk exits only on non-Fridays, discretionary rotation only on Fridays.

### 3. Session Stability

Our bot runs on a Windows machine that needs to stay awake and connected for 2+ weeks. Power outages, Windows updates, and network hiccups are real risks. We've implemented:
- Auto-restart on crash (up to 5 attempts)
- Keep-awake signals every 60 seconds
- Persistent browser profiles (no daily re-login)

### 4. StockTrak Platform Changes

StockTrak could change their UI at any moment. A single CSS class rename could break our selectors. Our JavaScript-based clicking is more resilient, but we're not immune.

---

## Adapting to a Late Start

Here's the uncomfortable truth: we're starting 2 weeks into a 4-week competition. Half the time is gone, and until today, we held $1,000,000 in cash—earning nothing while the market moved.

### The Opportunity Cost

The S&P 500 is up roughly 3% since competition start. Our cash position missed that entirely. We're effectively starting at a -3% handicap versus teams who deployed capital immediately.

### Our Adjusted Strategy

1. **Rapid Initial Deployment** (Today - Done)
   - Get core positions established immediately
   - Don't wait for "perfect" entry points
   - Accept that we're buying at current market prices

2. **Minimal Turnover** (Weeks 3-4)
   - Our 1/N strategy actually benefits from inactivity
   - Only trade on risk triggers (stop-loss, price violations)
   - Preserve remaining 74 trades for essential exits

3. **Leverage Our Automation Advantage**
   - Other teams may miss trading windows
   - Our bot executes at exactly 9:30 AM ET every market day
   - Consistent execution compounds over time

4. **Focus on Rubric Points**
   - Risk management documentation (automated via trade notes)
   - Clear investment rationale (our 1/N strategy is academically grounded)
   - Minimal trading costs (market orders, consolidated positions)

### The Math

With $500,000 deployed across 6 positions and 2 weeks remaining:
- If the market returns 2% over the next 2 weeks, we capture ~$10,000
- Our 1/N diversification should track market returns closely
- The 40% satellite allocation provides upside exposure to growth themes

We may not win on raw returns, but our systematic approach, documented process, and risk management should score well on the rubric's qualitative factors.

---

## Lessons Learned

1. **Browser automation is harder than it looks.** What seems like "click this button" becomes a multi-day debugging session when the target is a dynamically-rendered SPA with ad overlays.

2. **Build for failure.** Every function needs timeouts. Every step needs retries. Every state needs recovery logic. The happy path is the exception, not the rule.

3. **Logs are your lifeline.** When something fails at 3 AM, you're not there to see it. Comprehensive logging is the only way to diagnose problems after the fact.

4. **Start simple, add complexity.** Our first execution pipeline was 400 lines. The current one is 1,200 lines. Each addition came from a real failure, not speculation.

5. **JavaScript solves selector problems.** When Playwright's selectors fail, drop down to raw DOM manipulation. It's less elegant but more reliable.

---

## What's Next

The bot is scheduled to run at 9:30 AM ET every market day. Tomorrow's execution will test whether our system can:
- Handle a portfolio with existing positions
- Skip trades that aren't needed
- Correctly identify risk triggers

We'll be watching the logs closely for the next few days. After that, the goal is simple: trust the automation and let the 1/N strategy do its work.

---

*The bot executed successfully. Now we wait.*

```
BOT STATUS
==========
Trades Used: 6/80
Trades Remaining: 74
Week Replacements: 0
Positions: 6
Last Execution: 2026-02-02
Portfolio Value: $500,424.84
```

---

**Team 9 - University of Washington Tacoma**
*Milgard School of Business*
*Morgan Stanley Investment Challenge 2026*
