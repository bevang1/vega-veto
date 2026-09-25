"""The paper broker must charge realistic costs, or paper P&L is fiction."""

import math

from paper_trader import config
from paper_trader.broker import PaperBroker, amm_out_usd, quote_buy, quote_sell
from paper_trader.models import Order, TokenSnapshot


def snap(price=0.001, liq=100_000, address="TOKEN"):
    return TokenSnapshot(address=address, symbol="TST", name="", pair_address="", dex="", url="",
                         price_usd=price, liquidity_usd=liq, market_cap=0, created_at_ms=0,
                         vol_m5=0, vol_h1=0, vol_h24=0, buys_m5=0, sells_m5=0, buys_h1=0,
                         sells_h1=0, chg_m5=0, chg_h1=0, chg_h24=0)


def test_small_trade_has_almost_no_impact():
    assert math.isclose(amm_out_usd(1, 1_000_000), 1, rel_tol=1e-5)


def test_trade_the_size_of_the_pool_side_gets_half():
    # $50k into a $100k pool (so $50k per side): x*y=k gives half the value.
    assert math.isclose(amm_out_usd(50_000, 100_000), 25_000)


def test_bigger_trades_pay_more_impact():
    s = snap()
    assert quote_buy(10, s, 150)["impact_pct"] > quote_buy(1, s, 150)["impact_pct"]


def test_round_trip_at_unchanged_price_loses_money():
    # Buy then immediately sell at the same price: fees + impact must cost you.
    s = snap()
    tokens = quote_buy(1.0, s, 150)["tokens"]
    back = quote_sell(tokens, s, 150)["sol_out"]
    assert back < 1.0
    assert back > 0.98  # but not absurdly: ~0.5% fees + small impact + network fee


def test_selling_into_a_rugged_pool_returns_almost_nothing():
    tokens = quote_buy(1.0, snap(), 150)["tokens"]
    rugged = snap(price=0.00001, liq=200)  # liquidity pulled, price collapses 99%
    assert quote_sell(tokens, rugged, 150)["sol_out"] < 0.01


def test_orders_fill_on_the_next_update_at_the_new_price():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "TOKEN", "TST", sol_amount=1.0, decision_price_usd=0.001))
    # Price jumped 10% before our order landed: we pay the new price.
    fills = b.fill_pending({"TOKEN": snap(price=0.0011)}, sol_usd=150, now=0)
    assert fills[0]["fill_price_usd"] > 0.0011
    assert fills[0]["slippage_pct"] > 10


def test_pending_buy_still_counts_in_equity():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "TOKEN", "TST", sol_amount=1.0))
    acct = b.accounts["A"]
    assert math.isclose(acct.equity_sol({}, 150), config.STARTING_SOL_PER_AGENT)


def test_buy_of_vanished_token_is_refunded():
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "TOKEN", "TST", sol_amount=1.0))
    fills = b.fill_pending({}, sol_usd=150, now=0)
    assert fills[0]["kind"] == "BUY_FAILED"
    assert math.isclose(b.accounts["A"].cash_sol, config.STARTING_SOL_PER_AGENT)


def test_sell_waits_through_a_data_gap_then_fills():
    from paper_trader.broker import SELL_WAIT_TICKS
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "TOKEN", "TST", sol_amount=1.0))
    b.fill_pending({"TOKEN": snap()}, sol_usd=150, now=0)
    b.submit(Order("A", "SELL", "TOKEN", "TST"))
    for _ in range(SELL_WAIT_TICKS - 1):
        assert b.fill_pending({}, sol_usd=150, now=0) == []   # token missing: keep waiting
    fills = b.fill_pending({"TOKEN": snap()}, sol_usd=150, now=0)  # data is back
    assert fills[0]["kind"] == "SELL" and fills[0]["quote"]["sol_out"] > 0.95


def test_sell_of_token_gone_for_good_is_written_off():
    from paper_trader.broker import SELL_WAIT_TICKS
    b = PaperBroker(["A"])
    b.submit(Order("A", "BUY", "TOKEN", "TST", sol_amount=1.0))
    b.fill_pending({"TOKEN": snap()}, sol_usd=150, now=0)
    b.submit(Order("A", "SELL", "TOKEN", "TST"))
    fills = []
    for _ in range(SELL_WAIT_TICKS + 1):
        fills += b.fill_pending({}, sol_usd=150, now=0)
    assert fills[0]["pnl_sol"] < -0.99
