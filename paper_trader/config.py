"""
Every tunable number in one place.

If you want to change how the desk behaves, this is the file to edit. Numbers
marked ESTIMATE are best guesses about real-world costs; check them against
the exchange and fee tier you'll actually use before trusting the P&L.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- What we trade --------------------------------------------------------------
# Large, liquid, established coins only: no memecoins, no fresh launches.
# Each one is traded against US dollars on a centralized exchange order book
# (e.g. "BTC-USD"). Add or remove symbols here; they must exist on Coinbase.
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "LTC", "ADA", "AVAX", "LINK", "DOT", "BCH"]
QUOTE_CURRENCY = "USD"
BENCHMARK = "BTC"          # "relative strength" means beating this coin

# --- Paper money --------------------------------------------------------------
STARTING_USD_PER_AGENT = 10_000.0   # each agent gets its own fake account
MIN_ORDER_USD = 10.0                # exchanges reject tiny orders; so do we

# --- Costs (what makes paper trading honest) ----------------------------------
TAKER_FEE = 0.004       # ESTIMATE: 0.40% per fill. Market orders pay the "taker" fee:
                        # Kraken Pro starts at 0.40%, Coinbase Advanced is higher at low volume.
IMPACT_COEF = 0.10      # ESTIMATE: price impact = IMPACT_COEF * sqrt(order size / 24h volume).
                        # The "square-root law" of market impact; 0.10 is deliberately pessimistic.
FALLBACK_SPREAD = 0.0005  # if the feed doesn't give a bid/ask, assume a 0.05% spread
# Orders are filled on the NEXT market update, not at the price that triggered
# them. That mimics the real delay between "bot decides" and "order lands".
# Buys fill at the ASK and sells at the BID, so every round trip pays the spread.

# --- Polling ------------------------------------------------------------------
LIVE_TICK_SECONDS = 10          # how often we pull fresh prices
STATS_EVERY_TICKS = 6           # 24h stats change slowly: refresh them every ~1 min
# Per tick: one ticker + one candles request per coin (20 for 10 coins), i.e.
# ~2 requests/second. Coinbase's public limit is about 10 per second.
HISTORY_MINUTES = 300           # Coinbase returns up to 300 one-minute candles
DEMO_TICK_SECONDS = 1.0         # demo mode runs faster than real time...
DEMO_SIM_SECONDS_PER_TICK = 60  # ...each tick is one market minute (one candle)


@dataclass(frozen=True)
class RiskLimits:
    """Hard rules the risk engine enforces. Agents can't override these."""
    min_volume_24h_usd: float = 5_000_000  # skip anything that isn't actively traded
    max_spread_pct: float = 0.25          # a wide bid/ask spread is a hidden fee; skip it
    min_history_minutes: int = 65         # indicators need an hour of candles to mean anything
    max_position_fraction: float = 0.20   # at most 20% of an agent's equity per trade
    max_volume_fraction: float = 0.001    # never be more than 0.1% of a coin's daily volume
    max_open_positions: int = 4
    reentry_cooldown_minutes: float = 60  # don't instantly re-buy something you just sold
    kill_switch_drawdown: float = 0.15    # agent stops trading after losing 15% from its peak

    # Exits (shared by all agents unless an agent overrides them). Majors move
    # far less than memecoins, so the targets are percent-sized, not 40%.
    stop_loss: float = 0.03               # sell if down 3%
    take_profit: float = 0.06             # sell if up 6%
    trail_activate: float = 0.025         # once up 2.5%...
    trail_distance: float = 0.015         # ...sell if it falls 1.5% from its best price
    max_hold_minutes: float = 8 * 60      # intraday desk: close anything still open after 8h
