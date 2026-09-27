"""Each risk rule, on its own, does what it says."""

from paper_trader.broker import Account
from paper_trader.config import RiskLimits
from paper_trader.models import Position
from paper_trader.risk import check_entry, check_exit
from tests.test_broker import snap

L = RiskLimits()
NOW = 1_000_000.0
OK_FEATS = {"history_min": 300}


def entry(acct=None, s=None, feats=OK_FEATS):
    return check_entry(acct or Account("A"), s or snap(), feats, 10_000.0, NOW, L, 0.15)


def test_healthy_coin_is_approved_with_capped_size():
    size, blocked = entry()
    assert blocked == []
    assert 0 < size <= 10_000 * L.max_position_fraction


def test_thin_volume_is_blocked():
    _, blocked = entry(s=snap(volume=1_000_000))
    assert any("volume" in b for b in blocked)


def test_wide_spread_is_blocked():
    _, blocked = entry(s=snap(spread_pct=1.0))
    assert any("spread" in b for b in blocked)


def test_not_enough_history_is_blocked():
    _, blocked = entry(feats={"history_min": 10})
    assert any("history" in b for b in blocked)


def test_size_never_exceeds_slice_of_daily_volume():
    size, _ = entry(s=snap(volume=6_000_000))
    assert size <= 6_000_000 * L.max_volume_fraction + 1e-9


def test_pending_buys_count_toward_the_position_limit():
    _, blocked = check_entry(Account("A"), snap(), OK_FEATS, 10_000.0, NOW, L, 0.15,
                             pending_buys=L.max_open_positions)
    assert any("max" in b for b in blocked)


def test_killed_agent_cannot_buy():
    acct = Account("A"); acct.killed = True
    _, blocked = entry(acct=acct)
    assert any("kill switch" in b for b in blocked)


def test_cooldown_after_selling():
    acct = Account("A"); acct.last_exit["ETH"] = NOW - 60
    _, blocked = entry(acct=acct)
    assert any("cooling off" in b for b in blocked)


def pos(entry_price=100.0, opened=NOW):
    return Position("A", "ETH", qty=10, cost_usd=1_000, entry_price_usd=entry_price,
                    opened_at=opened, entry_reason="", peak_price_usd=entry_price)


def test_stop_loss():
    assert check_exit(pos(), snap(price=96.5), NOW, L).startswith("STOP LOSS")


def test_take_profit():
    assert check_exit(pos(), snap(price=106.5), NOW, L).startswith("TAKE PROFIT")


def test_trailing_stop_after_a_run():
    p = pos()
    check_exit(p, snap(price=104), NOW, L)            # up 4%: trail armed
    assert check_exit(p, snap(price=102.2), NOW, L).startswith("TRAILING STOP")


def test_time_stop():
    assert check_exit(pos(opened=NOW - L.max_hold_minutes * 60 - 1), snap(), NOW, L).startswith("TIME STOP")


def test_data_gap_holds_instead_of_selling():
    p = pos()
    assert all(check_exit(p, None, NOW, L) is None for _ in range(50))
