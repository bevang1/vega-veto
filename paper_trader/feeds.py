"""
The live market feed: real crypto prices from Coinbase's public market data API.

No API key and no account are needed; it's read-only market data, and it
works from the US (unlike Binance's global API). Endpoints used, per coin
(docs: https://docs.cdp.coinbase.com/exchange/reference):
  GET /products/{BTC-USD}/ticker                   last price, best bid/ask, 24h volume
  GET /products/{BTC-USD}/candles?granularity=60   the last 300 one-minute candles
  GET /products/{BTC-USD}/stats                    24h open/high/low (for the 24h change)

We trade a fixed list of major coins (config.UNIVERSE) rather than
"discovering" whatever is being promoted. That's a deliberate choice: deep,
liquid markets where the price you see is close to the price you get.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request

from . import config
from .models import AssetSnapshot, Candle

API = "https://api.exchange.coinbase.com"


class CoinbaseFeed:
    name = "Coinbase (live crypto data)"
    is_live = True
    tick_seconds = config.LIVE_TICK_SECONDS

    def __init__(self, symbols: list[str] | None = None) -> None:
        self.products = [f"{s}-{config.QUOTE_CURRENCY}" for s in (symbols or config.UNIVERSE)]
        self.candles: dict[str, list[Candle]] = {}
        self.stats: dict[str, dict] = {}
        self.status = "starting"
        self._tick = 0

    def now(self) -> float:
        return time.time()

    # --- HTTP -----------------------------------------------------------------
    def _get(self, path: str):
        # Coinbase rejects requests that don't send a User-Agent.
        req = urllib.request.Request(API + path, headers={"User-Agent": "vega-veto-paper-desk/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.status = "rate limited by Coinbase: backing off"
                time.sleep(5)
            else:
                self.status = f"HTTP {e.code} from {path}"
        except (urllib.error.URLError, ssl.SSLError) as e:
            reason = str(getattr(e, "reason", e))
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                # Common on macOS with the python.org installer. See README.
                self.status = "SSL certificates missing: run 'Install Certificates.command' (see README)"
            else:
                self.status = f"network error: {reason}"
        except (TimeoutError, json.JSONDecodeError) as e:
            self.status = f"bad response: {e}"
        return None

    # --- Pricing --------------------------------------------------------------
    def poll(self, keep: set[str] = frozenset()) -> dict[str, AssetSnapshot]:
        refresh_stats = self._tick % config.STATS_EVERY_TICKS == 0
        self._tick += 1
        snaps: dict[str, AssetSnapshot] = {}
        for pid in self.products:
            rows = self._get(f"/products/{pid}/candles?granularity=60")
            if isinstance(rows, list) and rows:
                # Newest first from the API; we keep them oldest first.
                parsed = sorted((Candle.from_coinbase(r) for r in rows if len(r) >= 6), key=lambda c: c.t)
                self.candles[pid] = parsed[-config.HISTORY_MINUTES:]
            if refresh_stats or pid not in self.stats:
                stats = self._get(f"/products/{pid}/stats")
                if isinstance(stats, dict):
                    self.stats[pid] = stats
            ticker = self._get(f"/products/{pid}/ticker")
            if not isinstance(ticker, dict):
                continue
            snap = AssetSnapshot.from_coinbase(pid, ticker, self.stats.get(pid), self.candles.get(pid, []))
            if snap.price_usd > 0:
                snaps[snap.symbol] = snap
        if snaps:
            # "N of M" shows coverage: a gap means data is going missing.
            self.status = f"ok: {len(snaps)} of {len(self.products)} coins"
        return snaps
