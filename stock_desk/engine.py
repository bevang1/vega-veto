"""
The stock desk engine: one loop that runs a trading day.

start():  read the market clock, rebuild each agent's book from today's
          Alpaca orders (so a restart mid-day loses nothing), load today's
          1-minute bars.
tick():   every 30 s:
  1. Pull live snapshots for the watch list.
  2. Check orders sent last tick: filled -> update the agent's book.
  3. For each agent: exits first (incl. end-of-day), then the daily loss
     limit, then at most one new buy.

Orders are real orders in Alpaca's PAPER account: Alpaca decides the fill
price, so the P&L is what its simulator says actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .agents import StockAgent, default_agents
from .alpaca import Alpaca, AlpacaError
from .config import RiskLimits
from .ledger import Book, new_order_id, rebuild
from .market import MinuteHistory, Quote, parse_time
from .risk import check_entry, check_exit, limits_for
from .signals import features

NEW_YORK = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Pending:
    code: str
    side: str
    symbol: str
    reason: str
    checks: list
    tries: int = 0


class StockDesk:
    def __init__(self, api: Alpaca, agents: list[StockAgent] | None = None,
                 limits: RiskLimits = RiskLimits(), now_fn=utcnow) -> None:
        self.api = api
        self.agents = agents or default_agents()
        self.by_code = {a.code: a for a in self.agents}
        self.limits = limits
        self.now = now_fn
        self.books = {a.code: Book(a.code, config.AGENT_BUDGET_USD) for a in self.agents}
        self.hist = MinuteHistory()
        self.quotes: dict[str, Quote] = {}
        self.pending: dict[str, Pending] = {}
        self.events: list[dict] = []
        self.symbols = list(config.UNIVERSE)
        self.open_time: datetime | None = None
        self.close_time: datetime | None = None
        self.status = "starting"
        self._event_id = 0
        self._actives_at: datetime | None = None

    # --- Setup ----------------------------------------------------------------------
    def start(self) -> None:
        clock = self.api.clock()
        now = self.now()
        today_ny = now.astimezone(NEW_YORK).date()
        self.open_time = datetime.combine(today_ny, time(9, 30), NEW_YORK).astimezone(timezone.utc)
        # next_close is today's close while the market is open (handles half-days).
        self.close_time = parse_time(clock["next_close"]).astimezone(timezone.utc)
        restored = rebuild(self.books, self.api.orders_since(self.open_time.isoformat()))
        if restored:
            self._log("INFO", None, None, f"Restored {restored} filled order(s) from earlier today",
                      ["Picked up where the previous job left off, using Alpaca's order history."])
        self._refresh_symbols(now)
        self._backfill(self.symbols)

    def _refresh_symbols(self, now: datetime) -> None:
        try:
            actives = self.api.most_actives(config.MOST_ACTIVES_TOP)
        except AlpacaError as e:
            self.status = f"screener unavailable: {e}"
            actives = []
        held = {s for b in self.books.values() for s in b.lots}
        new = list(dict.fromkeys(config.UNIVERSE + actives + sorted(held)))
        added = [s for s in new if s not in self.symbols]
        self.symbols = new
        self._actives_at = now
        if added and self.hist.bars:
            self._backfill(added)

    def _backfill(self, symbols: list[str]) -> None:
        for sym, bars in self.api.minute_bars(symbols, self.open_time.isoformat()).items():
            for bar in bars:
                self.hist.add(sym, bar)

    # --- Helpers --------------------------------------------------------------------
    def minutes_to_close(self, now: datetime) -> float:
        return (self.close_time - now).total_seconds() / 60 if self.close_time else 999.0

    def _log(self, kind: str, agent: StockAgent | None, symbol: str | None, headline: str,
             explain: list[str], **extra) -> None:
        self._event_id += 1
        self.events.append({"id": self._event_id, "t": self.now().isoformat(), "kind": kind,
                            "agent": agent.name if agent else None, "symbol": symbol,
                            "headline": headline, "explain": explain, **extra})

    def _submit(self, agent: StockAgent, side: str, symbol: str, reason: str,
                notional: float | None = None, qty: float | None = None, checks=None) -> None:
        oid = new_order_id(agent.code)
        try:
            order = self.api.submit_order(symbol, side, oid, notional=notional, qty=qty)
            self.pending[order["id"]] = Pending(agent.code, side, symbol, reason, checks or [])
        except AlpacaError as e:
            self._log("INFO", agent, symbol, f"{side.upper()} {symbol} order rejected", [str(e)])

    # --- One tick -------------------------------------------------------------------
    def tick(self) -> None:
        now = self.now()
        if self._actives_at is None or now - self._actives_at > timedelta(minutes=10):
            self._refresh_symbols(now)

        snaps = self.api.snapshots(self.symbols)
        today = now.astimezone(NEW_YORK).date().isoformat()
        for sym, snap in snaps.items():
            q = Quote.from_snapshot(sym, snap)
            if q.price > 0:
                self.quotes[sym] = q
            self.hist.add(sym, snap.get("minuteBar"))
        self.status = f"ok: {len(self.quotes)} of {len(self.symbols)} stocks"

        self._check_pending()
        mtc = self.minutes_to_close(now)
        for agent in self.agents:
            book = self.books[agent.code]
            lim = limits_for(self.limits, agent.exits)
            self._exits(agent, book, now, mtc, lim)
            self._loss_limit(agent, book, lim)
            if not book.killed and mtc > lim.entry_stop_minutes:
                self._entries(agent, book, now, mtc, lim, today)

    def _check_pending(self) -> None:
        for oid, p in list(self.pending.items()):
            try:
                o = self.api.get_order(oid)
            except AlpacaError:
                continue
            status = o.get("status", "")
            qty, price = float(o.get("filled_qty") or 0), float(o.get("filled_avg_price") or 0)
            agent, book = self.by_code[p.code], self.books[p.code]
            done = status in ("filled", "canceled", "expired", "rejected", "done_for_day")
            if not done:
                p.tries += 1
                continue
            del self.pending[oid]
            if qty <= 0 or price <= 0:
                self._log("INFO", agent, p.symbol, f"{p.side.upper()} {p.symbol} not filled ({status})",
                          ["Alpaca didn't fill the order, so nothing changed."])
                continue
            when = parse_time(o["filled_at"]) if o.get("filled_at") else self.now()
            if p.side == "buy":
                book.apply_fill(p.symbol, "buy", qty, price, when, p.reason)
                self._log("BUY", agent, p.symbol, f"BUY {p.symbol}: ${qty * price:,.0f}", [
                    f"Why: {agent.thesis}",
                    f"Filled {qty:.4f} shares at ${price:,.2f} in Alpaca's paper account.",
                    "Exits (stop, target, trailing, time, end of day) now watch it every 30 s."],
                    usd=qty * price, checks=p.checks)
            else:
                lot = book.lots.get(p.symbol)
                cost = lot.avg_price if lot else price
                pnl = book.apply_fill(p.symbol, "sell", qty, price, when) or 0.0
                pct = (price / cost - 1) * 100
                self._log("SELL", agent, p.symbol, f"SELL {p.symbol}: {pct:+.2f}% (${pnl:+,.2f})", [
                    f"Exit rule: {p.reason}.",
                    f"Bought at ${cost:,.2f}, sold at ${price:,.2f}."],
                    pnl_usd=pnl, pnl_pct=pct, reason=p.reason.split(":")[0])

    def _selling(self, code: str, symbol: str) -> bool:
        return any(p.code == code and p.symbol == symbol for p in self.pending.values())

    def _exits(self, agent, book, now, mtc, lim) -> None:
        for lot in list(book.lots.values()):
            q = self.quotes.get(lot.symbol)
            if not q or self._selling(agent.code, lot.symbol):
                continue
            reason = check_exit(lot, q.price, now, mtc, lim)
            if reason:
                self._submit(agent, "sell", lot.symbol, reason, qty=lot.qty)

    def unrealized(self, book: Book) -> float:
        total = 0.0
        for lot in book.lots.values():
            q = self.quotes.get(lot.symbol)
            if q:
                total += lot.qty * (q.sell_price - lot.avg_price)
        return total

    def _loss_limit(self, agent, book, lim) -> None:
        pnl = book.realized + self.unrealized(book)
        if not book.killed and pnl <= -book.budget * lim.agent_daily_loss:
            book.killed = True
            for lot in list(book.lots.values()):
                if not self._selling(agent.code, lot.symbol):
                    self._submit(agent, "sell", lot.symbol, "DAILY LOSS LIMIT: closing everything", qty=lot.qty)
            self._log("KILL", agent, None, f"{agent.name} hit its daily loss limit", [
                f"Down ${-pnl:,.2f} today (limit {lim.agent_daily_loss:.0%} of its ${book.budget:,.0f}).",
                "It has sold everything and won't trade again until tomorrow."])

    def _entries(self, agent, book, now, mtc, lim, today: str) -> None:
        ranked = []
        for sym, q in self.quotes.items():
            if q.day_date != today or self._selling(agent.code, sym):
                continue  # stale pre-market data, or an order already in flight
            f = features(q, self.hist, self.open_time, now)
            fire, checks = agent.evaluate(f)
            if fire:
                ranked.append((f["rel_volume"], sym, f, checks))
        for _, sym, f, checks in sorted(ranked, key=lambda r: r[0], reverse=True):
            size, blocked = check_entry(book, sym, f, mtc, now, lim)
            if not blocked:
                reason = "; ".join(f"{c.label} {c.value:,.2f} ({c.need})" for c in checks)
                self._submit(agent, "buy", sym, reason, notional=size, checks=[c.as_dict() for c in checks])
                return  # at most one new buy per agent per tick

    # --- For reports ----------------------------------------------------------------
    def summary(self) -> dict:
        agents, positions = [], []
        for a in self.agents:
            b = self.books[a.code]
            unreal = self.unrealized(b)
            closed = b.wins + b.losses
            agents.append({"name": a.name, "code": a.code, "pnl": b.realized + unreal,
                           "pnl_pct": (b.realized + unreal) / b.budget * 100, "realized": b.realized,
                           "trades": closed, "win_rate": b.wins / closed * 100 if closed else None,
                           "open": len(b.lots), "killed": b.killed})
            for lot in b.lots.values():
                q = self.quotes.get(lot.symbol)
                px = q.sell_price if q else lot.avg_price
                positions.append({"agent": a.code, "symbol": lot.symbol,
                                  "pnl_pct": (px / lot.avg_price - 1) * 100,
                                  "pnl": lot.qty * (px - lot.avg_price)})
        total = sum(a["pnl"] for a in agents)
        budget = config.AGENT_BUDGET_USD * len(agents)
        return {"agents": agents, "positions": positions, "total_pnl": total,
                "total_pct": total / budget * 100, "budget": budget, "status": self.status,
                "pending": len(self.pending)}
