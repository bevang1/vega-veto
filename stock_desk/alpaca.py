"""
A tiny Alpaca client: just the calls the desk needs, standard library only.

Endpoints and headers are taken from Alpaca's official SDK (alpaca-py):
  Trading (paper):  https://paper-api.alpaca.markets/v2/...
  Market data:      https://data.alpaca.markets/v2/stocks/...  and  /v1beta1/screener/...
  Auth headers:     APCA-API-KEY-ID and APCA-API-SECRET-KEY

The trading URL is hard-wired to PAPER in config.py. Keys for a paper account
can't touch real money, which is the point.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import config


class AlpacaError(Exception):
    pass


class Alpaca:
    def __init__(self, key_id: str, secret: str) -> None:
        if not key_id or not secret:
            raise AlpacaError("Alpaca keys missing: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret,
                        "Accept": "application/json", "User-Agent": "vega-veto-stock-desk"}

    def _req(self, method: str, url: str, params: dict | None = None, body: dict | None = None):
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise AlpacaError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise AlpacaError(f"{method} {url.split('?')[0]} -> network error: {e}") from None

    # --- Trading (paper) ---------------------------------------------------------
    def _t(self, path: str) -> str:
        return f"{config.TRADING_URL}/v2{path}"

    def account(self) -> dict:
        return self._req("GET", self._t("/account"))

    def clock(self) -> dict:
        """{'timestamp', 'is_open', 'next_open', 'next_close'}"""
        return self._req("GET", self._t("/clock"))

    def submit_order(self, symbol: str, side: str, client_order_id: str,
                     notional: float | None = None, qty: float | None = None) -> dict:
        body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day",
                "client_order_id": client_order_id}
        # Buys use a dollar amount (fractional shares); sells use the exact share count.
        if notional is not None:
            body["notional"] = f"{notional:.2f}"
        else:
            body["qty"] = f"{qty:.9f}".rstrip("0").rstrip(".")
        return self._req("POST", self._t("/orders"), body=body)

    def get_order(self, order_id: str) -> dict:
        return self._req("GET", self._t(f"/orders/{order_id}"))

    def orders_since(self, after_iso: str) -> list[dict]:
        """Every order submitted after a time, oldest first (pages of 500)."""
        out: list[dict] = []
        after = after_iso
        while True:
            page = self._req("GET", self._t("/orders"), params={
                "status": "all", "after": after, "direction": "asc", "limit": 500}) or []
            out += page
            if len(page) < 500:
                return out
            after = page[-1]["submitted_at"]

    def close_all_positions(self) -> None:
        self._req("DELETE", self._t("/positions"), params={"cancel_orders": "true"})

    # --- Market data ----------------------------------------------------------------
    def snapshots(self, symbols: list[str]) -> dict:
        """{symbol: {latestTrade, latestQuote, minuteBar, dailyBar, prevDailyBar}}"""
        out: dict = {}
        for i in range(0, len(symbols), 100):
            out.update(self._req("GET", f"{config.DATA_URL}/v2/stocks/snapshots", params={
                "symbols": ",".join(symbols[i:i + 100]), "feed": config.DATA_FEED}) or {})
        return out

    def minute_bars(self, symbols: list[str], start_iso: str) -> dict[str, list[dict]]:
        """1-minute bars since `start_iso` for many symbols (follows page tokens)."""
        out: dict[str, list[dict]] = {}
        token = None
        while True:
            page = self._req("GET", f"{config.DATA_URL}/v2/stocks/bars", params={
                "symbols": ",".join(symbols), "timeframe": "1Min", "start": start_iso,
                "feed": config.DATA_FEED, "limit": 10000, "page_token": token}) or {}
            for sym, bars in (page.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = page.get("next_page_token")
            if not token:
                return out

    def most_actives(self, top: int) -> list[str]:
        data = self._req("GET", f"{config.DATA_URL}/v1beta1/screener/stocks/most-actives",
                         params={"by": "volume", "top": top}) or {}
        return [row["symbol"] for row in data.get("most_actives") or [] if row.get("symbol")]
