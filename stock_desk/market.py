"""
Market data shapes: a Quote per stock, and a rolling history of 1-minute bars.

Alpaca's snapshot gives, per stock: the latest trade, the best bid/ask, the
latest 1-minute bar, today's bar so far (open, volume, VWAP) and yesterday's
bar. We keep our own minute history on top, for short-term momentum and the
opening range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _f(d: dict | None, key: str, default: float = 0.0) -> float:
    try:
        return float((d or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def parse_time(s: str) -> datetime:
    # Alpaca sends RFC 3339 with nanoseconds, e.g. 2026-09-25T13:31:00.123456789Z
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        frac, _, tz = tail.partition("+")
        s = f"{head}.{frac[:6]}+{tz}" if tz else f"{head}.{frac[:6]}"
    return datetime.fromisoformat(s)


@dataclass
class Quote:
    symbol: str
    price: float          # last trade
    bid: float
    ask: float
    day_open: float
    day_volume: float
    vwap: float           # today's volume-weighted average price
    prev_close: float
    prev_volume: float
    day_date: str         # date of today's bar, to spot stale data before the open

    @classmethod
    def from_snapshot(cls, symbol: str, snap: dict) -> "Quote":
        daily = snap.get("dailyBar") or {}
        return cls(
            symbol=symbol,
            price=_f(snap.get("latestTrade"), "p"),
            bid=_f(snap.get("latestQuote"), "bp"),
            ask=_f(snap.get("latestQuote"), "ap"),
            day_open=_f(daily, "o"),
            day_volume=_f(daily, "v"),
            vwap=_f(daily, "vw"),
            prev_close=_f(snap.get("prevDailyBar"), "c"),
            prev_volume=_f(snap.get("prevDailyBar"), "v"),
            day_date=str(daily.get("t", ""))[:10],
        )

    @property
    def spread_pct(self) -> float:
        if self.bid <= 0 or self.ask <= 0:
            return 99.0
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid * 100

    @property
    def sell_price(self) -> float:
        """What selling right now would roughly get: the bid, if we have one."""
        return self.bid if 0 < self.bid <= self.price * 1.05 else self.price


class MinuteHistory:
    """Per-symbol 1-minute bars keyed by their start time (ISO string)."""

    def __init__(self) -> None:
        self.bars: dict[str, dict[str, dict]] = {}

    def add(self, symbol: str, bar: dict | None) -> None:
        if bar and bar.get("t"):
            self.bars.setdefault(symbol, {})[bar["t"][:19]] = bar

    def closes_before(self, symbol: str, t: datetime) -> list[float]:
        """Closes of bars that started before time t, oldest first."""
        cutoff = t.strftime("%Y-%m-%dT%H:%M:%S")
        rows = self.bars.get(symbol, {})
        return [rows[k]["c"] for k in sorted(rows) if k < cutoff]

    def range_between(self, symbol: str, start: datetime, end: datetime) -> tuple[float, float] | None:
        """(high, low) of bars starting in [start, end), e.g. the opening range."""
        a, b = start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [v for k, v in self.bars.get(symbol, {}).items() if a <= k < b]
        if not rows:
            return None
        return max(r["h"] for r in rows), min(r["l"] for r in rows)
