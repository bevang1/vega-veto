"""
Turn a coin's recent candles into the handful of numbers agents look at.

These are standard, well-known indicators (moving averages, RSI, Bollinger
z-score, breakouts), computed from scratch so you can see the maths.
Keeping this separate means every agent sees the same features, and the
dashboard can show exactly the values that triggered a trade.
"""

from __future__ import annotations

import math
import statistics

from .models import AssetSnapshot


def ema(values: list[float], period: int) -> float:
    """Exponential moving average: an average that weights recent prices more."""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index, 0-100 (Wilder's method).

    Above 70 = "overbought" (it has gone up a lot, fast); below 30 =
    "oversold". 50 = gains and losses have been about equal.
    """
    if len(closes) <= period:
        return 50.0
    gains = [max(b - a, 0) for a, b in zip(closes, closes[1:])]
    losses = [max(a - b, 0) for a, b in zip(closes, closes[1:])]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def pct(now: float, then: float) -> float:
    return (now / then - 1) * 100 if then > 0 else 0.0


def features(snap: AssetSnapshot, now: float) -> dict:
    candles = snap.candles
    # The newest candle is still forming; use the live price as its close.
    closes = [c.close for c in candles]
    if closes and snap.price_usd > 0:
        closes[-1] = snap.price_usd
    price = snap.price_usd
    n = len(closes)

    def ago(minutes: int) -> float:
        return closes[-1 - minutes] if n > minutes else (closes[0] if closes else price)

    last_hour = closes[-60:] or [price]
    mean60 = statistics.fmean(last_hour)
    sd60 = statistics.pstdev(last_hour) if len(last_hour) > 1 else 0.0
    ema_fast, ema_slow = ema(closes, 20), ema(closes, 60)
    # RSI on 5-minute bars (every 5th close): one-minute RSI is mostly noise.
    five_min = closes[::-1][::5][::-1]
    # Highest high of the PREVIOUS hour, not counting the forming candle.
    prior = candles[-61:-1]
    prior_high = max((c.high for c in prior), default=0.0)

    # Up vs down volume, last 5 minutes: volume on candles that closed higher
    # counts as buying pressure, lower as selling. A rough but honest proxy
    # (exchanges' public candles don't say who was the aggressor).
    up = sum(c.volume * c.close for c in candles[-5:] if c.close >= c.open)
    down = sum(c.volume * c.close for c in candles[-5:] if c.close < c.open)
    # Volume acceleration: trading pace in the last 5 minutes vs the average
    # pace over our whole history. 1.0 = normal; 3.0 = three times busier.
    per_min_recent = sum(c.volume for c in candles[-5:]) / 5 if candles else 0.0
    per_min_avg = sum(c.volume for c in candles) / max(len(candles), 1)
    returns = [b / a - 1 for a, b in zip(last_hour, last_hour[1:]) if a > 0]

    f = {
        "history_min": n,
        "chg_m5": pct(price, ago(5)),
        "chg_h1": pct(price, ago(60)),
        "chg_h24": snap.chg_24h,
        # Trend: how far the 20-minute average is above the 60-minute one.
        "trend_pct": pct(ema_fast, ema_slow),
        "px_vs_ema20": pct(price, ema_fast),
        "rsi": rsi(five_min),
        # Z-score: how many standard deviations price is from its hourly mean.
        # -2 = at the lower Bollinger band ("stretched to the downside").
        "zscore": (price - mean60) / sd60 if sd60 > 0 else 0.0,
        "breakout_pct": pct(price, prior_high) if prior_high else 0.0,
        "vol_accel": per_min_recent / per_min_avg if per_min_avg > 0 else 1.0,
        # Capped at 100x so five green candles in a row don't divide by ~zero.
        "up_down_ratio": up / max(down, (up + down) * 0.01) if (up or down) else 1.0,
        "up_vol_usd": up,
        "down_vol_usd": down,
        "volatility_h1": (statistics.pstdev(returns) * 100) if len(returns) > 1 else 0.0,
        "spread_pct": snap.spread_pct,
        "volume_24h": snap.volume_24h_usd,
        "rs_h1": 0.0,   # filled in by add_relative_strength()
    }
    f["score"] = signal_score(f)
    return f


def add_relative_strength(feats: dict[str, dict], benchmark: str) -> None:
    """rs_h1 = how much a coin beat the benchmark (BTC) over the last hour, in % points.

    Crypto mostly moves with BTC. A coin rising while BTC is flat is showing
    its own strength; a coin rising only because BTC is rising isn't.
    """
    base = feats.get(benchmark)
    for sym, f in feats.items():
        f["rs_h1"] = f["chg_h1"] - base["chg_h1"] if base and sym != benchmark else 0.0


def signal_score(f: dict) -> float:
    """A 0-100 'how strong is this' number, used only to draw the dashboard.

    Agents do NOT trade on this: each agent has its own explicit rules.
    """
    def squash(x: float, scale: float) -> float:
        return 1 / (1 + math.exp(-x / scale))

    trend = squash(f["trend_pct"], 0.3)
    momentum = squash(f["chg_h1"], 1.5)
    pressure = squash(math.log(max(f["up_down_ratio"], 1e-3)), 0.5)
    accel = squash(f["vol_accel"] - 1, 1)
    return round(100 * (0.30 * trend + 0.30 * momentum + 0.25 * pressure + 0.15 * accel), 1)
