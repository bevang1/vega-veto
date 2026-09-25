"""Stock desk tests against a fake Alpaca that simulates one trading day."""

from __future__ import annotations

import itertools
import math
from datetime import datetime, timedelta, timezone

import pytest

from stock_desk import config
from stock_desk.config import RiskLimits
from stock_desk.engine import StockDesk
from stock_desk.ledger import Book, Lot, rebuild
from stock_desk.market import Quote, parse_time
from stock_desk.report import update_text
from stock_desk.risk import check_entry, check_exit

OPEN = datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc)    # 09:30 New York (EDT)
CLOSE = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
PREV_CLOSE = {"AAA": 100.0, "BBB": 100.0, "CCC": 50.0}


def price(sym: str, t: datetime) -> float:
    m = max(0.0, (t - OPEN).total_seconds() / 60)
    if sym == "AAA":   # gaps up 3%, then trends up with small wiggles
        return 103 * (1 + 0.0002 * m) * (1 + 0.002 * math.sin(m / 3))
    if sym == "BBB":   # sells off to -4% by minute 60, then recovers
        return 100 * (1 - 0.04 * min(m, 60) / 60 + 0.0006 * max(0, m - 60))
    return 50 * (1 + 0.001 * math.sin(m / 7))   # CCC: flat


class FakeAlpaca:
    def __init__(self, now: datetime):
        self.now = now
        self.orders: dict[str, dict] = {}
        self.ids = itertools.count(1)

    def _bars(self, sym, start, end):
        t = start.replace(second=0, microsecond=0)
        out = []
        while t + timedelta(minutes=1) <= end:
            p0, p1 = price(sym, t), price(sym, t + timedelta(minutes=1))
            out.append({"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": p0, "h": max(p0, p1) * 1.0005,
                        "l": min(p0, p1) * 0.9995, "c": p1, "v": 1000, "vw": (p0 + p1) / 2})
            t += timedelta(minutes=1)
        return out

    def clock(self):
        return {"is_open": OPEN <= self.now < CLOSE, "next_open": "2026-09-25T13:30:00Z",
                "next_close": CLOSE.strftime("%Y-%m-%dT%H:%M:%SZ")}

    def account(self):
        return {"equity": "100000", "last_equity": "100000"}

    def most_actives(self, top):
        return []

    def minute_bars(self, symbols, start_iso):
        return {s: self._bars(s, parse_time(start_iso), self.now) for s in symbols if s in PREV_CLOSE}

    def snapshots(self, symbols):
        out = {}
        for s in symbols:
            if s not in PREV_CLOSE:
                continue
            bars = self._bars(s, OPEN, self.now) or self._bars(s, OPEN, OPEN + timedelta(minutes=1))
            p = price(s, self.now)
            elapsed = max(1, len(bars))
            out[s] = {
                "latestTrade": {"p": p},
                "latestQuote": {"bp": p * 0.9999, "ap": p * 1.0001},
                "minuteBar": bars[-1],
                "dailyBar": {"t": OPEN.strftime("%Y-%m-%dT04:00:00Z"), "o": price(s, OPEN),
                             "v": 3000 * elapsed, "vw": sum(b["c"] for b in bars) / elapsed},
                "prevDailyBar": {"c": PREV_CLOSE[s], "v": 390 * 1000},
            }
        return out

    def submit_order(self, symbol, side, client_order_id, notional=None, qty=None):
        oid = f"o{next(self.ids)}"
        p = price(symbol, self.now)
        fill = p * (1.0001 if side == "buy" else 0.9999)
        q = notional / fill if notional is not None else qty
        self.orders[oid] = {"id": oid, "client_order_id": client_order_id, "symbol": symbol, "side": side,
                            "status": "filled", "filled_qty": str(q), "filled_avg_price": str(fill),
                            "filled_at": self.now.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"),
                            "submitted_at": self.now.isoformat()}
        return {"id": oid}

    def get_order(self, oid):
        return self.orders[oid]

    def orders_since(self, after_iso):
        return list(self.orders.values())


@pytest.fixture(autouse=True)
def fake_universe(monkeypatch):
    # The fake market only knows these three made-up stocks.
    monkeypatch.setattr(config, "UNIVERSE", list(PREV_CLOSE))


def run_day(api: FakeAlpaca, until: datetime, desk: StockDesk | None = None) -> StockDesk:
    if desk is None:
        desk = StockDesk(api, now_fn=lambda: api.now)
        desk.start()
    while api.now < until:
        desk.tick()
        api.now += timedelta(seconds=30)
    return desk


