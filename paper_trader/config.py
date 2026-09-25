"""
Every tunable number in one place.

If you want to change how the desk behaves, this is the file to edit. Numbers
marked ESTIMATE are best guesses about real-world costs; check them against
the DEX and wallet you'll actually use before trusting the P&L.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Paper money --------------------------------------------------------------
STARTING_SOL_PER_AGENT = 10.0   # each agent gets its own fake wallet

# --- Costs (what makes paper trading honest) ----------------------------------
DEX_FEE = 0.0025          # ESTIMATE: 0.25% swap fee (Raydium AMM v4). Other pools charge 0.01%-1%.
NETWORK_FEE_SOL = 0.0005  # ESTIMATE: base fee + priority fee per transaction when the network is busy.
# Orders are filled on the NEXT market update, not at the price that triggered
# them. That mimics the real delay between "bot decides" and "swap lands".

# --- Polling ------------------------------------------------------------------
LIVE_TICK_SECONDS = 10          # how often we pull fresh prices (DexScreener limit: 300 req/min)
DISCOVERY_EVERY_TICKS = 6       # look for new tokens every 6 ticks (~1 min; limit is 60 req/min)
MAX_TRACKED_TOKENS = 90         # 3 batched requests of 30 per tick
DEMO_TICK_SECONDS = 1.5         # demo mode runs faster than real time...
DEMO_SIM_SECONDS_PER_TICK = 10  # ...but each tick still represents 10 market seconds


@dataclass(frozen=True)
class RiskLimits:
    """Hard rules the risk engine enforces. Agents can't override these."""
    min_liquidity_usd: float = 25_000     # thin pools = huge slippage and easy rugs
    min_age_minutes: float = 20           # skip the first minutes when snipers dominate
    max_position_fraction: float = 0.10   # at most 10% of an agent's equity per trade
    max_pool_fraction: float = 0.01       # never be more than 1% of a pool's liquidity
    max_open_positions: int = 5
    reentry_cooldown_minutes: float = 30  # don't instantly re-buy something you just sold
    kill_switch_drawdown: float = 0.30    # agent stops trading after losing 30% from its peak

    # Exits (shared by all agents unless an agent overrides them)
    stop_loss: float = 0.15               # sell if down 15%
    take_profit: float = 0.40             # sell if up 40%
    trail_activate: float = 0.15          # once up 15%...
    trail_distance: float = 0.12          # ...sell if it falls 12% from its best price
    max_hold_minutes: float = 60          # memecoin momentum fades fast: don't marry a bag
    rug_liquidity_drop: float = 0.40      # liquidity down 40% since entry = get out
    stale_ticks_before_exit: int = 6      # token vanished from the feed for ~1 min = exit
