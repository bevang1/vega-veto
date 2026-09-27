# Vega Veto

Two paper-trading desks, fake money only:

- **Stock desk** (`stock_desk/`, `stock_run.py`): day-trades liquid US stocks
  in an Alpaca **paper** account on GitHub's servers every trading day, and
  posts P&L updates to your phone. [Jump to it](#stock-desk-us-stocks-alpaca-paper).
- **Crypto desk** (below): major coins (BTC, ETH, SOL, XRP, LTC, ...) on
  live Coinbase prices, with the Signal Iris dashboard.

**Crypto Paper Desk · Signal Iris**

A 24/7 crypto **paper trading** desk with a live dashboard. Four agents trade
**established, liquid coins only** (no memecoins, no fresh launches) with
**fake US dollars**, and explain every buy and sell. No exchange account, no
keys, no real money anywhere in this program.

The coins it watches (`UNIVERSE` in `paper_trader/config.py`):
**BTC, ETH, SOL, XRP, LTC, ADA, AVAX, LINK, DOT, BCH**, each traded against
USD on Coinbase's order books.

The point is to learn how the tool behaves, and to find out whether any
strategy makes money **after realistic costs**, before a cent is at risk.

## Run it (macOS)

No installs needed: it's Python standard library only.

Download it (once):

```bash
git clone https://github.com/bevang1/vega-veto.git
cd vega-veto
```

**Run it.** Real live prices, fake money. `caffeinate` keeps the Mac awake so
it can run overnight:

```bash
caffeinate -i python3 run.py
```

The dashboard opens at <http://127.0.0.1:8765>. The top-right badge should
read **LIVE DATA · PAPER MONEY** (green). It keeps running until you press
`Ctrl+C`. Agents can trade from the first minute: the feed loads the last
5 hours of one-minute candles on startup, so the indicators are warm.

Optional, **offline practice only**: `python3 run.py --demo` runs a
*simulated* market with made-up price paths (badge: SIMULATED MARKET, amber).
It's deliberately livelier than reality so you see trades quickly. Its
results say nothing about real markets, so don't judge any strategy on it.

(The Mac Terminal doesn't treat `# ...` as a comment when pasted, so these
command blocks have none.)

Stop with `Ctrl+C`. Every event is kept in `data/crypto_desk.db`.

Tests: `python3 -m pip install pytest && python3 -m pytest`.

### Troubleshooting
- **"SSL certificates missing"** in the top bar: you installed Python from
  python.org. Run `/Applications/Python\ 3.*/Install\ Certificates.command` once.
- **Port in use**: `python3 run.py --port 8800`.
- **"rate limited"**: the desk makes about 2 requests a second to Coinbase's
  public API, well under its limit, but running several copies might not be.

## Reading the dashboard

| Area | What it tells you |
|---|---|
| **Top bar** | Feed status, BTC price, the cost model in use, and a big **SIMULATED** or **LIVE DATA** badge so you always know which you're looking at. |
| **Signal Iris** (centre) | One bundle of strands per coin. **Length = signal score** (trend, 1h momentum, up-vs-down volume, volume acceleration). **White = up-volume dominates, red = down-volume dominates.** Strands in an agent's colour, marked ◆, are **held**. Buys burst outward, sells pull inward. Hover for the indicators (RSI, trend, z-score, strength vs BTC, spread); click to open it on Coinbase. The centre shows desk P&L. |
| **Left accordion** | **Open positions** with live P&L (valued at what you'd *actually* get selling now, at the bid, after fees). **Activity** is every BUY, SELL, BLOCKED and alert. Click any row to see why it happened: the agent's checklist with ✓/✗, the fill price vs the trigger price, spread, fees, price impact, and the exit rule that fired. |
| **Right** | Each agent's fake account, equity sparkline (dotted line = starting $25), trades, win rate, fees, and **ARMED/KILLED** state. Click an agent (or a chip up top) to filter everything to it. |
| **Bottom** | Market-wide net up/down volume, which exit rules fire most (a pareto), and P&L per agent after all costs. |

**PAUSE** stops new entries (exits keep running). **KILL ALL** sells everything
and stops every agent until you restart.

## The agents (the "sub-agents")

Same objective, different playbooks, separate accounts ($25 of fake money
each, $100 for the desk), so you can see which approach actually works rather than guessing.
They're the four classic families of systematic trading:

| Agent | Buys when... |
|---|---|
| **TR · Trend Rider** | 20-min EMA ≥0.15% above the 60-min EMA, price above the 20-min EMA, up ≥0.5% on the hour, RSI 52–70 (trending, not yet overbought) |
| **BR · Breakout** | price just cleared the previous hour's high (by 0.05–1%, so it isn't chasing), volume running ≥1.5x normal, up-volume ≥1.3x down-volume |
| **MR · Mean Revert** | price ≥2 standard deviations below its 1h average (lower Bollinger band), RSI ≤35, and not a crash day (24h change ≥ −8%). Tighter exits: +3% target, −2.5% stop, 4h time stop |
| **RS · Rel. Strength** | beating BTC by ≥1 percentage point over the last hour, its own trend up, still rising over 5 min |

Indicators are computed from scratch in `paper_trader/signals.py`; rules live
in `paper_trader/agents.py` as short checklists, easy to change.

## The risk engine (agents can't override it)

`paper_trader/risk.py` and `paper_trader/config.py`. Blocks buys in coins
trading under $5M a day, with a bid/ask spread wider than 0.25%, with under
65 minutes of price history, when the agent already has 4 open positions, when
it sold that coin in the last hour, or any trade over 20% of equity or 0.1% of
the coin's daily volume. Exits every position on the first of: **stop loss
−3%**, **take profit +6%**, **trailing stop** (1.5% off the peak once up
2.5%), or **time stop** after 8 hours. An agent down 15% from its peak hits
its **kill switch**.

If the feed hiccups, positions are held (and valued at the last good price)
until data returns. Major coins don't vanish; selling blind would be worse.

## How realistic is the fake money?

Real prices from the live feed, plus the costs a real market order pays:
- **Delay:** orders fill on the *next* update (~10 s later), at that price.
- **Spread:** buys fill at the **ask**, sells at the **bid**, using the live
  order book's best prices.
- **Price impact:** bigger orders fill further from the best price, using
  the "square-root law" against the coin's 24h volume (deliberately
  pessimistic; tune `IMPACT_COEF`).
