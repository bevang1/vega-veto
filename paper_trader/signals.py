"""
Turn a raw token snapshot into the handful of numbers agents look at.

Keeping this separate means every agent sees the same features, and the
dashboard can show exactly the values that triggered a trade.
"""

from __future__ import annotations

import math

from .models import TokenSnapshot


def features(snap: TokenSnapshot, now: float) -> dict:
    age_min = (now * 1000 - snap.created_at_ms) / 60_000 if snap.created_at_ms else None
    txns_m5 = snap.buys_m5 + snap.sells_m5
    f = {
        "age_min": age_min,
        # Buy/sell ratio: 2.0 means twice as many buys as sells.
        "buy_ratio_m5": snap.buys_m5 / max(snap.sells_m5, 1),
        "buy_ratio_h1": snap.buys_h1 / max(snap.sells_h1, 1),
        # Volume acceleration: last 5 min scaled to an hour vs the actual hour.
        # 1.0 = normal pace; 3.0 = trading three times faster than the hourly average.
        "vol_accel": snap.vol_m5 * 12 / max(snap.vol_h1, 1),
        "txns_m5": txns_m5,
        "chg_m5": snap.chg_m5,
        "chg_h1": snap.chg_h1,
        "liquidity": snap.liquidity_usd,
    }
    f["score"] = signal_score(f)
    return f


def signal_score(f: dict) -> float:
    """A 0-100 'how hot is this' number, used only to draw the dashboard.

    Agents do NOT trade on this: each agent has its own explicit rules.
    """
    def squash(x: float, scale: float) -> float:
        return 1 / (1 + math.exp(-x / scale))

    momentum = squash(f["chg_m5"], 5)
    pressure = squash(math.log(max(f["buy_ratio_m5"], 1e-3)), 0.3)
    accel = squash(f["vol_accel"] - 1, 1)
    depth = min(1.0, math.log10(max(f["liquidity"], 1)) / 6)
    return round(100 * (0.35 * momentum + 0.30 * pressure + 0.20 * accel + 0.15 * depth), 1)
