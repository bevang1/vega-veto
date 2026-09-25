"""
The live market feed: real Solana token data from DexScreener's public API.

No API key and no wallet are needed; it's read-only market data. Endpoints
used (limits from https://docs.dexscreener.com/api/reference):
  GET /token-boosts/latest/v1      tokens recently promoted on DexScreener  (60 req/min)
  GET /token-boosts/top/v1         tokens with the most active promotion   (60 req/min)
  GET /token-profiles/latest/v1    tokens that recently set up a profile   (60 req/min)
  GET /latest/dex/tokens/{a,b,..}  live pair data, up to 30 tokens a call  (300 req/min)

Note what "discovery" means here: boosted/profiled tokens are ones someone
PAID to promote. That's where attention is, and it's also where a lot of
the scams are. The risk engine exists for exactly that reason.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request

from . import config
from .models import TokenSnapshot

API = "https://api.dexscreener.com"
SOL_MINT = "So11111111111111111111111111111111111111112"  # wrapped SOL
DISCOVERY_PATHS = ["/token-boosts/latest/v1", "/token-boosts/top/v1", "/token-profiles/latest/v1"]


class DexScreenerFeed:
    name = "DexScreener (live Solana data)"
    is_live = True
    tick_seconds = config.LIVE_TICK_SECONDS

    def __init__(self) -> None:
        self.tracked: list[str] = []      # token addresses we poll, newest first
        self.sol_usd = 0.0
        self.status = "starting"
        self._tick = 0

    def now(self) -> float:
        return time.time()

    # --- HTTP -----------------------------------------------------------------
    def _get(self, path: str):
        req = urllib.request.Request(API + path, headers={"User-Agent": "paper-trader/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.status = "rate limited by DexScreener: backing off"
                time.sleep(15)
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

    # --- Discovery ------------------------------------------------------------
    def discover(self, keep: set[str]) -> None:
        """Add newly promoted Solana tokens; drop the oldest beyond the cap."""
        found: list[str] = []
        for path in DISCOVERY_PATHS:
            items = self._get(path) or []
            for item in items if isinstance(items, list) else []:
                if item.get("chainId") == "solana" and item.get("tokenAddress"):
                    found.append(item["tokenAddress"])
        merged = list(dict.fromkeys(found + self.tracked))  # dedupe, keep order
        # Tokens we hold are never dropped, or we'd lose track of our own bags.
        must = [a for a in merged if a in keep]
        rest = [a for a in merged if a not in keep]
        self.tracked = must + rest[: max(0, config.MAX_TRACKED_TOKENS - len(must))]

    # --- Pricing --------------------------------------------------------------
    def poll(self, keep: set[str] = frozenset()) -> dict[str, TokenSnapshot]:
        if self._tick % config.DISCOVERY_EVERY_TICKS == 0:
            self.discover(set(keep))
        self._tick += 1

        addresses = [SOL_MINT] + [a for a in self.tracked if a != SOL_MINT]
        best: dict[str, dict] = {}
        for i in range(0, len(addresses), 30):
            data = self._get("/latest/dex/tokens/" + ",".join(addresses[i:i + 30])) or {}
            for pair in data.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue
                addr = (pair.get("baseToken") or {}).get("address")
                liq = ((pair.get("liquidity") or {}).get("usd")) or 0
                # A token can trade in several pools; use the deepest one,
                # since that's where a real swap router would send most of it.
                if addr and liq >= ((best.get(addr) or {}).get("liquidity") or {}).get("usd", -1):
                    best[addr] = pair

        sol_pair = best.pop(SOL_MINT, None)
        if sol_pair:
            self.sol_usd = float(sol_pair.get("priceUsd") or self.sol_usd)
        snapshots = {a: TokenSnapshot.from_dexscreener(p) for a, p in best.items()}
        if snapshots:
            self.status = f"ok: {len(snapshots)} tokens"
        return snapshots
