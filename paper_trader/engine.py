"""
The engine: one loop that runs the whole desk.

Every tick:
  1. Pull fresh market data from the feed.
  2. Fill orders submitted LAST tick at THIS tick's prices (simulated latency).
  3. For each agent: check exits on open positions, then look for new entries.
  4. Update equity, kill switches, and the state the dashboard reads.

The dashboard never talks to the market; it only reads `engine.state()`.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque

from . import config
from .agents import Agent, default_agents
from .broker import PaperBroker, quote_sell
from .config import RiskLimits
from .models import Order
from .risk import check_entry, check_exit, limits_for
from .signals import add_relative_strength, features
from .store import Store

MAX_BUYS_PER_AGENT_PER_TICK = 2
BLOCKED_LOG_COOLDOWN = 1800   # seconds: don't spam "blocked" for the same coin


class Engine:
    def __init__(self, feed, store: Store, agents: list[Agent] | None = None,
                 limits: RiskLimits = RiskLimits()) -> None:
        self.feed = feed
        self.store = store
        self.agents = agents or default_agents()
        self.limits = limits
        self.broker = PaperBroker([a.name for a in self.agents])
        self.lock = threading.Lock()
        self.paused = False
        self.stopped = False
        self.tick_count = 0
        self.started_wall = time.time()
        self.started_market = None
        self.snaps: dict = {}
        self.valuation: dict = {}   # snaps + last good reading for held coins missing this tick
        self.last_good: dict = {}   # symbol -> most recent complete snapshot
        self.feats: dict = {}
        self.events: deque = deque(maxlen=300)
        self.equity_hist = {a.name: deque(maxlen=720) for a in self.agents}
        self.flow_hist: deque = deque(maxlen=180)
        self.exit_reasons: Counter = Counter()
        self._event_id = 0
        self._blocked_seen: dict = {}
        self._state: dict = {}

    # --- Event log --------------------------------------------------------------
    def _log(self, kind: str, agent: Agent | None, snap, headline: str,
             explain: list[str], **extra) -> None:
        self._event_id += 1
        e = {"id": self._event_id, "t": self.feed.now(), "kind": kind,
             "agent": agent.name if agent else None, "color": agent.color if agent else "#ffffff",
             "symbol": snap.symbol if snap else extra.pop("symbol", None),
             "url": snap.url if snap else "", "headline": headline, "explain": explain, **extra}
        self.events.appendleft(e)
        self.store.event(e)

    # --- One tick ---------------------------------------------------------------
    def tick(self) -> None:
        held = {sym for acct in self.broker.accounts.values() for sym in acct.positions}
        held |= {o.symbol for o in self.broker.pending}
        raw = self.feed.poll(keep=held)
        # Only trust readings with a real price; a missing field is a gap in
        # the data, not a price of zero.
        snaps = {s: v for s, v in raw.items() if v.price_usd > 0}
        self.last_good.update(snaps)
        # To VALUE what we hold, fall back to the last good reading during a
        # gap, so a hiccup doesn't show as a 100% loss or trip the kill switch.
        valuation = dict(snaps)
        for sym in held:
            if sym not in valuation and sym in self.last_good:
                valuation[sym] = self.last_good[sym]
        now = self.feed.now()
        if self.started_market is None:
            self.started_market = now
        if not snaps and not self.snaps:
            # No prices yet (feed down or still starting): can't value or
            # trade anything. Still publish state so the dashboard shows why.
            with self.lock:
                self._state = self._build_state(now)
            return

        for fill in self.broker.fill_pending(snaps, now):
            self._report_fill(fill)

        feats = {s: features(v, now) for s, v in snaps.items()}
        add_relative_strength(feats, config.BENCHMARK)
        ranked = sorted(snaps, key=lambda s: feats[s]["score"], reverse=True)

        for agent in self.agents:
            acct = self.broker.accounts[agent.name]
            lim = limits_for(self.limits, agent.exits)
            self._exits(agent, acct, snaps, now, lim)
            equity = acct.equity_usd(valuation)
            acct.peak_equity_usd = max(acct.peak_equity_usd, equity)
            if not acct.killed and equity < acct.peak_equity_usd * (1 - lim.kill_switch_drawdown):
                self._kill(agent, f"equity fell {1 - equity / acct.peak_equity_usd:.0%} from its peak")
            if not self.paused:
                self._entries(agent, acct, snaps, feats, ranked, equity, now, lim)
            self.equity_hist[agent.name].append((now, equity))
            self.store.equity(now, agent.name, equity)

        # Market-wide up vs down volume (USD, last 5 min), for the flow chart.
        self.flow_hist.append((now, sum(f["up_vol_usd"] for f in feats.values()),
                               sum(f["down_vol_usd"] for f in feats.values())))

        self.store.commit()
        self.snaps, self.feats, self.valuation = snaps, feats, valuation
        self.tick_count += 1
        with self.lock:
            self._state = self._build_state(now)

    def _exits(self, agent, acct, snaps, now, lim) -> None:
        for pos in list(acct.positions.values()):
            if self.broker.has_pending(agent.name, pos.symbol):
                continue
            reason = check_exit(pos, snaps.get(pos.symbol), now, lim)
            if reason:
                snap = snaps.get(pos.symbol)
                self.broker.submit(Order(agent.name, "SELL", pos.symbol, reason=reason,
                                         submitted_at=now,
                                         decision_price_usd=snap.price_usd if snap else 0))

    def _entries(self, agent, acct, snaps, feats, ranked, equity, now, lim) -> None:
        buys = 0
        for sym in ranked:
            if buys >= MAX_BUYS_PER_AGENT_PER_TICK:
                break
            if self.broker.has_pending(agent.name, sym):
                continue
            fire, checks = agent.evaluate(feats[sym])
            if not fire:
                continue
            snap = snaps[sym]
            pending = sum(1 for o in self.broker.pending if o.agent == agent.name and o.side == "BUY")
            size, blocked = check_entry(acct, snap, feats[sym], equity, now, lim, agent.size_fraction,
                                        pending_buys=pending)
            if blocked:
                key = (agent.name, sym)
                if now - self._blocked_seen.get(key, 0) > BLOCKED_LOG_COOLDOWN and "already holding it" not in blocked:
                    self._blocked_seen[key] = now
                    self._log("BLOCKED", agent, snap, f"{agent.name} wanted {sym}: risk said no",
                              [f"Signal fired: {agent.thesis}"] + [f"Blocked: {b}" for b in blocked],
                              checks=[c.as_dict() for c in checks])
                continue
            reason = "; ".join(f"{c.label} {c.value:,.2f} ({c.need})" for c in checks)
            self.broker.submit(Order(agent.name, "BUY", sym, usd_amount=size,
                                     reason=reason, submitted_at=now,
                                     decision_price_usd=snap.price_usd,
                                     details={"checks": [c.as_dict() for c in checks]}))
            buys += 1

    def _kill(self, agent: Agent, why: str) -> None:
        acct = self.broker.accounts[agent.name]
        acct.killed = True
        now = self.feed.now()
        for pos in list(acct.positions.values()):
            if not self.broker.has_pending(agent.name, pos.symbol):
                self.broker.submit(Order(agent.name, "SELL", pos.symbol,
                                         reason="KILL SWITCH: closing everything", submitted_at=now))
        self._log("KILL", agent, None, f"{agent.name} KILL SWITCH", [
            f"Why: {why}.",
            "All open positions are being sold and this agent will not open new ones.",
            "Restart the desk to reset it, after you've understood what went wrong."])

    def _report_fill(self, fill: dict) -> None:
        o = fill["order"]
        agent = next(a for a in self.agents if a.name == o.agent)
        snap = fill.get("snap")
        if fill["kind"] == "BUY_FAILED":
            self._log("INFO", agent, None, f"{agent.name} buy of {o.symbol} failed",
                      [fill["why"], "The reserved cash was returned."], symbol=o.symbol)
        elif fill["kind"] == "BUY":
            q = fill["quote"]
            self._log("BUY", agent, snap, f"BUY {o.symbol} for ${o.usd_amount:,.2f}", [
                f"Why: {agent.thesis}",
                f"Filled one update after the signal, at ${fill['fill_price_usd']:,.6g} "
                f"({fill['slippage_pct']:+.2f}% vs the price that triggered it).",
                f"Paid the ask (spread {q['spread_pct']:.3f}%), price impact {q['impact_pct']:.3f}%, "
                f"taker fee ${q['fee_usd']:.2f}.",
                "Exits: stop loss, take profit, trailing stop and time stop "
                "are now watching it every tick.",
            ], checks=o.details.get("checks", []), usd=o.usd_amount,
               fill_price=fill["fill_price_usd"], impact_pct=q["impact_pct"],
               slippage_pct=fill["slippage_pct"])
        elif fill["kind"] == "SELL":
            pos, q = fill["position"], fill["quote"]
            self.exit_reasons[o.reason.split(":")[0]] += 1
            result = "PROFIT" if fill["pnl_usd"] > 0 else "LOSS"
            self._log("SELL", agent, snap, f"SELL {o.symbol}: {fill['pnl_pct']:+.2f}% ({result})", [
                f"Exit rule: {o.reason}.",
                f"Held {fill['held_minutes']:.0f} min. Paid ${pos.cost_usd:,.2f}, "
                f"got back ${q['usd_out']:,.2f} after the spread, fees and {q['impact_pct']:.3f}% price impact.",
                f"Bought because: {pos.entry_reason}.",
            ], symbol=o.symbol, pnl_usd=fill["pnl_usd"], pnl_pct=fill["pnl_pct"],
               reason=o.reason.split(":")[0])

    # --- Dashboard state ----------------------------------------------------------
    def _build_state(self, now: float) -> dict:
        agents, positions = [], []
        for agent in self.agents:
            acct = self.broker.accounts[agent.name]
            eq = self.equity_hist[agent.name][-1][1] if self.equity_hist[agent.name] else acct.start_usd
            closed = acct.wins + acct.losses
            agents.append({
                "name": agent.name, "color": agent.color, "thesis": agent.thesis,
                "equity_usd": eq, "cash_usd": acct.cash_usd,
                "pnl_usd": eq - acct.start_usd, "pnl_pct": (eq / acct.start_usd - 1) * 100,
                "realized_usd": acct.realized_pnl_usd, "fees_usd": acct.fees_usd,
                "open": len(acct.positions), "trades": closed,
                "win_rate": acct.wins / closed * 100 if closed else None,
                "killed": acct.killed,
                "equity_hist": [round(v, 2) for _, v in list(self.equity_hist[agent.name])[::2]],
            })
            for p in acct.positions.values():
                snap = self.valuation.get(p.symbol)
                value = quote_sell(p.qty, snap)["usd_out"]
                positions.append({
                    "agent": agent.name, "color": agent.color, "symbol": p.symbol,
                    "url": snap.url if snap else "", "qty": p.qty,
                    "cost_usd": p.cost_usd, "value_usd": value,
                    "pnl_pct": (value / p.cost_usd - 1) * 100 if p.cost_usd else 0,
                    "minutes": (now - p.opened_at) / 60,
                    "entry_price": p.entry_price_usd,
                    "price": snap.price_usd if snap else None,
                    "stale": p.symbol not in self.snaps,   # valued at last good reading
                })

        held_by: dict = {}
        for p in positions:
            held_by.setdefault(p["symbol"], []).append(p["color"])
        assets = []
        for sym, snap in sorted(self.snaps.items(), key=lambda kv: -self.feats[kv[0]]["score"]):
            f = self.feats[sym]
            assets.append({
                "symbol": sym, "score": f["score"], "price": snap.price_usd,
                "chg_m5": f["chg_m5"], "chg_h1": f["chg_h1"], "chg_h24": f["chg_h24"],
                "rsi": round(f["rsi"], 1), "trend_pct": round(f["trend_pct"], 3),
                "zscore": round(f["zscore"], 2), "rs_h1": round(f["rs_h1"], 2),
                "up_down": round(f["up_down_ratio"], 2), "vol_accel": round(f["vol_accel"], 2),
                "spread_pct": round(f["spread_pct"], 4), "volume_24h": snap.volume_24h_usd,
                "held": held_by.get(sym, []), "url": snap.url,
            })

        total_start = sum(a["equity_usd"] - a["pnl_usd"] for a in agents)
        total_eq = sum(a["equity_usd"] for a in agents)
        bench = self.snaps.get(config.BENCHMARK)
        return {
            "feed": self.feed.name, "live": self.feed.is_live, "status": self.feed.status,
            "paused": self.paused, "tick": self.tick_count,
            "benchmark": config.BENCHMARK, "benchmark_usd": bench.price_usd if bench else None,
            "market_time": now, "runtime_min": (now - (self.started_market or now)) / 60,
            "uptime_s": time.time() - self.started_wall,
            "total": {"equity_usd": total_eq, "start_usd": total_start,
                      "pnl_pct": (total_eq / total_start - 1) * 100 if total_start else 0,
                      "fees_usd": sum(a["fees_usd"] for a in agents),
                      "open": len(positions), "pending": len(self.broker.pending)},
            "agents": agents, "positions": positions, "assets": assets,
            "events": list(self.events)[:150],
            "flow": [(round(u), round(d)) for _, u, d in self.flow_hist],
            "exit_reasons": self.exit_reasons.most_common(),
            "costs": {"taker_fee": config.TAKER_FEE, "impact_coef": config.IMPACT_COEF},
        }

    def state(self) -> dict:
        with self.lock:
            return self._state

    # --- Controls -----------------------------------------------------------------
    def control(self, action: str) -> str:
        if action == "pause":
            self.paused = True
            return "paused: no new entries (exits still run)"
        if action == "resume":
            self.paused = False
            return "resumed"
        if action == "kill_all":
            for agent in self.agents:
                if not self.broker.accounts[agent.name].killed:
                    self._kill(agent, "you pressed KILL ALL")
            return "all agents killed"
        return "unknown action"

    def run_forever(self) -> None:
        while not self.stopped:
            started = time.time()
            try:
                self.tick()
            except Exception as e:  # keep the desk alive; show the error on the dashboard
                self.feed.status = f"engine error: {e!r}"
            time.sleep(max(0.0, self.feed.tick_seconds - (time.time() - started)))
