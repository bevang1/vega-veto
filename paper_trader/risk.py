"""
The risk engine: deterministic rules that agents cannot override.

Entry: every proposed buy passes through check_entry(). Any failed rule
blocks it, and the reason is shown on the dashboard.
Exit:  every open position passes through check_exit() each tick. The first
rule that fires closes the position.

These are plain functions with no hidden state, so tests/test_risk.py can
prove each rule works. When real money is wired in later, this file is what
stands between a bug and your account.
"""

from __future__ import annotations

from dataclasses import replace

from . import config
from .broker import Account
from .config import RiskLimits
from .models import AssetSnapshot, Position


def limits_for(base: RiskLimits, overrides: dict) -> RiskLimits:
    return replace(base, **overrides) if overrides else base


def check_entry(acct: Account, snap: AssetSnapshot, feats: dict, equity_usd: float,
                now: float, limits: RiskLimits, size_fraction: float,
                pending_buys: int = 0) -> tuple[float, list[str]]:
    """Return (dollars to spend, reasons it's blocked). Empty reasons = approved.

    `pending_buys` = this agent's buy orders still waiting to fill. They count
    toward the position limit, or two buys in one tick could overshoot it.
    """
    blocked = []
    if acct.killed:
        blocked.append("kill switch is on for this agent")
    if snap.symbol in acct.positions:
        blocked.append("already holding it")
    if len(acct.positions) + pending_buys >= limits.max_open_positions:
        blocked.append(f"already at max {limits.max_open_positions} open positions")
    if snap.volume_24h_usd < limits.min_volume_24h_usd:
        blocked.append(f"24h volume ${snap.volume_24h_usd:,.0f} below ${limits.min_volume_24h_usd:,.0f} minimum")
    if snap.spread_pct > limits.max_spread_pct:
        blocked.append(f"spread {snap.spread_pct:.2f}% wider than {limits.max_spread_pct:.2f}% limit")
    if feats.get("history_min", 0) < limits.min_history_minutes:
        blocked.append(f"under {limits.min_history_minutes} min of price history: indicators not ready")
    last = acct.last_exit.get(snap.symbol)
    if last is not None and now - last < limits.reentry_cooldown_minutes * 60:
        blocked.append("sold this recently: cooling off")
    if snap.price_usd <= 0:
        blocked.append("no valid price")

    size = equity_usd * min(size_fraction, limits.max_position_fraction)
    # Never be a meaningful share of the market: cap at a slice of daily volume.
    size = min(size, snap.volume_24h_usd * limits.max_volume_fraction)
    size = min(size, acct.cash_usd)
    if size < config.MIN_ORDER_USD and not blocked:
        blocked.append("not enough cash for a meaningful trade")
    return size, blocked


def check_exit(pos: Position, snap: AssetSnapshot | None, now: float,
               limits: RiskLimits) -> str | None:
    """Return a plain-English exit reason, or None to keep holding."""
    if snap is None:
        # No fresh price this tick (feed hiccup). Majors don't disappear, so
        # hold and re-check when data returns rather than panic-selling blind.
        return None
    pos.peak_price_usd = max(pos.peak_price_usd, snap.price_usd)
    change = snap.price_usd / pos.entry_price_usd - 1 if pos.entry_price_usd else 0.0
    from_peak = snap.price_usd / pos.peak_price_usd - 1 if pos.peak_price_usd else 0.0
    peak_gain = pos.peak_price_usd / pos.entry_price_usd - 1 if pos.entry_price_usd else 0.0

    if change <= -limits.stop_loss:
        return f"STOP LOSS: down {change:.1%} (limit -{limits.stop_loss:.1%})"
    if change >= limits.take_profit:
        return f"TAKE PROFIT: up {change:.1%} (target +{limits.take_profit:.1%})"
    if peak_gain >= limits.trail_activate and from_peak <= -limits.trail_distance:
        return f"TRAILING STOP: {from_peak:.1%} off the peak after being up {peak_gain:.1%}"
    if (now - pos.opened_at) / 60 >= limits.max_hold_minutes:
        return f"TIME STOP: held {limits.max_hold_minutes / 60:.0f}h without hitting a target"
    return None
