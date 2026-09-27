"""The paper broker must charge realistic costs, or paper P&L is fiction."""

import math

from paper_trader import config
from paper_trader.broker import PaperBroker, impact_fraction, quote_buy, quote_sell
from paper_trader.models import AssetSnapshot, Candle, Order


def snap(price=100.0, spread_pct=0.02, volume=50_000_000, symbol="ETH", candles=None):
    half = price * spread_pct / 100 / 2
    return AssetSnapshot(symbol=symbol, product_id=f"{symbol}-USD", name=symbol, url="",
                         price_usd=price, bid=price - half, ask=price + half,
                         volume_24h_usd=volume, chg_24h=0.0, candles=candles or [])


def test_impact_grows_with_size_and_shrinks_with_volume():
    assert impact_fraction(10_000, 1e8) > impact_fraction(1_000, 1e8)
    assert impact_fraction(1_000, 1e9) < impact_fraction(1_000, 1e7)


def test_buy_pays_the_ask_and_sell_gets_the_bid():
    s = snap(spread_pct=0.2)
    assert quote_buy(1_000, s)["fill_price_usd"] > s.ask
    assert quote_sell(1, s)["fill_price_usd"] < s.bid


def test_round_trip_at_unchanged_price_loses_money():
    # Buy then immediately sell at the same price: fees, spread and impact must cost you.
    s = snap()
    qty = quote_buy(1_000, s)["qty"]
    back = quote_sell(qty, s)["usd_out"]
    assert back < 1_000
    # ...but not absurdly: two taker fees plus a small spread and impact.
    assert back > 1_000 * (1 - 2 * config.TAKER_FEE) * 0.998


def test_missing_bid_ask_falls_back_to_an_assumed_spread():
    s = snap()
    s.bid = s.ask = 0
    assert quote_buy(1_000, s)["fill_price_usd"] > s.price_usd
    assert quote_sell(1, s)["fill_price_usd"] < s.price_usd


def test_orders_fill_on_the_next_update_at_the_new_price():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "ETH", usd_amount=1_000, decision_price_usd=100))
    # Price jumped 2% before our order landed: we pay the new price.
    fills = b.fill_pending({"ETH": snap(price=102)}, now=0)
    assert fills[0]["fill_price_usd"] > 102
    assert fills[0]["slippage_pct"] > 2


def test_pending_buy_still_counts_in_equity():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "ETH", usd_amount=1_000))
    assert math.isclose(b.accounts["A"].equity_usd({}), config.STARTING_USD_PER_AGENT)


def test_buy_with_no_price_is_refunded():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "ETH", usd_amount=1_000))
    fills = b.fill_pending({}, now=0)
    assert fills[0]["kind"] == "BUY_FAILED"
    assert math.isclose(b.accounts["A"].cash_usd, config.STARTING_USD_PER_AGENT)


def test_sell_waits_through_a_data_gap_then_fills():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "ETH", usd_amount=1_000))
    b.fill_pending({"ETH": snap()}, now=0)
    b.submit(Order("A", "SELL", "ETH"))
    for _ in range(20):
        assert b.fill_pending({}, now=0) == []   # no price: keep waiting, never write off
    fills = b.fill_pending({"ETH": snap()}, now=0)
    assert fills[0]["kind"] == "SELL" and fills[0]["quote"]["usd_out"] > 990
