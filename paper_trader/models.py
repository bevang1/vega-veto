"""
The shapes of data that flow through the desk.

An AssetSnapshot is one reading of one coin's market at one moment: the
live price, the order book's best bid and ask, 24h volume, and the recent
one-minute candles. Both the live feed (Coinbase) and the demo feed produce
these, so everything downstream works the same whichever feed is plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _num(value, default: float = 0.0) -> float:
    """Exchanges send some numbers as strings and omit others. Be forgiving."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Candle:
    """One minute of trading: where price opened, went, and closed, and how much traded."""
    t: float          # start of the minute (unix seconds)
    open: float
    high: float
    low: float
    close: float
    volume: float     # in coins (e.g. BTC), not dollars

    @classmethod
    def from_coinbase(cls, row) -> "Candle":
        # Coinbase sends each candle as [time, low, high, open, close, volume].
        t, low, high, open_, close, volume = (_num(x) for x in row[:6])
        return cls(t=t, open=open_, high=high, low=low, close=close, volume=volume)


@dataclass
class AssetSnapshot:
    symbol: str                 # "BTC"
    product_id: str             # "BTC-USD": the order book it trades on
    name: str
    url: str
    price_usd: float            # last traded price
    bid: float                  # best price someone will BUY from you at (you sell here)
    ask: float                  # best price someone will SELL to you at (you buy here)
    volume_24h_usd: float
    chg_24h: float              # % price change over 24 hours
    candles: list[Candle] = field(default_factory=list)   # 1-minute, oldest first

    @property
    def spread_pct(self) -> float:
        """The gap between bid and ask, as % of price. A round trip pays it once."""
        if self.bid > 0 and self.ask > 0 and self.price_usd > 0:
            return (self.ask - self.bid) / self.price_usd * 100
        return 0.0

    @classmethod
    def from_coinbase(cls, product_id: str, ticker: dict, stats: dict | None,
                      candles: list[Candle]) -> "AssetSnapshot":
        symbol = product_id.split("-")[0]
        price = _num(ticker.get("price"))
        stats = stats or {}
        opened = _num(stats.get("open"))
        # Ticker volume is the last 24h in coins; convert it to dollars.
        volume = _num(ticker.get("volume") or stats.get("volume"))
        return cls(
            symbol=symbol, product_id=product_id, name=symbol,
            url=f"https://www.coinbase.com/advanced-trade/spot/{product_id}",
            price_usd=price, bid=_num(ticker.get("bid")), ask=_num(ticker.get("ask")),
            volume_24h_usd=volume * price,
            chg_24h=(price / opened - 1) * 100 if opened > 0 and price > 0 else 0.0,
            candles=candles,
        )


@dataclass
class Position:
    """A paper holding: which agent owns how many coins, and what it cost."""
    agent: str
    symbol: str
    qty: float                  # coins held
    cost_usd: float             # everything spent, including fees
    entry_price_usd: float      # the price we actually paid, after spread, impact and fees
    opened_at: float            # market time (seconds)
    entry_reason: str
    peak_price_usd: float = 0.0


@dataclass
class Order:
    """An order waiting for the next market update to fill (simulated latency)."""
    agent: str
    side: str                   # "BUY" or "SELL"
    symbol: str
    usd_amount: float = 0.0     # for buys: dollars to spend
    reason: str = ""            # plain-English why, shown on the dashboard
    details: dict = field(default_factory=dict)
    submitted_at: float = 0.0
    decision_price_usd: float = 0.0
