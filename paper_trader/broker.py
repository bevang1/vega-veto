"""
The paper broker: fake money, realistic fills.

This is the file that decides whether paper P&L means anything. A naive
simulator buys at the displayed price for free, and every strategy looks
brilliant. Here each market order pays:

  1. Latency:      orders fill on the NEXT market update, at that price.
  2. Spread:       buys fill at the ASK, sells at the BID. The gap between
                   them is a cost you pay on every round trip.
  3. Price impact: a bigger order eats deeper into the order book. We use the
                   "square-root law": impact grows with sqrt(order / daily
                   volume), so size matters more in thinner coins.
  4. Taker fee:    a % of every fill (config.TAKER_FEE).

Positions are also VALUED at what you'd get if you sold right now (at the
bid, after impact and fees), not at the last traded price.

Not modelled (see README): partial fills, maker (limit order) rebates,
exchange outages, and our own trades moving the real market.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config
from .models import AssetSnapshot, Order, Position


def impact_fraction(order_usd: float, volume_24h_usd: float) -> float:
    """How far past the best price an order of this size fills, as a fraction.

    $1,000 of BTC (billions traded a day) barely registers; the same $1,000
    in a coin trading $5M a day moves it noticeably more.
    """
    return config.IMPACT_COEF * math.sqrt(max(order_usd, 0) / max(volume_24h_usd, 1.0))


def bid_ask(snap: AssetSnapshot) -> tuple[float, float]:
    """Best bid and ask, falling back to an assumed spread if the feed has none."""
    if snap.bid > 0 and snap.ask >= snap.bid:
        return snap.bid, snap.ask
    half = snap.price_usd * config.FALLBACK_SPREAD / 2
    return snap.price_usd - half, snap.price_usd + half


def quote_buy(usd_in: float, snap: AssetSnapshot) -> dict:
    """What spending `usd_in` dollars would buy right now, fees and impact included."""
    fee_usd = usd_in * config.TAKER_FEE
    _, ask = bid_ask(snap)
    impact = impact_fraction(usd_in, snap.volume_24h_usd)
    fill = ask * (1 + impact)
    qty = (usd_in - fee_usd) / fill if fill > 0 else 0.0
    return {
        "qty": qty,
        "fee_usd": fee_usd,
        "impact_pct": impact * 100,
        "spread_pct": snap.spread_pct,
        "fill_price_usd": fill,
        # All-in price per coin, fee included: what the position really cost.
        "avg_price_usd": usd_in / qty if qty else 0.0,
    }


def quote_sell(qty: float, snap: AssetSnapshot | None) -> dict:
    """What selling `qty` coins would return right now, in dollars, after everything."""
    if snap is None or snap.price_usd <= 0:
        return {"usd_out": 0.0, "fee_usd": 0.0, "impact_pct": 0.0, "fill_price_usd": 0.0, "gross_usd": 0.0}
    bid, _ = bid_ask(snap)
    gross = qty * bid
    impact = impact_fraction(gross, snap.volume_24h_usd)
    fill = bid * (1 - impact)
    got = qty * fill
    fee_usd = got * config.TAKER_FEE
    return {
        "usd_out": max(0.0, got - fee_usd),
        "fee_usd": fee_usd,
        "impact_pct": impact * 100,
        "fill_price_usd": fill,
        "gross_usd": qty * snap.price_usd,
    }


@dataclass
class Account:
    """One agent's fake account, in US dollars."""
    name: str
    cash_usd: float = config.STARTING_USD_PER_AGENT
    start_usd: float = config.STARTING_USD_PER_AGENT
    positions: dict[str, Position] = field(default_factory=dict)   # symbol -> position
    reserved_usd: float = 0.0   # cash set aside for buy orders that haven't filled yet
    realized_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    wins: int = 0
    losses: int = 0
    peak_equity_usd: float = config.STARTING_USD_PER_AGENT
    killed: bool = False
    last_exit: dict[str, float] = field(default_factory=dict)  # symbol -> time sold

    def equity_usd(self, snaps: dict[str, AssetSnapshot]) -> float:
        held = sum(quote_sell(p.qty, snaps.get(p.symbol))["usd_out"] for p in self.positions.values())
        # Reserved cash is still ours until the order fills, so it counts.
        return self.cash_usd + self.reserved_usd + held


class PaperBroker:
    def __init__(self, agent_names: list[str]) -> None:
        self.accounts = {n: Account(n) for n in agent_names}
        self.pending: list[Order] = []

    def submit(self, order: Order) -> None:
        # Reserve the cash now so an agent can't spend the same dollars twice
        # while its order is in flight.
        if order.side == "BUY":
            acct = self.accounts[order.agent]
            acct.cash_usd -= order.usd_amount
            acct.reserved_usd += order.usd_amount
        self.pending.append(order)

    def has_pending(self, agent: str, symbol: str) -> bool:
        return any(o.agent == agent and o.symbol == symbol for o in self.pending)

    def fill_pending(self, snaps: dict[str, AssetSnapshot], now: float) -> list[dict]:
        """Fill every waiting order at the NEW prices. Returns fill reports."""
        fills = []
        orders, self.pending = self.pending, []
        for o in orders:
            acct = self.accounts[o.agent]
            snap = snaps.get(o.symbol)
            if o.side == "BUY":
                acct.reserved_usd -= o.usd_amount
                fills.append(self._fill_buy(acct, o, snap, now))
            elif snap is None:
                # No fresh price for this coin (a gap in the feed). Unlike a
                # memecoin, BTC doesn't vanish: wait for data, then sell.
                self.pending.append(o)
            else:
                fills.append(self._fill_sell(acct, o, snap, now))
        return [f for f in fills if f]

    def _fill_buy(self, acct: Account, o: Order, snap, now: float) -> dict:
        if snap is None or snap.price_usd <= 0:
            acct.cash_usd += o.usd_amount   # no price to fill at: order fails, refund
            return {"kind": "BUY_FAILED", "order": o, "why": "no fresh price when the order was due to fill"}
        q = quote_buy(o.usd_amount, snap)
        acct.fees_usd += q["fee_usd"]
        acct.positions[o.symbol] = Position(
            agent=o.agent, symbol=o.symbol, qty=q["qty"], cost_usd=o.usd_amount,
            entry_price_usd=q["avg_price_usd"], opened_at=now,
            entry_reason=o.reason, peak_price_usd=snap.price_usd,
        )
        slip = (q["fill_price_usd"] / o.decision_price_usd - 1) * 100 if o.decision_price_usd else 0.0
        return {"kind": "BUY", "order": o, "quote": q, "price_usd": snap.price_usd,
                "fill_price_usd": q["fill_price_usd"], "slippage_pct": slip, "snap": snap}

    def _fill_sell(self, acct: Account, o: Order, snap, now: float) -> dict | None:
        pos = acct.positions.pop(o.symbol, None)
        if pos is None:
            return None
        q = quote_sell(pos.qty, snap)
        acct.cash_usd += q["usd_out"]
        acct.fees_usd += q["fee_usd"]
        pnl = q["usd_out"] - pos.cost_usd
        acct.realized_pnl_usd += pnl
        if pnl > 0:
            acct.wins += 1
        else:
            acct.losses += 1
        acct.last_exit[o.symbol] = now
        return {"kind": "SELL", "order": o, "quote": q, "position": pos, "pnl_usd": pnl,
                "pnl_pct": pnl / pos.cost_usd * 100 if pos.cost_usd else 0.0,
                "held_minutes": (now - pos.opened_at) / 60, "snap": snap}
