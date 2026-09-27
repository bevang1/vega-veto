"""
A simulated market of major coins, for learning the tool offline.

Run with --demo. Each tick is one market minute and produces one candle per
coin. Every coin follows BTC to a degree (its "beta"), plus its own noise,
and the market drifts between regimes: calm ranges, trends up, trends down,
and the occasional sharp shock. It's livelier than the real thing so you
see trades within minutes. The dashboard labels this mode SIMULATED MARKET
so you never confuse it with real data. Profits here mean nothing about
real markets: we wrote the rules.
"""

from __future__ import annotations

import random
from collections import deque

from . import config
from .models import AssetSnapshot, Candle

# symbol: (start price, per-minute volatility, beta to BTC, 24h volume USD, spread %)
COINS = {
    "BTC":  (65_000.0, 0.0012, 1.0, 900_000_000, 0.01),
    "ETH":  (3_200.0,  0.0016, 1.1, 450_000_000, 0.01),
    "SOL":  (150.0,    0.0024, 1.3, 180_000_000, 0.02),
    "XRP":  (0.55,     0.0022, 1.1, 120_000_000, 0.03),
    "LTC":  (80.0,     0.0020, 1.0,  40_000_000, 0.04),
    "ADA":  (0.45,     0.0024, 1.2,  35_000_000, 0.05),
    "AVAX": (30.0,     0.0028, 1.3,  30_000_000, 0.05),
    "LINK": (14.0,     0.0026, 1.2,  45_000_000, 0.04),
    "DOT":  (6.5,      0.0026, 1.2,  20_000_000, 0.06),
    "BCH":  (400.0,    0.0022, 1.0,  25_000_000, 0.05),
}
# Market regimes: (BTC drift per minute, volatility multiplier, typical length in minutes)
REGIMES = {
    "range":      (0.0000,  1.0, 120),
    "trend_up":   (0.0006,  1.1, 90),
    "trend_down": (-0.0006, 1.2, 90),
    "shock":      (-0.0030, 2.5, 8),    # a quick flush, the kind mean reversion hunts
}


class _SimCoin:
    def __init__(self, rng: random.Random, symbol: str) -> None:
        self.rng = rng
        self.symbol = symbol
        self.price, self.vol, self.beta, self.volume_24h, self.spread_pct = COINS[symbol]
        self.price *= rng.uniform(0.9, 1.1)
        self.drift = 0.0            # the coin's own story, on top of the market's
        self.candles: list[Candle] = []
        # Closes over the last day, so the 24h change is a real 24h change
        # (once the demo has run that long; before that, since it started).
        self.day: deque[float] = deque(maxlen=1440)

    def step(self, t: float, market_ret: float, vol_mult: float) -> None:
        if self.rng.random() < 0.01:   # now and then the coin gets its own narrative
            self.drift = self.rng.choice([0.0, 0.0, 0.0008, -0.0006])
        open_ = self.price
        high = low = open_
        # Four sub-steps make each candle's high/low wider than its open/close.
        for _ in range(4):
            own = self.drift / 4 + self.vol * vol_mult * self.rng.gauss(0, 1) / 2
            self.price *= 1 + self.beta * market_ret / 4 + own
            high, low = max(high, self.price), min(low, self.price)
        move = abs(self.price / open_ - 1)
        # Busier minutes when price moves more (and in hectic regimes), like
        # real markets: big moves come with big volume.
        busy = vol_mult * (0.4 + move / self.vol) ** 1.5
        usd = self.volume_24h / 1440 * max(0.1, busy * self.rng.gauss(1, 0.25))
        self.candles.append(Candle(t, open_, high, low, self.price, usd / self.price))
        self.candles = self.candles[-config.HISTORY_MINUTES:]
        self.day.append(open_)

    def snapshot(self) -> AssetSnapshot:
        half = self.price * self.spread_pct / 100 / 2
        return AssetSnapshot(
            symbol=self.symbol, product_id=f"{self.symbol}-USD", name=f"{self.symbol} (simulated)",
            url="", price_usd=self.price, bid=self.price - half, ask=self.price + half,
            volume_24h_usd=self.volume_24h, chg_24h=(self.price / self.day[0] - 1) * 100,
            candles=list(self.candles),
        )


class DemoFeed:
    name = "SIMULATED MARKET (demo)"
    is_live = False
    tick_seconds = config.DEMO_TICK_SECONDS

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.clock = 1_750_000_000.0        # market time, advanced one minute per tick
        self.status = "ok: simulated"
        self.coins = [_SimCoin(self.rng, s) for s in COINS]
        self.regime, self.regime_left = "range", 60
        # Pre-fill history so the indicators are ready from the first tick.
        for _ in range(config.HISTORY_MINUTES):
            self._step()

    def now(self) -> float:
        return self.clock

    def _step(self) -> None:
        self.regime_left -= 1
        if self.regime_left <= 0:
            self.regime = self.rng.choices(list(REGIMES), [0.45, 0.25, 0.2, 0.1])[0]
            self.regime_left = max(3, int(self.rng.expovariate(1 / REGIMES[self.regime][2])))
        drift, vol_mult, _ = REGIMES[self.regime]
        market_ret = drift + 0.0010 * vol_mult * self.rng.gauss(0, 1)
        self.clock += config.DEMO_SIM_SECONDS_PER_TICK
        for c in self.coins:
            c.step(self.clock, market_ret, vol_mult)

    def poll(self, keep: set[str] = frozenset()) -> dict[str, AssetSnapshot]:
        self._step()
        self.status = f"ok: simulated · regime {self.regime.replace('_', ' ')}"
        return {c.symbol: c.snapshot() for c in self.coins}