- **Fees:** 0.40% taker fee per fill (an estimate: Kraken Pro's base tier;
  Coinbase Advanced charges more at low volume. Check yours in `TAKER_FEE`).
  A round trip costs ~0.8%, which is why the targets are several percent.
- **Honest valuation:** open positions are priced at what selling them now
  would return, not at the last traded price.

**Not modelled**, so live results would be somewhat worse than paper:
- Partial fills, and the order book being thinner than usual in a fast market.
- Cheaper **maker** (limit order) fees; this desk only uses market orders.
- Exchange outages and API delays.
- Taxes on each trade.

## Before any real money

Use these as a checklist, not a vibe:
1. Run **live data** for at least 2–4 weeks, not the demo. The demo is a
   market I invented; its results mean nothing.
2. Look for an agent with **100+ closed trades** and positive P&L **after
   costs**. Ten trades is luck.
3. Check it isn't one lucky trade: the P&L should survive removing the single
   best trade (query `data/crypto_desk.db`, below).
4. Compare with simply holding BTC over the same weeks. A strategy that
   loses to buy-and-hold isn't earning its fees.
5. Expect live to be worse than paper (see "Not modelled").

```bash
# Per-agent results across all runs (DuckDB reads SQLite directly)
duckdb -c "INSTALL sqlite; LOAD sqlite;
  SELECT agent, COUNT(*) AS trades, ROUND(SUM(pnl_usd), 2) AS pnl_usd,
         ROUND(SUM(pnl_usd) - MAX(pnl_usd), 2) AS pnl_without_best_trade
  FROM sqlite_scan('data/crypto_desk.db', 'events')
  WHERE kind = 'SELL' GROUP BY agent ORDER BY pnl_usd DESC"
```

