"""
The stock desk's risk rules: plain functions, no hidden state, unit-tested.

check_entry(): may this agent buy this stock now, and for how much?
check_exit():  should this position be sold now, and why?
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .config import RiskLimits
from .ledger import Book, Lot


def limits_for(base: RiskLimits, overrides: dict) -> RiskLimits:
    return replace(base, **overrides) if overrides else base


def check_entry(book: Book, symbol: str, f: dict, minutes_to_close: float, now: datetime,
                lim: RiskLimits) -> tuple[float, list[str]]:
    """Return (dollars to spend, reasons it's blocked). No reasons = approved."""
    blocked = []
    if book.killed:
        blocked.append("agent hit its daily loss limit")
    if symbol in book.lots:
        blocked.append("already holding it")
    if len(book.lots) >= lim.max_open_positions:
        blocked.append(f"already at max {lim.max_open_positions} positions")
    if f["elapsed_min"] < lim.entry_start_minutes:
        blocked.append(f"first {lim.entry_start_minutes:.0f} min of the day: too wild")
    if minutes_to_close < lim.entry_stop_minutes:
        blocked.append(f"last {lim.entry_stop_minutes:.0f} min of the day: no new trades")
    if f["price"] < lim.min_price:
        blocked.append(f"price under ${lim.min_price:.0f}")
    if f["spread_pct"] > lim.max_spread_pct:
        blocked.append(f"bid/ask spread {f['spread_pct']:.2f}% too wide")
    last = book.last_exit.get(symbol)
    if last and (now - last).total_seconds() < lim.reentry_cooldown_minutes * 60:
        blocked.append("sold it recently: cooling off")
    size = min(book.budget * lim.position_fraction, book.cash)
    if size < 100 and not blocked:
        blocked.append("not enough of the agent's budget left")
    return size, blocked


def check_exit(lot: Lot, price: float, now: datetime, minutes_to_close: float,
               lim: RiskLimits) -> str | None:
    """Return a plain-English exit reason, or None to keep holding."""
    lot.peak_price = max(lot.peak_price, price)
    change = price / lot.avg_price - 1
    from_peak = price / lot.peak_price - 1
    peak_gain = lot.peak_price / lot.avg_price - 1
    if minutes_to_close <= lim.flatten_minutes:
        return "END OF DAY: closing everything before the bell (no overnight risk)"
    if change <= -lim.stop_loss:
        return f"STOP LOSS: down {change:.1%} (limit -{lim.stop_loss:.1%})"
    if change >= lim.take_profit:
        return f"TAKE PROFIT: up {change:.1%} (target +{lim.take_profit:.1%})"
    if peak_gain >= lim.trail_activate and from_peak <= -lim.trail_distance:
        return f"TRAILING STOP: {from_peak:.1%} off the high after being up {peak_gain:.1%}"
    if (now - lot.opened_at).total_seconds() / 60 >= lim.max_hold_minutes:
        return f"TIME STOP: held {lim.max_hold_minutes:.0f} min without hitting a target"
    return None
