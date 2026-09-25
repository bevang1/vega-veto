"""
The four stock agents: same objective, different intraday playbooks.

Each is a short checklist. A stock is a candidate only when every item
passes; the risk engine then decides whether (and how much) to buy. Each
agent has its own $25k slice of the paper account, so the reports show
which playbook actually works.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from paper_trader.agents import Check, between, ge


def above(label, value, threshold, unit="%"):
    return Check(label, value, f"> {threshold}{unit}", value > threshold)


@dataclass
class StockAgent:
    name: str
    code: str        # short tag used in order IDs and reports
    thesis: str
    exits: dict = field(default_factory=dict)

    def checks(self, f: dict) -> list[Check]:
        raise NotImplementedError

    def evaluate(self, f: dict) -> tuple[bool, list[Check]]:
        c = self.checks(f)
        return all(x.passed for x in c), c


class OpeningRangeBreakout(StockAgent):
    def __init__(self):
        super().__init__("OPENING RANGE", "OR",
                         "Buys a break above the first 15 minutes' high, on strong volume.")

    def checks(self, f):
        orh = f["or_high"]
        breakout = (f["price"] / orh - 1) * 100 if orh else -99
        return [ge("Minutes since open", f["elapsed_min"], 15),
                between("Above opening-range high", breakout, 0.1, 1.0, "%"),
                ge("Volume pace vs yesterday", f["rel_volume"], 1.2, "x"),
                above("Distance above VWAP", f["vwap_dist"], 0)]


class Momentum(StockAgent):
    def __init__(self):
        super().__init__("MOMENTUM", "MO",
                         "Rides stocks already up on the day that are still climbing.")

    def checks(self, f):
        return [ge("Up since open", f["intraday_chg"], 1.5, "%"),
                ge("Last 15 min", f["mom_15m"], 0.4, "%"),
                above("Distance above VWAP", f["vwap_dist"], 0),
                ge("Volume pace vs yesterday", f["rel_volume"], 1.3, "x")]


class VwapReversion(StockAgent):
    def __init__(self):
        super().__init__("VWAP REVERSION", "VR",
                         "Buys big-cap sell-offs that stretch below VWAP and start to turn up.",
                         exits={"take_profit": 0.015, "max_hold_minutes": 60})

    def checks(self, f):
        return [Check("Down vs yesterday", f["day_chg"], "<= -2%", f["day_chg"] <= -2),
                Check("Below VWAP", f["vwap_dist"], "<= -0.8%", f["vwap_dist"] <= -0.8),
                ge("Last 5 min (turning up)", f["mom_5m"], 0.1, "%")]


class GapAndGo(StockAgent):
    def __init__(self):
        super().__init__("GAP & GO", "GG",
                         "Buys stocks that gapped up at the open and are holding the gain.")

    def checks(self, f):
        return [ge("Opening gap", f["gap"], 2, "%"),
                ge("Up since open", f["intraday_chg"], 0, "%"),
                above("Distance above VWAP", f["vwap_dist"], 0),
                between("Minutes since open", f["elapsed_min"], 15, 120, " min")]


def default_agents() -> list[StockAgent]:
    return [OpeningRangeBreakout(), Momentum(), VwapReversion(), GapAndGo()]
