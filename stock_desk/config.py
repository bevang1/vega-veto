"""
Every tunable number for the stock desk, in one place.

The desk day-trades liquid US stocks in an Alpaca PAPER account: real-time
prices, a real order simulator, fake money. Every position is closed before
the market shuts, so nothing is held overnight.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Alpaca --------------------------------------------------------------------
TRADING_URL = "https://paper-api.alpaca.markets"   # PAPER only. Never the live URL.
DATA_URL = "https://data.alpaca.markets"
DATA_FEED = "iex"   # the free real-time feed. Its volumes are IEX's slice of the
                    # market, so compare them with each other, not with totals.

# --- What we watch -------------------------------------------------------------
# Big, liquid names: tight spreads, no rug pulls, and our orders are tiny
# compared with their trading volume.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "NFLX",
    "JPM", "BAC", "XOM", "CVX", "WMT", "COST", "KO", "PEP", "DIS", "INTC",
    "MU", "QCOM", "CRM", "ORCL", "ADBE", "UBER", "PLTR", "COIN", "SHOP", "PYPL",
    "BA", "CAT", "GE", "F", "GM", "PFE", "MRK", "LLY", "UNH", "V", "MA",
]
MOST_ACTIVES_TOP = 20      # plus today's most-traded stocks from Alpaca's screener

# --- Money ---------------------------------------------------------------------
AGENT_BUDGET_USD = 25_000  # each of the 4 agents trades its own $25k slice of the paper account

# --- Timing --------------------------------------------------------------------
TICK_SECONDS = 30          # how often the desk looks at prices
SESSION_MINUTES = 390      # 09:30-16:00 New York time


@dataclass(frozen=True)
class RiskLimits:
    """Hard rules. Agents propose, these decide."""
    min_price: float = 5.0                # skip penny stocks
    max_spread_pct: float = 0.25          # skip if bid/ask gap > 0.25% (costly to trade)
    position_fraction: float = 0.20       # each trade = 20% of the agent's budget ($5k)
    max_open_positions: int = 4
    reentry_cooldown_minutes: float = 30
    entry_start_minutes: float = 15       # no new trades in the first 15 min (wild open)
    entry_stop_minutes: float = 30        # ...or in the last 30 min
    flatten_minutes: float = 10           # sell everything 10 min before the close
    agent_daily_loss: float = 0.03        # agent down 3% of its budget today = stop for the day

    # Exits (agents can override some of these)
    stop_loss: float = 0.015              # -1.5%
    take_profit: float = 0.03             # +3%
    trail_activate: float = 0.015         # once up 1.5%...
    trail_distance: float = 0.01          # ...sell if it drops 1% from its best
    max_hold_minutes: float = 120