# --- Engine over a whole simulated day -------------------------------------------------
def test_full_day_trades_and_is_flat_at_the_close():
    api = FakeAlpaca(OPEN + timedelta(seconds=30))
    desk = run_day(api, CLOSE)
    desk._check_pending()
    kinds = {e["kind"] for e in desk.events}
    assert "BUY" in kinds and "SELL" in kinds
    assert all(not b.lots for b in desk.books.values()), "everything is sold before the close"
    # Each playbook has a stock made for it in the fake market, so each should trade.
    assert {e["agent"] for e in desk.events if e["kind"] == "BUY"} == {a.name for a in desk.agents}
    for b in desk.books.values():
        assert b.cash >= -1e-6
    text = update_text(desk, 1, 0, "@me", api.account())
    assert "Desk P&L today" in text and "| MOMENTUM |" in text


def test_no_trades_in_the_first_15_minutes():
    api = FakeAlpaca(OPEN + timedelta(seconds=30))
    desk = run_day(api, OPEN + timedelta(minutes=14))
    assert not api.orders


def test_restart_mid_day_restores_every_agents_positions():
    api = FakeAlpaca(OPEN + timedelta(seconds=30))
    first = run_day(api, OPEN + timedelta(minutes=90))
    first._check_pending()
    before = {c: sorted(b.lots) for c, b in first.books.items()}
    realized = {c: round(b.realized, 6) for c, b in first.books.items()}
    second = StockDesk(api, now_fn=lambda: api.now)   # e.g. the afternoon job
    second.start()
    assert {c: sorted(b.lots) for c, b in second.books.items()} == before
    assert {c: round(b.realized, 6) for c, b in second.books.items()} == realized


# --- Ledger ---------------------------------------------------------------------------
def test_ledger_realised_pnl_and_cash():
    b = Book("MO", 25_000)
    b.apply_fill("AAA", "buy", 10, 100, OPEN)
    assert b.cash == 24_000
    pnl = b.apply_fill("AAA", "sell", 10, 103, OPEN)
    assert math.isclose(pnl, 30) and b.wins == 1 and not b.lots and math.isclose(b.cash, 25_030)


def test_rebuild_ignores_orders_without_our_tag():
    books = {"MO": Book("MO", 25_000)}
    orders = [{"client_order_id": "manual-123", "symbol": "AAA", "side": "buy", "filled_qty": "1",
               "filled_avg_price": "100", "filled_at": "2026-09-24T14:00:00Z"},
              {"client_order_id": "vv-MO-abc", "symbol": "AAA", "side": "buy", "filled_qty": "2",
               "filled_avg_price": "100", "filled_at": "2026-09-24T14:00:00Z"}]
    assert rebuild(books, orders) == 1 and books["MO"].lots["AAA"].qty == 2


# --- Risk -----------------------------------------------------------------------------
L = RiskLimits()
F = {"elapsed_min": 60, "price": 100, "spread_pct": 0.02}


def test_entry_sizes_at_20_percent_of_budget():
    size, blocked = check_entry(Book("MO", 25_000), "AAA", F, 200, OPEN, L)
    assert blocked == [] and size == 5_000


def test_entry_blocked_near_close_and_on_wide_spreads():
    _, blocked = check_entry(Book("MO", 25_000), "AAA", F, 20, OPEN, L)
    assert any("last" in b for b in blocked)
    _, blocked = check_entry(Book("MO", 25_000), "AAA", dict(F, spread_pct=1.0), 200, OPEN, L)
    assert any("spread" in b for b in blocked)


def lot():
    return Lot("AAA", 10, 100, OPEN, "", 100)


def test_exit_rules():
    assert check_exit(lot(), 98, OPEN, 200, L).startswith("STOP LOSS")
    assert check_exit(lot(), 103.5, OPEN, 200, L).startswith("TAKE PROFIT")
    assert check_exit(lot(), 100, OPEN, 5, L).startswith("END OF DAY")
    assert check_exit(lot(), 100, OPEN + timedelta(minutes=121), 200, L).startswith("TIME STOP")
    l = lot()
    check_exit(l, 102, OPEN, 200, L)                        # up 2%: trailing armed
    assert check_exit(l, 100.9, OPEN, 200, L).startswith("TRAILING STOP")


# --- Parsing --------------------------------------------------------------------------
def test_parse_alpaca_times_and_snapshot():
    assert parse_time("2026-09-24T13:31:00.123456789Z").minute == 31
    q = Quote.from_snapshot("AAA", {"latestTrade": {"p": 101}, "latestQuote": {"bp": 100.9, "ap": 101.1},
                                    "dailyBar": {"t": "2026-09-24T04:00:00Z", "o": 100, "v": 5, "vw": 100.5},
                                    "prevDailyBar": {"c": 99, "v": 10}})
    assert q.price == 101 and q.day_date == "2026-09-24" and 0.19 < q.spread_pct < 0.21
