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
    sol = dict(PAIR, baseToken={"address": SOL_MINT, "symbol": "SOL"}, priceUsd="150.5")

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
