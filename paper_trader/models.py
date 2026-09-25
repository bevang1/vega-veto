"""
The shapes of data that flow through the desk.

A TokenSnapshot is one reading of one token's market at one moment. Both the
live feed (DexScreener) and the demo feed produce these, so everything
downstream works the same whichever feed is plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _num(value, default: float = 0.0) -> float:
    """DexScreener sends some numbers as strings and omits others. Be forgiving."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class TokenSnapshot:
    address: str
    symbol: str
    name: str
    pair_address: str
    dex: str
    url: str
    price_usd: float
    liquidity_usd: float
    market_cap: float
    created_at_ms: float        # when the trading pair was created
    vol_m5: float               # USD volume, last 5 minutes
    vol_h1: float               # USD volume, last hour
    vol_h24: float
    buys_m5: int                # number of buy transactions, last 5 minutes
    sells_m5: int
    buys_h1: int
    sells_h1: int
    chg_m5: float               # % price change, last 5 minutes
    chg_h1: float
    chg_h24: float
    quote_symbol: str = "SOL"
    price_native: float = 0.0   # price in the quote token (SOL for most memecoin pairs)

    @classmethod
    def from_dexscreener(cls, pair: dict) -> "TokenSnapshot":
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        txns = pair.get("txns") or {}
        volume = pair.get("volume") or {}
        change = pair.get("priceChange") or {}
        liquidity = pair.get("liquidity") or {}
        m5, h1 = txns.get("m5") or {}, txns.get("h1") or {}
        return cls(
            address=base.get("address", ""),
            symbol=base.get("symbol", "?"),
            name=base.get("name", ""),
            pair_address=pair.get("pairAddress", ""),
            dex=pair.get("dexId", ""),
            url=pair.get("url", ""),
            price_usd=_num(pair.get("priceUsd")),
            price_native=_num(pair.get("priceNative")),
            quote_symbol=quote.get("symbol", ""),
            liquidity_usd=_num(liquidity.get("usd")),
            market_cap=_num(pair.get("marketCap") or pair.get("fdv")),
            created_at_ms=_num(pair.get("pairCreatedAt")),
            vol_m5=_num(volume.get("m5")),
            vol_h1=_num(volume.get("h1")),
            vol_h24=_num(volume.get("h24")),
            buys_m5=int(_num(m5.get("buys"))),
            sells_m5=int(_num(m5.get("sells"))),
            buys_h1=int(_num(h1.get("buys"))),
            sells_h1=int(_num(h1.get("sells"))),
            chg_m5=_num(change.get("m5")),
            chg_h1=_num(change.get("h1")),
            chg_h24=_num(change.get("h24")),
        )


@dataclass
class Position:
    """A paper holding: which agent owns how many tokens, and what it cost."""
    agent: str
    address: str
    symbol: str
    tokens: float
    cost_sol: float             # everything spent, including fees
    entry_price_usd: float      # the price we actually paid, after slippage
    entry_liquidity_usd: float
    opened_at: float            # market time (seconds)
    entry_reason: str
    peak_price_usd: float = 0.0
    missing_ticks: int = 0      # how many updates in a row the token was absent


@dataclass
class Order:
    """An order waiting for the next market update to fill (simulated latency)."""
    agent: str
    side: str                   # "BUY" or "SELL"
    address: str
    symbol: str
    sol_amount: float = 0.0     # for buys: SOL to spend
    reason: str = ""            # plain-English why, shown on the dashboard
    details: dict = field(default_factory=dict)
    submitted_at: float = 0.0
    decision_price_usd: float = 0.0
    retries: int = 0            # updates a sell has waited for the token's data to come back
