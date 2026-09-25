"""Live-feed parsing (without the network) and a full engine run on the demo market."""

import json

from paper_trader.demo_feed import DemoFeed
from paper_trader.engine import Engine
from paper_trader.feeds import SOL_MINT, DexScreenerFeed
from paper_trader.models import TokenSnapshot
from paper_trader.store import Store

# Shaped like a DexScreener pair: note prices arrive as strings.
PAIR = {
    "chainId": "solana", "dexId": "raydium", "url": "https://dexscreener.com/solana/pair1",
    "pairAddress": "pair1", "baseToken": {"address": "MEME", "name": "Meme", "symbol": "MEME"},
    "quoteToken": {"address": SOL_MINT, "symbol": "SOL"},
    "priceNative": "0.00001", "priceUsd": "0.0015",
    "txns": {"m5": {"buys": 40, "sells": 20}, "h1": {"buys": 300, "sells": 250}},
    "volume": {"m5": 12000, "h1": 90000, "h24": 1e6},
    "priceChange": {"m5": 5.5, "h1": 12, "h24": -3},
    "liquidity": {"usd": 80000, "base": 1, "quote": 1},
    "marketCap": 1500000, "pairCreatedAt": 1_700_000_000_000,
}


def test_parse_dexscreener_pair():
    s = TokenSnapshot.from_dexscreener(PAIR)
    assert s.price_usd == 0.0015 and s.buys_m5 == 40 and s.liquidity_usd == 80000
    assert s.chg_m5 == 5.5 and s.symbol == "MEME"


def test_parse_tolerates_missing_fields():
    s = TokenSnapshot.from_dexscreener({"baseToken": {"address": "X"}})
    assert s.price_usd == 0 and s.buys_m5 == 0


def test_live_feed_poll_with_fake_http():
    feed = DexScreenerFeed()
    shallow = dict(PAIR, pairAddress="pair2", liquidity={"usd": 1000})
    sol = dict(PAIR, baseToken={"address": SOL_MINT, "symbol": "SOL"}, priceUsd="150.5",
               quoteToken={"address": "USDC", "symbol": "USDC"})

    def fake_get(path):
        if path.startswith("/token-"):
            return [{"chainId": "solana", "tokenAddress": "MEME"}, {"chainId": "base", "tokenAddress": "NOPE"}]
        return {"pairs": [shallow, PAIR, sol]}

    feed._get = fake_get
    snaps = feed.poll()
    assert feed.sol_usd == 150.5
    assert list(snaps) == ["MEME"]                     # other chains ignored
    assert snaps["MEME"].pair_address == "pair1"       # deepest pool chosen


def test_engine_runs_and_accounting_holds():
    engine = Engine(DemoFeed(seed=5), Store(None))
    for _ in range(400):
        engine.tick()
    state = engine.state()
    json.dumps(state, default=str)                     # the dashboard can serialise it
    for acct in engine.broker.accounts.values():
        assert acct.cash_sol >= -1e-9                   # never spends money it doesn't have
        assert len(acct.positions) <= engine.limits.max_open_positions
    kinds = {e["kind"] for e in engine.events}
    assert "BUY" in kinds and "SELL" in kinds


class GappyFeed:
    """Replays one token, then drops it (or blanks its liquidity) for a tick."""
    name, is_live, tick_seconds, status, sol_usd = "test", True, 0, "ok", 150.0

    def __init__(self, frames):
        self.frames, self.t = frames, 1_700_000_000.0

    def now(self):
        return self.t

    def poll(self, keep=frozenset()):
        self.t += 10
        return self.frames.pop(0) if self.frames else {}


def test_data_gap_is_not_a_100_percent_loss():
    from paper_trader.models import Position
    good = TokenSnapshot.from_dexscreener(PAIR)
    blank = TokenSnapshot.from_dexscreener(dict(PAIR, liquidity={}))   # liquidity omitted
    engine = Engine(GappyFeed([{"MEME": good}, {"MEME": blank}, {}]), Store(None))
    acct = engine.broker.accounts["MOMENTUM"]
    acct.cash_sol -= 1.0
    acct.positions["MEME"] = Position("MOMENTUM", "MEME", "MEME", tokens=1.0 * 150 / 0.0015,
                                      cost_sol=1.0, entry_price_usd=0.0015, entry_liquidity_usd=80000,
                                      opened_at=engine.feed.t, entry_reason="test", peak_price_usd=0.0015)
    for _ in range(3):
        engine.tick()
        mo = next(a for a in engine.state()["agents"] if a["name"] == "MOMENTUM")
        assert mo["pnl_pct"] > -3, "a gap in the data must not show as a big loss"
        assert not mo["killed"]


def test_sol_and_busy_tokens_dont_crowd_others_out_of_the_reply():
    # Mimic the real API returning at most 30 pairs per reply, with SOL
    # trading in 50 pools. Every tracked token must still come back.
    feed = DexScreenerFeed()
    tokens = [f"T{i}" for i in range(25)]
    feed.discover = lambda keep: None
    feed.tracked = tokens
    sol_pairs = [dict(PAIR, pairAddress=f"sol{i}", priceUsd="150.5",
                      baseToken={"address": SOL_MINT, "symbol": "SOL"},
                      quoteToken={"symbol": "USDC"}) for i in range(50)]

    def capped_get(path):
        asked = path.rsplit("/", 1)[-1].split(",")
        pairs = []
        for a in asked:
            pairs += sol_pairs if a == SOL_MINT else [
                dict(PAIR, pairAddress=f"{a}-{k}", baseToken={"address": a, "symbol": a}) for k in range(2)]
        return {"pairs": pairs[:30]}

    feed._get = capped_get
    snaps = feed.poll()
    assert feed.sol_usd == 150.5
    assert sorted(snaps) == sorted(tokens)
