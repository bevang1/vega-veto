"""
Phone-friendly reports, posted as comments on a daily GitHub issue.

Each comment @mentions you, so the GitHub mobile app sends a notification.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class GitHubIssue:
    """One issue per trading day; every job that day comments on the same one."""

    def __init__(self, repo: str, token: str) -> None:
        self.api = f"https://api.github.com/repos/{repo}"
        self.token = token
        self.number: int | None = None

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Accept": "application/vnd.github+json",
                                              "User-Agent": "vega-veto-stock-desk"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"null")

    def find_or_open(self, title: str, body: str) -> None:
        for issue in self._call("GET", "/issues", params={"state": "open", "per_page": 50}) or []:
            if issue.get("title") == title and "pull_request" not in issue:
                self.number = issue["number"]
                return
        self.number = self._call("POST", "/issues", {"title": title, "body": body})["number"]

    def comment(self, text: str) -> None:
        self._call("POST", f"/issues/{self.number}/comments", {"body": text})

    def close(self) -> None:
        self._call("PATCH", f"/issues/{self.number}", {"state": "closed", "state_reason": "completed"})


def money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def update_text(desk, number: int, since_event: int, mention: str, account: dict | None,
                title: str = "Update") -> str:
    s = desk.summary()
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    mtc = desk.minutes_to_close(desk.now())
    lines = [
        f"{mention} **{title} {number}** · {now} · market closes in {max(mtc, 0):.0f} min",
        f"**Desk P&L today: {money(s['total_pnl'])} ({s['total_pct']:+.2f}%)** on ${s['budget']:,.0f} of "
        f"agent budgets · open {len(s['positions'])} · orders in flight {s['pending']}",
    ]
    if account:
        eq, last = float(account.get("equity", 0)), float(account.get("last_equity", 0))
        if eq and last:
            lines.append(f"Paper account: ${eq:,.2f} ({money(eq - last)} today)")
    lines += [f"Feed: {s['status']}", "",
              "| Agent | P&L today | % of budget | Closed | Win rate | Open | State |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for a in s["agents"]:
        win = "—" if a["win_rate"] is None else f"{a['win_rate']:.0f}%"
        lines.append(f"| {a['name']} | {money(a['pnl'])} | {a['pnl_pct']:+.2f}% | {a['trades']} | {win} | "
                     f"{a['open']} | {'STOPPED' if a['killed'] else 'armed'} |")

    new = [e for e in desk.events if e["id"] > since_event]
    sells = [e for e in new if e["kind"] == "SELL"]
    buys = [e for e in new if e["kind"] == "BUY"]
    realised = sum(e.get("pnl_usd", 0) for e in sells)
    lines += ["", f"**Since last update:** {len(sells)} sell(s), realised {money(realised)} · {len(buys)} buy(s)"]
    for e in [e for e in new if e["kind"] in ("KILL", "INFO")][:5]:
        lines.append(f"- {'⛔' if e['kind'] == 'KILL' else 'ℹ️'} {e['headline']}")
    for e in sells[:12]:
        lines.append(f"- {'✅' if e['pnl_usd'] > 0 else '🔻'} SELL **{e['symbol']}** ({e['agent']}) "
                     f"{e['pnl_pct']:+.2f}% ({money(e['pnl_usd'])}) · {e.get('reason', '')}")
    if buys:
        lines.append("- 🟢 Bought: " + ", ".join(f"{e['symbol']} ({e['agent']}, ${e['usd']:,.0f})" for e in buys[:15]))
    if s["positions"]:
        lines += ["", "**Open now:** " + ", ".join(
            f"{p['symbol']} ({p['agent']}) {p['pnl_pct']:+.2f}%" for p in s["positions"])]
    lines += ["", "_Alpaca paper account: real-time prices, simulated fills, no real money._"]
    return "\n".join(lines)
