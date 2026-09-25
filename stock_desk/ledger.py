"""
Who owns what: each agent's positions and P&L inside the one paper account.

Alpaca holds a single account, so we tag every order with the agent's code
in its client_order_id (e.g. "vv-MO-3f9a1c2e"). That makes Alpaca's own
order history the source of truth: if the job restarts mid-day, the
ledger is rebuilt from today's filled orders.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .market import parse_time

PREFIX = "vv-"


def new_order_id(code: str) -> str:
    return f"{PREFIX}{code}-{uuid.uuid4().hex[:10]}"


def agent_code(client_order_id: str) -> str | None:
    if not client_order_id or not client_order_id.startswith(PREFIX):
        return None
    return client_order_id[len(PREFIX):].split("-", 1)[0]


@dataclass
class Lot:
    symbol: str
    qty: float
    avg_price: float
    opened_at: datetime
    entry_reason: str = ""
    peak_price: float = 0.0

    @property
    def cost(self) -> float:
        return self.qty * self.avg_price


@dataclass
class Book:
    """One agent's slice of the account."""
    code: str
    budget: float
    lots: dict[str, Lot] = field(default_factory=dict)
    realized: float = 0.0
    wins: int = 0
    losses: int = 0
    killed: bool = False
    last_exit: dict[str, datetime] = field(default_factory=dict)

    @property
    def invested(self) -> float:
        return sum(l.cost for l in self.lots.values())

    @property
    def cash(self) -> float:
        return self.budget + self.realized - self.invested

    def apply_fill(self, symbol: str, side: str, qty: float, price: float, when: datetime,
                   reason: str = "") -> float | None:
        """Record a filled order. Returns realised P&L for sells, None for buys."""
        if side == "buy":
            lot = self.lots.get(symbol)
            if lot:  # add to an existing lot at the blended price
                total = lot.qty + qty
                lot.avg_price = (lot.cost + qty * price) / total
                lot.qty = total
            else:
                self.lots[symbol] = Lot(symbol, qty, price, when, reason, price)
            return None
        lot = self.lots.get(symbol)
        if not lot:
            return None
        sold = min(qty, lot.qty)
        pnl = sold * (price - lot.avg_price)
        self.realized += pnl
        lot.qty -= sold
        if lot.qty <= 1e-9:
            del self.lots[symbol]
            self.last_exit[symbol] = when
            if pnl > 0:
                self.wins += 1
            else:
                self.losses += 1
        return pnl


def rebuild(books: dict[str, Book], orders: list[dict]) -> int:
    """Replay today's filled orders into the books. Returns how many were applied."""
    applied = 0
    for o in sorted(orders, key=lambda o: o.get("filled_at") or ""):
        code = agent_code(o.get("client_order_id", ""))
        qty, price = float(o.get("filled_qty") or 0), float(o.get("filled_avg_price") or 0)
        if code in books and qty > 0 and price > 0 and o.get("filled_at"):
            books[code].apply_fill(o["symbol"], o["side"], qty, price, parse_time(o["filled_at"]),
                                   "(restored from Alpaca order history)")
            applied += 1
    return applied
