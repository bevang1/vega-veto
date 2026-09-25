"""
The trading agents ("sub-agents"): same objective, different playbooks.

Every agent looks at the same market each tick and decides, by its own
explicit rules, whether a token is worth buying. Each has its own fake
wallet, so the dashboard shows which playbook is actually working after
costs, and which is just noise.

An agent only PROPOSES a buy. The risk engine (risk.py) decides whether it's
allowed and how big it can be, and handles every exit. Rules here are plain
checklists so you can see exactly why each trade happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Check:
    label: str        # "5m price change"
    value: float
    need: str         # ">= 4%"
    passed: bool

    def as_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "need": self.need, "passed": self.passed}


@dataclass
class Agent:
    name: str
    color: str
    thesis: str                       # one-line plain-English idea
    size_fraction: float = 0.08       # share of equity per trade (risk may cut it)
    exits: dict = field(default_factory=dict)  # overrides for risk exit limits

    def checks(self, f: dict) -> list[Check]:
        raise NotImplementedError

    def evaluate(self, f: dict) -> tuple[bool, list[Check]]:
        checks = self.checks(f)
        return all(c.passed for c in checks), checks


def ge(label, value, need, unit=""):
    return Check(label, value, f">= {need}{unit}", value >= need)


def between(label, value, lo, hi, unit=""):
    return Check(label, value, f"{lo}{unit} to {hi}{unit}", lo <= value <= hi)


class Momentum(Agent):
    def __init__(self):
        super().__init__("MOMENTUM", "#3987e5",
                         "Rides tokens already running, while buyers are still in control.")

    def checks(self, f):
        return [ge("5m price change", f["chg_m5"], 4, "%"),
                ge("1h price change", f["chg_h1"], 10, "%"),
                ge("5m buy/sell ratio", f["buy_ratio_m5"], 1.3, "x"),
                ge("5m transactions", f["txns_m5"], 30)]


class VolumeSurge(Agent):
    def __init__(self):
        super().__init__("VOLUME SURGE", "#d95926",
                         "Catches sudden bursts of trading before price has fully moved.")

    def checks(self, f):
        return [ge("Volume acceleration", f["vol_accel"], 2.5, "x"),
                ge("5m buy/sell ratio", f["buy_ratio_m5"], 1.2, "x"),
                between("5m price change", f["chg_m5"], 0, 15, "%")]


class DipBuyer(Agent):
    def __init__(self):
        super().__init__("DIP BUYER", "#199e70",
                         "Buys short pullbacks in tokens that are strong over the hour.",
                         exits={"take_profit": 0.25, "max_hold_minutes": 40})

    def checks(self, f):
        return [ge("1h price change", f["chg_h1"], 25, "%"),
                between("5m price change", f["chg_m5"], -15, -4, "%"),
                ge("1h buy/sell ratio", f["buy_ratio_h1"], 1.1, "x")]


class FreshLaunch(Agent):
    def __init__(self):
        super().__init__("FRESH LAUNCH", "#c98500",
                         "Takes small early bets on young pairs with heavy, broad buying.",
                         size_fraction=0.05,
                         exits={"stop_loss": 0.10, "max_hold_minutes": 30})

    def checks(self, f):
        age = f["age_min"] if f["age_min"] is not None else -1
        return [between("Pair age", age, 20, 180, " min"),
                ge("5m buy/sell ratio", f["buy_ratio_m5"], 1.5, "x"),
                ge("5m transactions", f["txns_m5"], 50),
                ge("Liquidity", f["liquidity"], 40_000, " USD")]


def default_agents() -> list[Agent]:
    return [Momentum(), VolumeSurge(), DipBuyer(), FreshLaunch()]
