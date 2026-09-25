# Vega Veto

Two paper-trading desks, fake money only:

- **Stock desk** (`stock_desk/`, `stock_run.py`): day-trades liquid US stocks
  in an Alpaca **paper** account on GitHub's servers every trading day, and
  posts P&L updates to your phone. [Jump to it](#stock-desk-us-stocks-alpaca-paper).
- **Solana desk** (below): memecoins on live DexScreener data, with the
  Signal Iris dashboard.

**Solana Paper Desk · Signal Iris**

A 24/7 memecoin **paper trading** desk with a live dashboard. Four agents scan
real Solana tokens, trade **fake SOL**, and explain every buy and sell. No
wallet, no keys, no real money anywhere in this program.

The point is to learn how the tool behaves, and to find out whether any
strategy makes money **after realistic costs**, before a cent is at risk.

## Run it (macOS)

No installs needed: it's Python standard library only.

Download it (once):

```bash
git clone https://github.com/bevang1/vega-veto.git
cd vega-veto
```

**Run it.** Real Solana tokens, real live prices, fake money. `caffeinate`
keeps the Mac awake so it can run overnight:

```bash
caffeinate -i python3 run.py
```

The dashboard opens at <http://127.0.0.1:8765>. The top-right badge should
read **LIVE DATA · PAPER MONEY** (green). It keeps running until you press
`Ctrl+C`.

Optional, **offline practice only**: `python3 run.py --demo` runs an *invented*
market with made-up coins (badge: SIMULATED MARKET, amber). Its results say
nothing about real markets, so don't judge any strategy on it.

(The Mac Terminal doesn't treat `# ...` as a comment when pasted, so these
command blocks have none.)

Stop with `Ctrl+C`. Every event is kept in `data/paper_trades.db`.

Tests: `python3 -m pip install pytest && python3 -m pytest` (24 tests).

### Troubleshooting
- **"SSL certificates missing"** in the top bar: you installed Python from
  python.org. Run `/Applications/Python\ 3.*/Install\ Certificates.command` once.
- **Port in use**: `python3 run.py --port 8800`.
- **"rate limited"**: DexScreener allows 300 price requests and 60 discovery
  requests a minute. The desk stays well under that, but running two copies won't.

## Reading the dashboard

| Area | What it tells you |
|---|---|
| **Top bar** | Feed status, SOL price, the cost model in use, and a big **SIMULATED** or **LIVE DATA** badge so you always know which you're looking at. |
| **Signal Iris** (centre) | One bundle of strands per token. **Length = signal score** (momentum, buy pressure, volume acceleration, depth). **White = buyers dominate, red = sellers dominate.** Strands in an agent's colour, marked ◆, are **held**. Buys burst outward, sells pull inward. Hover for numbers; click to open it on DexScreener. The centre shows desk P&L. |
| **Left accordion** | **Open positions** with live P&L (valued at what you'd *actually* get selling now). **Activity** is every BUY, SELL, BLOCKED and alert. Click any row to see why it happened: the agent's checklist with ✓/✗, the fill price vs the trigger price, fees, price impact, and the exit rule that fired. |
| **Right** | Each agent's fake wallet, equity sparkline (dotted line = starting 10 SOL), trades, win rate, fees, and **ARMED/KILLED** state. Click an agent (or a chip up top) to filter everything to it. |
| **Bottom** | Market-wide net buy/sell flow, which exit rules fire most (a pareto), and P&L per agent after all costs. |

**PAUSE** stops new entries (exits keep running). **KILL ALL** sells everything
and stops every agent until you restart.

## The agents (the "sub-agents")

Same objective, different playbooks, separate wallets (10 fake SOL each), so you
can see which approach actually works rather than guessing:

| Agent | Buys when... |
|---|---|
| **MO · Momentum** | up ≥4% in 5 min and ≥10% in 1h, buys outnumber sells 1.3x, ≥30 trades in 5 min |
| **VS · Volume Surge** | trading pace ≥2.5x the hourly average, buyers ≥1.2x, price hasn't run more than 15% yet |
| **DB · Dip Buyer** | up ≥25% on the hour but pulled back 4–15% in the last 5 min, buyers still ≥1.1x |
| **FL · Fresh Launch** | pair 20–180 min old, buyers ≥1.5x, ≥50 trades in 5 min, ≥$40k liquidity (smaller size, tighter stop) |

Rules live in `paper_trader/agents.py`: short checklists, easy to change.

## The risk engine (agents can't override it)

`paper_trader/risk.py` and `paper_trader/config.py`. Blocks buys with liquidity
under $25k, pairs younger than 20 min (the sniper zone), more than 5 open
positions, re-buying within 30 min, or any trade over 10% of equity or 1% of the
pool. Exits every position on the first of: **stop loss -15%**, **take profit
+40%**, **trailing stop** (12% off the peak once up 15%), **time stop** 60 min,
**rug alert** (liquidity down 40% since entry), or the token vanishing from the
feed. An agent down 30% from its peak hits its **kill switch**.

## How realistic is the fake money?

Real prices from the live feed, plus the costs a real swap pays:
- **Delay:** orders fill on the *next* update (~10 s later), at that price.
- **Price impact:** constant-product AMM maths (`x*y=k`) against the pool's
  real liquidity. Thin pools punish you, as they would for real.
- **Fees:** 0.25% DEX fee + 0.0005 SOL network/priority fee per transaction
  (estimates; check yours).
- **Honest valuation:** open positions are priced at what selling them now
  would return, so a rugged bag shows as near-zero, not its last price.

**Not modelled**, so live results would be somewhat worse than paper:
- MEV "sandwich" bots front-running your swaps.
- Failed or dropped transactions when the network is congested.
- pump.fun bonding-curve pricing for tokens that haven't graduated to a DEX yet.
- DexScreener's own data delay.
- Token taxes and honeypots, where a scam token blocks selling. A paper trade
  can always "sell"; a real one sometimes can't.

## Before any real money

Use these as a checklist, not a vibe:
1. Run **live data** for at least 2–4 weeks, not the demo. The demo is a
   market I invented; its results mean nothing.
2. Look for an agent with **100+ closed trades** and positive P&L **after
   costs**. Ten trades is luck.
3. Check it isn't one lucky trade: the P&L should survive removing the single
   best trade (query `data/paper_trades.db`, below).
4. Expect live to be worse than paper (see "Not modelled").

```bash
# Per-agent results across all runs (DuckDB reads SQLite directly)
duckdb -c "INSTALL sqlite; LOAD sqlite;
  SELECT agent, COUNT(*) AS trades, ROUND(SUM(pnl_sol), 3) AS pnl_sol,
         ROUND(SUM(pnl_sol) - MAX(pnl_sol), 3) AS pnl_without_best_trade
  FROM sqlite_scan('data/paper_trades.db', 'events')
  WHERE kind = 'SELL' GROUP BY agent ORDER BY pnl_sol DESC"
```

Wiring in a real wallet is deliberately **not** part of this project. It
would be a separate, reviewed step: a dedicated wallet holding only what you can
afford to lose, and the same risk engine standing in front of it.

## Files

```
run.py                     start everything
paper_trader/config.py     every tunable number
paper_trader/feeds.py      live DexScreener data
paper_trader/demo_feed.py  simulated market
paper_trader/signals.py    features from raw snapshots
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
