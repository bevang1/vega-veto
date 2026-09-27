"""Live-feed parsing (without the network), indicators, and a full engine run on the demo market."""

import json

from paper_trader import config
from paper_trader.demo_feed import DemoFeed
from paper_trader.engine import Engine
from paper_trader.feeds import CoinbaseFeed
from paper_trader.models import AssetSnapshot, Candle, Position
from paper_trader.signals import add_relative_strength, features, rsi
from paper_trader.store import Store
from tests.test_broker import snap

# Shaped like Coinbase's replies: note numbers arrive as strings, and
# candles come newest first as [time, low, high, open, close, volume].
TICKER = {"price": "65000.5", "bid": "65000.0", "ask": "65001.0", "volume": "12000.5", "size": "0.01"}
STATS = {"open": "64000", "high": "66000", "low": "63500", "last": "65000.5", "volume": "12000"}
CANDLES = [[1_700_000_000 + 60 * i, 64900, 65100, 65000, 65000 + i, 5.0] for i in range(120)][::-1]


def test_parse_coinbase_ticker_and_candles():
    candles = sorted((Candle.from_coinbase(r) for r in CANDLES), key=lambda c: c.t)
    s = AssetSnapshot.from_coinbase("BTC-USD", TICKER, STATS, candles)
    assert s.symbol == "BTC" and s.price_usd == 65000.5 and s.bid == 65000.0
    assert round(s.chg_24h, 3) == round((65000.5 / 64000 - 1) * 100, 3)
    assert s.volume_24h_usd == 12000.5 * 65000.5
    assert s.candles[0].t < s.candles[-1].t and s.candles[-1].close == 65119


def test_parse_tolerates_missing_fields():
    s = AssetSnapshot.from_coinbase("ETH-USD", {}, None, [])
    assert s.price_usd == 0 and s.chg_24h == 0 and s.spread_pct == 0


def test_live_feed_poll_with_fake_http():
    feed = CoinbaseFeed(["BTC", "ETH"])
    calls = []

    def fake_get(path):
        calls.append(path)
        if path.startswith("/products/ETH-USD/ticker"):
            return None                               # a failed request: ETH just goes missing
        if path.endswith("/ticker"):
            return TICKER
        if path.endswith("/stats"):
            return STATS
        return CANDLES

    feed._get = fake_get
    snaps = feed.poll()
    assert list(snaps) == ["BTC"]
    assert len(snaps["BTC"].candles) == 120
    assert any("granularity=60" in c for c in calls)


def test_indicators_on_a_steady_uptrend():
    candles = [Candle(60 * i, 100 + i * 0.1, 100 + i * 0.1 + 0.05, 100 + i * 0.1 - 0.05,
                      100 + i * 0.1 + 0.08, 10) for i in range(200)]
    s = snap(price=candles[-1].close, candles=candles)
    f = features(s, now=0)
    assert f["trend_pct"] > 0 and f["chg_h1"] > 0
    assert f["rsi"] > 70                                  # only up-moves: overbought
    assert f["history_min"] == 200


def test_rsi_bounds():
    assert rsi([1, 2, 3, 4, 5] * 10) >= 0
    assert rsi(list(range(100, 0, -1))) < 5


def test_relative_strength_vs_btc():
    feats = {"BTC": {"chg_h1": 1.0}, "SOL": {"chg_h1": 3.0}}
    add_relative_strength(feats, "BTC")
    assert feats["SOL"]["rs_h1"] == 2.0 and feats["BTC"]["rs_h1"] == 0.0


def test_engine_runs_and_accounting_holds():
    engine = Engine(DemoFeed(seed=5), Store(None))
    for _ in range(600):
        engine.tick()
    state = engine.state()
    json.dumps(state, default=str)                     # the dashboard can serialise it
    assert {a["symbol"] for a in state["assets"]} == set(config.UNIVERSE)
    for acct in engine.broker.accounts.values():
        assert acct.cash_usd >= -1e-6                   # never spends money it doesn't have
        assert len(acct.positions) <= engine.limits.max_open_positions
    kinds = {e["kind"] for e in engine.events}
    assert "BUY" in kinds and "SELL" in kinds


class GappyFeed:
    """Replays one coin, then drops it for a couple of ticks."""
    name, is_live, tick_seconds, status = "test", True, 0, "ok"

    def __init__(self, frames):
        self.frames, self.t = frames, 1_700_000_000.0

    def now(self):
        return self.t

    def poll(self, keep=frozenset()):
        self.t += 10
        return self.frames.pop(0) if self.frames else {}


def test_data_gap_is_not_a_100_percent_loss():
    good = snap(symbol="ETH")
    engine = Engine(GappyFeed([{"ETH": good}, {}, {}]), Store(None))
    name = engine.agents[0].name
    acct = engine.broker.accounts[name]
    acct.cash_usd -= 10
    acct.positions["ETH"] = Position(name, "ETH", qty=0.1, cost_usd=10, entry_price_usd=100,
                                     opened_at=engine.feed.t, entry_reason="test", peak_price_usd=100)
    for _ in range(3):
        engine.tick()
        a = next(x for x in engine.state()["agents"] if x["name"] == name)
        assert a["pnl_pct"] > -1, "a gap in the data must not show as a big loss"
        assert not a["killed"]
    assert "ETH" in acct.positions                      # and it wasn't panic-sold
