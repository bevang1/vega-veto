"""
Turn a Quote plus minute history into the few numbers agents look at.

All percentages are in % (1.5 means 1.5%).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import config
from .market import MinuteHistory, Quote


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if a > 0 and b > 0 else 0.0


def features(q: Quote, hist: MinuteHistory, open_time: datetime, now: datetime) -> dict:
    elapsed = max(0.0, (now - open_time).total_seconds() / 60)
    closes = hist.closes_before(q.symbol, now)

    def momentum(minutes: int) -> float:
        # Price now vs the close `minutes` bars ago (bars are 1 minute each).
        return pct(q.price, closes[-minutes]) if len(closes) >= minutes else 0.0

    orange = hist.range_between(q.symbol, open_time, open_time + timedelta(minutes=15))
    # Volume pace: today's volume so far vs yesterday's, adjusted for how much
    # of the day has passed. 2.0 = trading at twice yesterday's pace.
    day_fraction = max(elapsed / config.SESSION_MINUTES, 0.05)
    rel_volume = (q.day_volume / q.prev_volume) / day_fraction if q.prev_volume else 0.0
    return {
        "elapsed_min": elapsed,
        "day_chg": pct(q.price, q.prev_close),        # vs yesterday's close
        "intraday_chg": pct(q.price, q.day_open),     # vs today's open
        "gap": pct(q.day_open, q.prev_close),         # how it opened vs yesterday
        "vwap_dist": pct(q.price, q.vwap),            # above (+) or below (-) today's VWAP
        "rel_volume": rel_volume,
        "mom_5m": momentum(5),
        "mom_15m": momentum(15),
        "or_high": orange[0] if orange else None,     # opening range: first 15 minutes
        "or_low": orange[1] if orange else None,
        "spread_pct": q.spread_pct,
        "price": q.price,
    }
