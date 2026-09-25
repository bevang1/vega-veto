"""
A simulated memecoin market, for learning the tool offline.

Run with --demo. Tokens are born, pump, bleed, chop around, or get rugged,
roughly in the proportions you see in real memecoin markets (most die). The
dashboard labels this mode SIMULATED MARKET so you never confuse it with
real data. Profits here mean nothing about real markets: we wrote the rules.
"""

from __future__ import annotations

import math
import random
import string
from collections import deque

from . import config
from .models import TokenSnapshot

# Each token secretly gets a "fate" that drives its price path.
# (drift per tick, volatility per tick, chance of being picked)
FATES = {
    "rug":    (0.004, 0.030, 0.30),   # pumps a bit to attract buyers, then liquidity is pulled
    "bleed":  (-0.004, 0.025, 0.30),  # slow death, the most common real outcome
    "chop":   (0.000, 0.030, 0.20),
    "pump":   (0.010, 0.035, 0.15),   # a real run, then fades
    "moon":   (0.018, 0.040, 0.05),   # rare big winner
}
WINDOW_M5 = 30   # ticks in 5 minutes when a tick = 10 market seconds
WINDOW_H1 = 360


def _symbol(rng: random.Random) -> str:
    stems = ["DOG", "CAT", "PEPE", "WIF", "BONK", "FROG", "MOON", "CHAD", "GIGA", "NEKO",
             "BAT", "APE", "SHIB", "GOAT", "HAM", "RAT", "BEAR", "DUCK", "PNUT", "FISH"]
    return rng.choice(stems) + rng.choice(["", "", "AI", "INU", "X", "2", "SOL", "KING"])


class _SimToken:
    def __init__(self, rng: random.Random, born_at: float) -> None:
        self.rng = rng
        self.address = "".join(rng.choices(string.ascii_letters + string.digits, k=44))
        self.symbol = _symbol(rng)
        names, weights = zip(*[(k, v[2]) for k, v in FATES.items()])
        self.fate = rng.choices(names, weights)[0]
        self.drift, self.vol, _ = FATES[self.fate]
        self.price = rng.uniform(0.00001, 0.002)
        self.liquidity = rng.uniform(15_000, 250_000)
        self.born_at = born_at
        self.age_ticks = 0
        self.turn_at = rng.randint(120, 600)   # when the story changes (pump fades, rug pulls)
        self.dead = False
        self.prices: deque[float] = deque(maxlen=WINDOW_H1 + 1)
        self.flow: deque[tuple[int, int, float]] = deque(maxlen=WINDOW_H1)  # (buys, sells, usd volume)

    def step(self) -> None:
        self.age_ticks += 1
        drift = self.drift
        if self.age_ticks > self.turn_at:
            if self.fate == "rug" and not self.dead:
                self.liquidity *= 0.05     # the pull: 95% of liquidity gone in one tick
                self.price *= 0.10
                self.dead = True
            elif self.fate in ("pump", "moon"):
                drift = -0.006             # every pump fades eventually
        if self.dead:
            drift = -0.01
        ret = drift + self.vol * self.rng.gauss(0, 1)
        ret = max(ret, -0.6)
        self.price *= 1 + ret
        self.liquidity *= 1 + ret * 0.3    # liquidity follows price, but less
        self.prices.append(self.price)

        # Transactions: more activity when moving, buys dominate on up-ticks.
        activity = max(1.0, self.rng.gauss(8 + 300 * abs(ret), 3))
        buy_share = min(0.9, max(0.1, 0.5 + ret * 8 + self.rng.gauss(0, 0.08)))
        buys = int(activity * buy_share)
        sells = int(activity * (1 - buy_share))
        usd = (buys + sells) * self.rng.uniform(40, 400)
        self.flow.append((buys, sells, usd))

    def _pct_change(self, ticks: int) -> float:
        if len(self.prices) < 2:
            return 0.0
        past = self.prices[max(0, len(self.prices) - 1 - ticks)]
        return (self.price / past - 1) * 100

    def snapshot(self, now: float) -> TokenSnapshot:
        m5 = list(self.flow)[-WINDOW_M5:]
        h1 = list(self.flow)
        return TokenSnapshot(
            address=self.address, symbol=self.symbol, name=f"{self.symbol} (simulated)",
            pair_address="sim-" + self.address[:8], dex="simulated", url="",
            price_usd=self.price, liquidity_usd=self.liquidity,
            market_cap=self.price * 1_000_000_000,
            created_at_ms=self.born_at * 1000,
            vol_m5=sum(f[2] for f in m5), vol_h1=sum(f[2] for f in h1), vol_h24=sum(f[2] for f in h1) * 5,
            buys_m5=sum(f[0] for f in m5), sells_m5=sum(f[1] for f in m5),
            buys_h1=sum(f[0] for f in h1), sells_h1=sum(f[1] for f in h1),
            chg_m5=self._pct_change(WINDOW_M5), chg_h1=self._pct_change(WINDOW_H1),
            chg_h24=self._pct_change(WINDOW_H1),
        )


class DemoFeed:
    name = "SIMULATED MARKET (demo)"
    is_live = False
    tick_seconds = config.DEMO_TICK_SECONDS

    def __init__(self, seed: int | None = None, n_tokens: int = 60) -> None:
        self.rng = random.Random(seed)
        self.clock = 1_750_000_000.0        # market time, advanced 10s per tick
        self.sol_usd = 150.0
        self.status = "ok: simulated"
        # Start with tokens of mixed ages so the market isn't all brand new.
        self.tokens: list[_SimToken] = []
        for _ in range(n_tokens):
            t = _SimToken(self.rng, self.clock)
            for _ in range(self.rng.randint(0, 400)):
                t.step()
            t.born_at = self.clock - t.age_ticks * config.DEMO_SIM_SECONDS_PER_TICK
            self.tokens.append(t)
        self.n_tokens = n_tokens

    def now(self) -> float:
        return self.clock

    def poll(self, keep: set[str] = frozenset()) -> dict[str, TokenSnapshot]:
        self.clock += config.DEMO_SIM_SECONDS_PER_TICK
        self.sol_usd *= 1 + self.rng.gauss(0, 0.0008)
        for t in self.tokens:
            t.step()
        # Dead tokens fall off the screener after a while, like on the real
        # feed, even if you still hold them. The engine must cope with that.
        self.tokens = [t for t in self.tokens
                       if not (t.dead and t.age_ticks > t.turn_at + 60) and t.liquidity > 500]
        while len(self.tokens) < self.n_tokens:
            self.tokens.append(_SimToken(self.rng, self.clock))
        return {t.address: t.snapshot(self.clock) for t in self.tokens if not math.isnan(t.price)}
