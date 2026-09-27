"""
The trading agents ("sub-agents"): same objective, different playbooks.

Every agent looks at the same market each tick and decides, by its own
explicit rules, whether a coin is worth buying. Each has its own fake
account, so the dashboard shows which playbook is actually working after
costs, and which is just noise.

The four playbooks are the classic families of systematic trading:
trend following, breakouts, mean reversion, and relative strength.

An agent only PROPOSES a buy. The risk engine (risk.py) decides whether it's
allowed and how big it can be, and handles every exit. Rules here are plain
checklists so you can see exactly why each trade happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Check:
    label: str        # "1h price change"
    value: float
    need: str         # ">= 0.5%"
    passed: bool

    def as_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "need": self.need, "passed": self.passed}


@dataclass
class Agent:
    name: str
    color: str
    thesis: str                       # one-line plain-English idea
    size_fraction: float = 0.15       # share of equity per trade (risk may cut it)
    exits: dict = field(default_factory=dict)  # overrides for risk exit limits

    def checks(self, f: dict) -> list[Check]:
        raise NotImplementedError

    def evaluate(self, f: dict) -> tuple[bool, list[Check]]:
        checks = self.checks(f)
        return all(c.passed for c in checks), checks


def ge(label, value, need, unit=""):
    return Check(label, value, f">= {need}{unit}", value >= need)


def le(label, value, need, unit=""):
    return Check(label, value, f"<= {need}{unit}", value <= need)


def between(label, value, lo, hi, unit=""):
    return Check(label, value, f"{lo}{unit} to {hi}{unit}", lo <= value <= hi)


class TrendRider(Agent):
    def __init__(self):
        super().__init__("TREND RIDER", "#3987e5",
                         "Buys coins in a clean uptrend: fast average above slow, momentum not yet stretched.")

    def checks(self, f):
        return [ge("EMA20 vs EMA60", f["trend_pct"], 0.15, "%"),
                ge("Price vs EMA20", f["px_vs_ema20"], 0, "%"),
                ge("1h price change", f["chg_h1"], 0.5, "%"),
                between("RSI (5m bars)", f["rsi"], 52, 70)]


class Breakout(Agent):
    def __init__(self):
        super().__init__("BREAKOUT", "#d95926",
                         "Buys a fresh 1-hour high when volume confirms it, before the move is chased.")

    def checks(self, f):
        return [between("Above prior 1h high", f["breakout_pct"], 0.05, 1.0, "%"),
                ge("Volume acceleration", f["vol_accel"], 1.5, "x"),
                ge("5m up/down volume", f["up_down_ratio"], 1.3, "x")]


class MeanRevert(Agent):
    def __init__(self):
        super().__init__("MEAN REVERT", "#199e70",
                         "Buys sharp, oversold dips below the lower Bollinger band, expecting a snap back.",
                         exits={"take_profit": 0.03, "stop_loss": 0.025, "max_hold_minutes": 4 * 60})

    def checks(self, f):
        return [le("Z-score vs 1h mean", f["zscore"], -2.0),
                le("RSI (5m bars)", f["rsi"], 35),
                ge("24h price change", f["chg_h24"], -8, "%")]   # a dip, not a crash


class RelativeStrength(Agent):
    def __init__(self):
        super().__init__("REL. STRENGTH", "#c98500",
                         "Rotates into coins beating BTC over the hour, while their own trend is up.",
                         size_fraction=0.12)

    def checks(self, f):
        return [ge("1h vs BTC", f["rs_h1"], 1.0, " pts"),
                ge("EMA20 vs EMA60", f["trend_pct"], 0, "%"),
                ge("5m price change", f["chg_m5"], 0, "%")]


def default_agents() -> list[Agent]:
    return [TrendRider(), Breakout(), MeanRevert(), RelativeStrength()]
