"""Each risk rule, on its own, does what it says."""

from paper_trader.broker import Account
from paper_trader.config import RiskLimits
from paper_trader.models import Position
from paper_trader.risk import check_entry, check_exit
from tests.test_broker import snap

L = RiskLimits()
NOW = 1_000_000.0
OK_FEATS = {"age_min": 120}


def entry(acct=None, s=None, feats=OK_FEATS):
    return check_entry(acct or Account("A"), s or snap(), feats, 10.0, 150, NOW, L, 0.08)


def test_healthy_token_is_approved_with_capped_size():
    size, blocked = entry()
    assert blocked == []
    assert 0 < size <= 10.0 * L.max_position_fraction


def test_thin_liquidity_is_blocked():
    _, blocked = entry(s=snap(liq=5_000))
    assert any("liquidity" in b for b in blocked)


def test_brand_new_pair_is_blocked():
    _, blocked = entry(feats={"age_min": 3})
    assert any("sniper" in b for b in blocked)


def test_size_never_exceeds_one_percent_of_pool():
    size, _ = entry(s=snap(liq=30_000))
    assert size * 150 <= 30_000 * L.max_pool_fraction + 1e-9


def test_killed_agent_cannot_buy():
    acct = Account("A"); acct.killed = True
    _, blocked = entry(acct=acct)
    assert any("kill switch" in b for b in blocked)


def test_cooldown_after_selling():
    acct = Account("A"); acct.last_exit["TOKEN"] = NOW - 60
    _, blocked = entry(acct=acct)
    assert any("cooling off" in b for b in blocked)


def pos(entry_price=0.001, liq=100_000, opened=NOW):
    return Position("A", "TOKEN", "TST", tokens=1000, cost_sol=1, entry_price_usd=entry_price,
                    entry_liquidity_usd=liq, opened_at=opened, entry_reason="", peak_price_usd=entry_price)


def test_stop_loss():
    assert check_exit(pos(), snap(price=0.0008), NOW, L).startswith("STOP LOSS")


def test_take_profit():
    assert check_exit(pos(), snap(price=0.0015), NOW, L).startswith("TAKE PROFIT")


def test_trailing_stop_after_a_run():
    p = pos()
    check_exit(p, snap(price=0.00125), NOW, L)            # up 25%: trail armed
    assert check_exit(p, snap(price=0.00108), NOW, L).startswith("TRAILING STOP")


def test_rug_detected_from_liquidity_drop():
    assert check_exit(pos(), snap(price=0.001, liq=40_000), NOW, L).startswith("RUG ALERT")


def test_time_stop():
    assert check_exit(pos(opened=NOW - 3601), snap(), NOW, L).startswith("TIME STOP")


def test_vanished_token_exits_after_grace_period():
    p = pos()
    results = [check_exit(p, None, NOW, L) for _ in range(L.stale_ticks_before_exit)]
    assert results[:-1] == [None] * (L.stale_ticks_before_exit - 1)
    assert results[-1].startswith("RUG / DELISTED")
