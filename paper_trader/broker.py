"""
The paper broker: fake money, realistic fills.

This is the file that decides whether paper P&L means anything. A naive
simulator buys at the displayed price for free, and every strategy looks
brilliant. Here each swap pays:

  1. Latency:      orders fill on the NEXT market update, at that price.
  2. Price impact: memecoin pools are AMMs (constant-product x*y=k). Buying
                   pushes the price up against you; selling pushes it down.
                   The thinner the pool, the worse it gets.
  3. DEX fee:      a % of every swap (config.DEX_FEE).
  4. Network fee:  a flat SOL cost per transaction (config.NETWORK_FEE_SOL).

Positions are also VALUED at what you'd get if you sold right now (after
impact and fees), not at the headline price. So a bag in a pool that has
been rugged shows its true near-zero value.

Not modelled (see README): MEV sandwich attacks, failed transactions,
pump.fun bonding curves, and our own trades moving the real pool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .models import Order, Position, TokenSnapshot

# A sell can't fill against missing data. Wait this many updates (~1 min
# live) for the token to reappear before treating it as unsellable (worth 0).
SELL_WAIT_TICKS = 6


def amm_out_usd(in_usd: float, liquidity_usd: float) -> float:
    """USD value received for swapping `in_usd` into a constant-product pool.

    A pool with $L of liquidity holds ~$L/2 on each side. For x*y=k, swapping
    in dx gives out  depth * dx / (depth + dx),  valued at the pre-trade price.
    Tiny trades get ~100%; a trade the size of the whole side gets only 50%.
    """
    depth = max(liquidity_usd / 2, 1e-9)
    return depth * in_usd / (depth + in_usd)


def quote_buy(sol_in: float, snap: TokenSnapshot, sol_usd: float) -> dict:
    """What a buy of `sol_in` SOL would get right now, fees and impact included."""
    usd_in = sol_in * sol_usd
    fee_usd = usd_in * config.DEX_FEE
    got_usd = amm_out_usd(usd_in - fee_usd, snap.liquidity_usd)
    tokens = got_usd / snap.price_usd if snap.price_usd > 0 else 0.0
    return {
        "tokens": tokens,
        "fee_usd": fee_usd,
        "impact_pct": (1 - got_usd / max(usd_in - fee_usd, 1e-12)) * 100,
        "avg_price_usd": usd_in / tokens if tokens else 0.0,
    }


def quote_sell(tokens: float, snap: TokenSnapshot | None, sol_usd: float) -> dict:
    """What selling `tokens` would return right now, in SOL, after everything."""
    if snap is None or snap.price_usd <= 0 or sol_usd <= 0:
        return {"sol_out": 0.0, "fee_usd": 0.0, "impact_pct": 100.0, "gross_usd": 0.0}
    value_usd = tokens * snap.price_usd
    got_usd = amm_out_usd(value_usd, snap.liquidity_usd)
    fee_usd = got_usd * config.DEX_FEE
    sol_out = max(0.0, (got_usd - fee_usd) / sol_usd - config.NETWORK_FEE_SOL)
    return {
        "sol_out": sol_out,
        "fee_usd": fee_usd,
        "impact_pct": (1 - got_usd / max(value_usd, 1e-12)) * 100,
        "gross_usd": value_usd,
    }


@dataclass
class Account:
    """One agent's fake wallet."""
    name: str
    cash_sol: float = config.STARTING_SOL_PER_AGENT
    start_sol: float = config.STARTING_SOL_PER_AGENT
    positions: dict[str, Position] = field(default_factory=dict)
    reserved_sol: float = 0.0   # SOL set aside for buy orders that haven't filled yet
    realized_pnl_sol: float = 0.0
    fees_sol: float = 0.0
    wins: int = 0
    losses: int = 0
    peak_equity_sol: float = config.STARTING_SOL_PER_AGENT
    killed: bool = False
    last_exit: dict[str, float] = field(default_factory=dict)  # address -> time sold

    def equity_sol(self, snaps: dict[str, TokenSnapshot], sol_usd: float) -> float:
        held = sum(quote_sell(p.tokens, snaps.get(p.address), sol_usd)["sol_out"]
                   for p in self.positions.values())
        # Reserved SOL is still ours until the order fills, so it counts.
        return self.cash_sol + self.reserved_sol + held


