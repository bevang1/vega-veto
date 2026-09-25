"""
Run the desk with no dashboard and post a progress report every few minutes.

Built for GitHub Actions (see .github/workflows/live-watch.yml), so the
desk can run on live data while you're away from your Mac. Each report is
a comment on a GitHub issue that @mentions you, so the GitHub mobile app
pushes it to your phone.

    python3 watch.py --hours 5.5 --every 5          # post to GitHub (needs env vars below)
    python3 watch.py --demo --hours 0.05 --every 1 --print-only   # try it locally

GitHub settings come from the environment the workflow provides:
  GITHUB_TOKEN, GITHUB_REPOSITORY (owner/repo), NOTIFY_USER (who to @mention)
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from paper_trader.demo_feed import DemoFeed
from paper_trader.engine import Engine
from paper_trader.feeds import DexScreenerFeed
from paper_trader.store import Store


# --- GitHub issue comments ----------------------------------------------------
class GitHubPoster:
    def __init__(self, repo: str, token: str) -> None:
        self.api = f"https://api.github.com/repos/{repo}"
        self.token = token
        self.issue_number = None

    def _post(self, url: str, payload: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "vega-veto-watch",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())

    def open_issue(self, title: str, body: str) -> None:
        self.issue_number = self._post(f"{self.api}/issues", {"title": title, "body": body})["number"]

    def comment(self, body: str) -> None:
        self._post(f"{self.api}/issues/{self.issue_number}/comments", {"body": body})


# --- Report text ----------------------------------------------------------------
def signed(v: float, digits: int = 3) -> str:
    return f"{v:+.{digits}f}"


def report(engine: Engine, since_event_id: int, number: int, started: float, mention: str) -> str:
    """One plain-text update: desk P&L, each agent, and trades since the last update."""
    st = engine.state()
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    minutes = (time.time() - started) / 60
    if not st:
        return f"{mention} update {number} · {now} · no data yet. Feed: {engine.feed.status}"

    t = st["total"]
    pnl_sol = t["equity_sol"] - t["start_sol"]
    lines = [
        f"{mention} **Update {number}** · {now} · running {minutes:.0f} min",
        f"**Desk P&L: {signed(pnl_sol)} SOL ({t['pnl_pct']:+.2f}%)** · equity {t['equity_sol']:.3f} SOL "
        f"≈ ${t['equity_sol'] * st['sol_usd']:,.0f} · open {t['open']} · fees paid {t['fees_sol']:.3f} SOL",
        f"Feed: {st['status']} · SOL ${st['sol_usd']:,.2f}",
        "",
        "| Agent | P&L SOL | P&L % | Closed trades | Win rate | Open | State |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for a in st["agents"]:
        win = "—" if a["win_rate"] is None else f"{a['win_rate']:.0f}%"
        lines.append(f"| {a['name']} | {signed(a['pnl_sol'])} | {a['pnl_pct']:+.2f}% | {a['trades']} | "
                     f"{win} | {a['open']} | {'KILLED' if a['killed'] else 'armed'} |")

    new = [e for e in reversed(st["events"]) if e["id"] > since_event_id and e["kind"] in ("BUY", "SELL", "KILL")]
    # Sells first (that's where profit and loss get locked in), then alerts,
    # then buys condensed to one line so the notification stays readable.
    sells = [e for e in new if e["kind"] == "SELL"]
    buys = [e for e in new if e["kind"] == "BUY"]
    kills = [e for e in new if e["kind"] == "KILL"]
    realised = sum(e["pnl_sol"] for e in sells)
    lines += ["", f"**Since last update:** {len(sells)} sell(s), realised {signed(realised)} SOL · {len(buys)} buy(s)"]
    for e in kills:
        lines.append(f"- ⛔ {e['headline']}")
    for e in sells[:12]:
        lines.append(f"- {'✅' if e['pnl_sol'] > 0 else '🔻'} SELL **{e['symbol']}** ({e['agent']}) "
                     f"{e['pnl_pct']:+.1f}% ({signed(e['pnl_sol'])} SOL) · {e.get('reason', '')}")
    if len(sells) > 12:
        lines.append(f"- …and {len(sells) - 12} more sells")
    if buys:
        lines.append("- 🟢 Bought: " + ", ".join(f"{e['symbol']} ({e['agent'][:2]})" for e in buys[:20])
                     + (f" +{len(buys) - 20} more" if len(buys) > 20 else ""))
    if st["positions"]:
        lines += ["", "**Open now:** " + ", ".join(
            f"{p['symbol']} ({p['agent'][:2]}) {p['pnl_pct']:+.1f}%" for p in st["positions"])]
    lines += ["", "_Paper money only. Values are what selling now would return, after fees and price impact._"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the desk headless and post updates")
    parser.add_argument("--hours", type=float, default=5.5)
    parser.add_argument("--every", type=float, default=5.0, help="minutes between updates")
    parser.add_argument("--demo", action="store_true", help="simulated market (for testing)")
    parser.add_argument("--print-only", action="store_true", help="print updates instead of posting")
    args = parser.parse_args()

    feed = DemoFeed(seed=1) if args.demo else DexScreenerFeed()
    engine = Engine(feed, Store(None))
    user = os.environ.get("NOTIFY_USER", "")
    mention = f"@{user}" if user else ""

    poster = None
    if not args.print_only:
        poster = GitHubPoster(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        poster.open_issue(
            f"Live paper run · {stamp}",
            f"{mention} Vega Veto is running on **live Solana data with paper money** for "
            f"{args.hours:g} h. An update is posted here every {args.every:g} min.\n\n"
            "Each of the 4 agents starts with 10 fake SOL (40 SOL total).")

    def send(text: str) -> None:
        if poster:
            try:
                poster.comment(text)
            except Exception as e:  # a failed post must not stop the desk
                print(f"post failed: {e!r}")
        print(text, "\n" + "-" * 60, flush=True)

    started = time.time()
    end = started + args.hours * 3600
    next_report = started + 60          # first report after ~1 min, to confirm the feed works
    last_event, number = 0, 0
    while time.time() < end:
        tick_start = time.time()
        try:
            engine.tick()
        except Exception as e:
            feed.status = f"engine error: {e!r}"
        if time.time() >= next_report:
            number += 1
            send(report(engine, last_event, number, started, mention))
            last_event = max([e["id"] for e in engine.state().get("events", [])] + [last_event])
            next_report += args.every * 60
        time.sleep(max(0.0, feed.tick_seconds - (time.time() - tick_start)))

    send("**Final report**\n\n" + report(engine, last_event, number + 1, started, mention))


if __name__ == "__main__":
    main()
