"""
The risk engine: deterministic rules that agents cannot override.

Entry: every proposed buy passes through check_entry(). Any failed rule
blocks it, and the reason is shown on the dashboard.
Exit:  every open position passes through check_exit() each tick. The first
rule that fires closes the position.

These are plain functions with no hidden state, so tests/test_risk.py can
prove each rule works. When real money is wired in later, this file is what
stands between a bug and your wallet.
"""

from __future__ import annotations

from dataclasses import replace

from .broker import Account
from .config import RiskLimits
from .models import Position, TokenSnapshot


def limits_for(base: RiskLimits, overrides: dict) -> RiskLimits:
    return replace(base, **overrides) if overrides else base


def check_entry(acct: Account, snap: TokenSnapshot, feats: dict, equity_sol: float,
                sol_usd: float, now: float, limits: RiskLimits,
                size_fraction: float) -> tuple[float, list[str]]:
    """Return (SOL to spend, reasons it's blocked). Empty reasons = approved."""
    blocked = []
    if acct.killed:
        blocked.append("kill switch is on for this agent")
    if snap.address in acct.positions:
        blocked.append("already holding it")
    if len(acct.positions) >= limits.max_open_positions:
        blocked.append(f"already at max {limits.max_open_positions} open positions")
    if snap.liquidity_usd < limits.min_liquidity_usd:
        blocked.append(f"liquidity ${snap.liquidity_usd:,.0f} below ${limits.min_liquidity_usd:,.0f} minimum")
    age = feats.get("age_min")
    if age is None or age < limits.min_age_minutes:
        blocked.append(f"pair younger than {limits.min_age_minutes:.0f} min (sniper zone)")
    last = acct.last_exit.get(snap.address)
    if last is not None and now - last < limits.reentry_cooldown_minutes * 60:
        blocked.append("sold this recently: cooling off")
    if snap.price_usd <= 0 or sol_usd <= 0:
        blocked.append("no valid price")

    size = equity_sol * min(size_fraction, limits.max_position_fraction)
    # Never be a whale in a puddle: cap the trade at a slice of the pool.
    if sol_usd > 0:
        size = min(size, snap.liquidity_usd * limits.max_pool_fraction / sol_usd)
    size = min(size, acct.cash_sol)
    if size < 0.01 and not blocked:
        blocked.append("not enough cash for a meaningful trade")
    return size, blocked


def check_exit(pos: Position, snap: TokenSnapshot | None, now: float,
               limits: RiskLimits) -> str | None:
    """Return a plain-English exit reason, or None to keep holding."""
    if snap is None:
        pos.missing_ticks += 1
        if pos.missing_ticks >= limits.stale_ticks_before_exit:
            return "RUG / DELISTED: token vanished from the market feed"
        return None
    pos.missing_ticks = 0
    pos.peak_price_usd = max(pos.peak_price_usd, snap.price_usd)
    change = snap.price_usd / pos.entry_price_usd - 1 if pos.entry_price_usd else 0.0
    from_peak = snap.price_usd / pos.peak_price_usd - 1 if pos.peak_price_usd else 0.0
    peak_gain = pos.peak_price_usd / pos.entry_price_usd - 1 if pos.entry_price_usd else 0.0

    if pos.entry_liquidity_usd and snap.liquidity_usd < pos.entry_liquidity_usd * (1 - limits.rug_liquidity_drop):
        return (f"RUG ALERT: liquidity fell {1 - snap.liquidity_usd / pos.entry_liquidity_usd:.0%} "
                "since entry")
    if change <= -limits.stop_loss:
        return f"STOP LOSS: down {change:.0%} (limit -{limits.stop_loss:.0%})"
    if change >= limits.take_profit:
        return f"TAKE PROFIT: up {change:.0%} (target +{limits.take_profit:.0%})"
    if peak_gain >= limits.trail_activate and from_peak <= -limits.trail_distance:
        return f"TRAILING STOP: {from_peak:.0%} off the peak after being up {peak_gain:.0%}"
    if (now - pos.opened_at) / 60 >= limits.max_hold_minutes:
        return f"TIME STOP: held {limits.max_hold_minutes:.0f} min without hitting a target"
    return None