class PaperBroker:
    def __init__(self, agent_names: list[str]) -> None:
        self.accounts = {n: Account(n) for n in agent_names}
        self.pending: list[Order] = []

    def submit(self, order: Order) -> None:
        # Reserve the cash now so an agent can't spend the same SOL twice
        # while its order is in flight.
        if order.side == "BUY":
            acct = self.accounts[order.agent]
            acct.cash_sol -= order.sol_amount
            acct.reserved_sol += order.sol_amount
        self.pending.append(order)

    def has_pending(self, agent: str, address: str) -> bool:
        return any(o.agent == agent and o.address == address for o in self.pending)

    def fill_pending(self, snaps: dict[str, TokenSnapshot], sol_usd: float, now: float) -> list[dict]:
        """Fill every waiting order at the NEW prices. Returns fill reports."""
        fills = []
        orders, self.pending = self.pending, []
        for o in orders:
            acct = self.accounts[o.agent]
            snap = snaps.get(o.address)
            if o.side == "BUY":
                acct.reserved_sol -= o.sol_amount
                fills.append(self._fill_buy(acct, o, snap, sol_usd, now))
            elif snap is None and o.retries < SELL_WAIT_TICKS:
                o.retries += 1
                self.pending.append(o)   # data gap: try again next update
            else:
                fills.append(self._fill_sell(acct, o, snap, sol_usd, now))
        return [f for f in fills if f]

    def _fill_buy(self, acct: Account, o: Order, snap, sol_usd: float, now: float) -> dict:
        if snap is None or snap.price_usd <= 0:
            acct.cash_sol += o.sol_amount   # token vanished: order fails, refund
            return {"kind": "BUY_FAILED", "order": o, "why": "token disappeared before the order filled"}
        spend = o.sol_amount - config.NETWORK_FEE_SOL
        q = quote_buy(spend, snap, sol_usd)
        acct.fees_sol += config.NETWORK_FEE_SOL + q["fee_usd"] / sol_usd
        acct.positions[o.address] = Position(
            agent=o.agent, address=o.address, symbol=o.symbol, tokens=q["tokens"],
            cost_sol=o.sol_amount, entry_price_usd=q["avg_price_usd"],
            entry_liquidity_usd=snap.liquidity_usd, opened_at=now,
            entry_reason=o.reason, peak_price_usd=snap.price_usd,
        )
        slip = (q["avg_price_usd"] / o.decision_price_usd - 1) * 100 if o.decision_price_usd else 0.0
        return {"kind": "BUY", "order": o, "quote": q, "price_usd": snap.price_usd,
                "fill_price_usd": q["avg_price_usd"], "slippage_pct": slip, "snap": snap}

    def _fill_sell(self, acct: Account, o: Order, snap, sol_usd: float, now: float) -> dict | None:
        pos = acct.positions.pop(o.address, None)
        if pos is None:
            return None
        q = quote_sell(pos.tokens, snap, sol_usd)
        acct.cash_sol += q["sol_out"]
        acct.fees_sol += config.NETWORK_FEE_SOL + (q["fee_usd"] / sol_usd if sol_usd else 0)
        pnl = q["sol_out"] - pos.cost_sol
        acct.realized_pnl_sol += pnl
        if pnl > 0:
            acct.wins += 1
        else:
            acct.losses += 1
        acct.last_exit[o.address] = now
        return {"kind": "SELL", "order": o, "quote": q, "position": pos, "pnl_sol": pnl,
                "pnl_pct": pnl / pos.cost_sol * 100 if pos.cost_sol else 0.0,
                "held_minutes": (now - pos.opened_at) / 60, "snap": snap}
