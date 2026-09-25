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
from .signals import features
from .store import Store

MAX_BUYS_PER_AGENT_PER_TICK = 2
BLOCKED_LOG_COOLDOWN = 600   # seconds: don't spam "blocked" for the same token


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
             "address": snap.address if snap else extra.pop("address", None),
             "url": snap.url if snap else "", "headline": headline, "explain": explain, **extra}
        self.events.appendleft(e)
        self.store.event(e)

    # --- One tick ---------------------------------------------------------------
    def tick(self) -> None:
        held = {addr for acct in self.broker.accounts.values() for addr in acct.positions}
        held |= {o.address for o in self.broker.pending}
        snaps = self.feed.poll(keep=held)
        now, sol = self.feed.now(), self.feed.sol_usd
        if self.started_market is None:
            self.started_market = now
        if sol <= 0:
            # No SOL price yet (feed down or still starting): can't value or
            # trade anything. Still publish state so the dashboard shows why.
            with self.lock:
                self._state = self._build_state(0.0, now)
            return

        for fill in self.broker.fill_pending(snaps, sol, now):
            self._report_fill(fill, sol)

        feats = {a: features(s, now) for a, s in snaps.items()}
        ranked = sorted(snaps, key=lambda a: feats[a]["score"], reverse=True)

        for agent in self.agents:
            acct = self.broker.accounts[agent.name]
            lim = limits_for(self.limits, agent.exits)
            self._exits(agent, acct, snaps, now, lim)
            equity = acct.equity_sol(snaps, sol)
            acct.peak_equity_sol = max(acct.peak_equity_sol, equity)
            if not acct.killed and equity < acct.peak_equity_sol * (1 - lim.kill_switch_drawdown):
                self._kill(agent, f"equity fell {1 - equity / acct.peak_equity_sol:.0%} from its peak")
            if not self.paused:
                self._entries(agent, acct, snaps, feats, ranked, equity, sol, now, lim)
            self.equity_hist[agent.name].append((now, equity))
            self.store.equity(now, agent.name, equity)

        # Market-wide buy vs sell flow (USD, last 5 min), for the stream chart.
        buy_usd = sum(s.vol_m5 * s.buys_m5 / max(s.buys_m5 + s.sells_m5, 1) for s in snaps.values())
        self.flow_hist.append((now, buy_usd, sum(s.vol_m5 for s in snaps.values()) - buy_usd))

        self.store.commit()
        self.snaps, self.feats = snaps, feats
        self.tick_count += 1
        with self.lock:
            self._state = self._build_state(sol, now)

    def _exits(self, agent, acct, snaps, now, lim) -> None:
        for pos in list(acct.positions.values()):
            if self.broker.has_pending(agent.name, pos.address):
                continue
            reason = check_exit(pos, snaps.get(pos.address), now, lim)
            if reason:
                snap = snaps.get(pos.address)
                self.broker.submit(Order(agent.name, "SELL", pos.address, pos.symbol, reason=reason,
                                         submitted_at=now,
                                         decision_price_usd=snap.price_usd if snap else 0))

    def _entries(self, agent, acct, snaps, feats, ranked, equity, sol, now, lim) -> None:
        buys = 0
        for addr in ranked:
            if buys >= MAX_BUYS_PER_AGENT_PER_TICK:
                break
            if self.broker.has_pending(agent.name, addr):
                continue
            fire, checks = agent.evaluate(feats[addr])
            if not fire:
                continue
            snap = snaps[addr]
            size, blocked = check_entry(acct, snap, feats[addr], equity, sol, now, lim,
                                        agent.size_fraction)
            if blocked:
                key = (agent.name, addr)
                if now - self._blocked_seen.get(key, 0) > BLOCKED_LOG_COOLDOWN and "already holding it" not in blocked:
                    self._blocked_seen[key] = now
                    self._log("BLOCKED", agent, snap, f"{agent.name} wanted {snap.symbol}: risk said no",
                              [f"Signal fired: {agent.thesis}"] + [f"Blocked: {b}" for b in blocked],
                              checks=[c.as_dict() for c in checks])
                continue
            reason = "; ".join(f"{c.label} {c.value:,.2f} ({c.need})" for c in checks)
            self.broker.submit(Order(agent.name, "BUY", addr, snap.symbol, sol_amount=size,
                                     reason=reason, submitted_at=now,
                                     decision_price_usd=snap.price_usd,
                                     details={"checks": [c.as_dict() for c in checks]}))
            buys += 1

    def _kill(self, agent: Agent, why: str) -> None:
        acct = self.broker.accounts[agent.name]
        acct.killed = True
        now = self.feed.now()
        for pos in list(acct.positions.values()):
            if not self.broker.has_pending(agent.name, pos.address):
                self.broker.submit(Order(agent.name, "SELL", pos.address, pos.symbol,
                                         reason="KILL SWITCH: closing everything", submitted_at=now))
        self._log("KILL", agent, None, f"{agent.name} KILL SWITCH", [
            f"Why: {why}.",
            "All open positions are being sold and this agent will not open new ones.",
            "Restart the desk to reset it, after you've understood what went wrong."])

    def _report_fill(self, fill: dict, sol: float) -> None:
        o = fill["order"]
        agent = next(a for a in self.agents if a.name == o.agent)
        snap = fill.get("snap")
        if fill["kind"] == "BUY_FAILED":
            self._log("INFO", agent, None, f"{agent.name} buy of {o.symbol} failed",
                      [fill["why"], "The reserved SOL was returned."], symbol=o.symbol, address=o.address)
        elif fill["kind"] == "BUY":
            q = fill["quote"]
            self._log("BUY", agent, snap, f"BUY {o.symbol} for {o.sol_amount:.3f} SOL", [
                f"Why: {agent.thesis}",
                f"Filled one update after the signal, at ${fill['fill_price_usd']:.8g} "
                f"({fill['slippage_pct']:+.1f}% vs the price that triggered it).",
                f"Price impact {q['impact_pct']:.2f}% in a ${snap.liquidity_usd:,.0f} pool; "
                f"DEX fee ${q['fee_usd']:.2f} + network fee {config.NETWORK_FEE_SOL} SOL.",
                "Exits: stop loss, take profit, trailing stop, time stop and rug check "
                "are now watching it every tick.",
            ], checks=o.details.get("checks", []), sol=o.sol_amount,
               fill_price=fill["fill_price_usd"], impact_pct=q["impact_pct"],
               slippage_pct=fill["slippage_pct"])
        elif fill["kind"] == "SELL":
            pos, q = fill["position"], fill["quote"]
            self.exit_reasons[o.reason.split(":")[0]] += 1
            result = "PROFIT" if fill["pnl_sol"] > 0 else "LOSS"
            self._log("SELL", agent, snap, f"SELL {o.symbol}: {fill['pnl_pct']:+.1f}% ({result})", [
                f"Exit rule: {o.reason}.",
                f"Held {fill['held_minutes']:.0f} min. Paid {pos.cost_sol:.3f} SOL, "
                f"got back {q['sol_out']:.3f} SOL after fees and {q['impact_pct']:.2f}% price impact.",
                f"Bought because: {pos.entry_reason}.",
            ], symbol=o.symbol, address=o.address, pnl_sol=fill["pnl_sol"], pnl_pct=fill["pnl_pct"],
               reason=o.reason.split(":")[0])

    # --- Dashboard state ----------------------------------------------------------
    def _build_state(self, sol: float, now: float) -> dict:
        agents, positions = [], []
        for agent in self.agents:
            acct = self.broker.accounts[agent.name]
            eq = self.equity_hist[agent.name][-1][1] if self.equity_hist[agent.name] else acct.start_sol
            closed = acct.wins + acct.losses
            agents.append({
                "name": agent.name, "color": agent.color, "thesis": agent.thesis,
                "equity_sol": eq, "equity_usd": eq * sol, "cash_sol": acct.cash_sol,
                "pnl_sol": eq - acct.start_sol, "pnl_pct": (eq / acct.start_sol - 1) * 100,
                "realized_sol": acct.realized_pnl_sol, "fees_sol": acct.fees_sol,
                "open": len(acct.positions), "trades": closed,
                "win_rate": acct.wins / closed * 100 if closed else None,
                "killed": acct.killed,
                "equity_hist": [round(v, 5) for _, v in list(self.equity_hist[agent.name])[::2]],
            })
            for p in acct.positions.values():
                snap = self.snaps.get(p.address)
                value = quote_sell(p.tokens, snap, sol)["sol_out"]
                positions.append({
                    "agent": agent.name, "color": agent.color, "symbol": p.symbol,
                    "address": p.address, "url": snap.url if snap else "",
                    "cost_sol": p.cost_sol, "value_sol": value,
                    "pnl_pct": (value / p.cost_sol - 1) * 100 if p.cost_sol else 0,
                    "minutes": (now - p.opened_at) / 60,
                    "entry_price": p.entry_price_usd,
                    "price": snap.price_usd if snap else None,
                })

        held_by: dict = {}
        for p in positions:
            held_by.setdefault(p["address"], []).append(p["color"])
        tokens = []
        for addr, snap in sorted(self.snaps.items(), key=lambda kv: -self.feats[kv[0]]["score"])[:140]:
            f = self.feats[addr]
            tokens.append({
                "address": addr, "symbol": snap.symbol, "score": f["score"],
                "chg_m5": snap.chg_m5, "chg_h1": snap.chg_h1, "buy_ratio": round(f["buy_ratio_m5"], 2),
                "vol_accel": round(f["vol_accel"], 2), "liq": snap.liquidity_usd,
                "vol_m5": snap.vol_m5, "txns_m5": f["txns_m5"],
                "age_min": round(f["age_min"], 1) if f["age_min"] is not None else None,
                "held": held_by.get(addr, []), "url": snap.url,
            })

        total_start = sum(a["equity_sol"] - a["pnl_sol"] for a in agents)
        total_eq = sum(a["equity_sol"] for a in agents)
        return {
            "feed": self.feed.name, "live": self.feed.is_live, "status": self.feed.status,
            "paused": self.paused, "tick": self.tick_count, "sol_usd": sol,
            "market_time": now, "runtime_min": (now - (self.started_market or now)) / 60,
            "uptime_s": time.time() - self.started_wall,
            "total": {"equity_sol": total_eq, "start_sol": total_start,
                      "pnl_pct": (total_eq / total_start - 1) * 100 if total_start else 0,
                      "fees_sol": sum(a["fees_sol"] for a in agents),
                      "open": len(positions), "pending": len(self.broker.pending)},
            "agents": agents, "positions": positions, "tokens": tokens,
            "events": list(self.events)[:150],
            "flow": [(round(b), round(s)) for _, b, s in self.flow_hist],
            "exit_reasons": self.exit_reasons.most_common(),
            "costs": {"dex_fee": config.DEX_FEE, "network_fee_sol": config.NETWORK_FEE_SOL},
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