Connecting a real exchange account is deliberately **not** part of this
project. It would be a separate, reviewed step: a dedicated account holding
only what you can afford to lose, trade-only API keys (no withdrawals), and
the same risk engine standing in front of it.

## Run it on GitHub instead of your Mac

Actions → **Live paper watch** → Run workflow runs the desk headless on live
Coinbase prices for up to ~5.5 hours and posts a P&L update to a GitHub issue
every few minutes, @mentioning you so the GitHub app notifies your phone.

## Files

```
run.py                     start everything
watch.py                   headless run with GitHub issue updates
paper_trader/config.py     every tunable number (and the coin list)
paper_trader/feeds.py      live Coinbase data
paper_trader/demo_feed.py  simulated market
paper_trader/signals.py    indicators from one-minute candles
paper_trader/agents.py     the four playbooks
paper_trader/risk.py       entry blocks + exit rules
paper_trader/broker.py     fake-money fills with real costs
paper_trader/engine.py     the loop that ties it together
paper_trader/server.py     local web server (127.0.0.1 only)
dashboard/                 the Signal Iris UI (plain HTML/CSS/JS, no libraries)
```


## Stock desk (US stocks, Alpaca paper)

Four agents day-trade big, liquid US stocks (Apple, Nvidia, JPMorgan... plus
the day's most-traded names) in an **Alpaca paper account**: real-time prices,
Alpaca's order simulator, no real money. Each agent has a $25k slice of the
account. Everything is sold 10 minutes before the close, so nothing is held
overnight.

| Agent | Buys when... |
|---|---|
| **OR · Opening Range** | price breaks above the first 15 minutes' high, volume running ahead of yesterday, above VWAP |
| **MO · Momentum** | up ≥1.5% since the open, still climbing over the last 15 min, above VWAP, heavy volume |
| **VR · VWAP Reversion** | down ≥2% on the day and ≥0.8% below VWAP, and starting to tick back up |
| **GG · Gap & Go** | opened ≥2% above yesterday's close and is holding the gain, in the first 2 hours |

**Risk rules** (`stock_desk/config.py`): $5k per trade, max 4 open per agent,
no trades in the first 15 or last 30 minutes, spreads must be under 0.25%,
stop loss −1.5%, take profit +3%, trailing stop, 2-hour time stop, and an
agent that loses 3% of its budget in a day stops until tomorrow.

### Setup (once)
1. Sign up at alpaca.markets (free) and open the **Paper Trading** account.
2. In the paper dashboard, generate **API keys** (a Key ID and a Secret).
3. In this repo on GitHub: Settings → Secrets and variables → Actions →
   New repository secret. Add `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`.

After that, the **Stock desk** workflow runs by itself every weekday: a
morning job waits for the opening bell, and an afternoon job takes over
(GitHub stops jobs at 6 hours; a session is 6.5). Each day gets its own issue
with an update every 5 minutes that @mentions you. To run it right now:
Actions → Stock desk → Run workflow.

**GitHub Actions minutes:** a full trading day uses roughly 400–470 minutes.
Private repos get 2,000 free minutes a month (about 4–5 trading days), and
by default GitHub then *stops* the jobs rather than charging you. Public
repos get unlimited free minutes; your keys stay secret either way. To use
fewer minutes, turn off the schedule and run it manually on the days you want.

Honest limits: Alpaca's paper fills don't model market impact or queue
position, though at $5k per trade in these stocks that's small. The free IEX
data feed only sees part of the market's volume, so volume numbers are
compared with each other, not with market totals.
