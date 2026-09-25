"""
Run the stock desk for one trading session (or part of one) and report.

    python3 stock_run.py                  run now if the market is open (or opens soon)
    python3 stock_run.py --print-only     print reports instead of posting to GitHub

Needs Alpaca PAPER keys in the environment: ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY.
On GitHub Actions it also uses GITHUB_TOKEN, GITHUB_REPOSITORY and NOTIFY_USER
to post updates on a daily issue.

A US session (6.5 h) is longer than a GitHub job may run (6 h), so the
workflow starts two jobs a day. The second picks up where the first stopped:
each agent's book is rebuilt from Alpaca's own order history.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from stock_desk.alpaca import Alpaca, AlpacaError
from stock_desk.engine import NEW_YORK, StockDesk, utcnow
from stock_desk.market import parse_time
from stock_desk.report import GitHubIssue, money, update_text
from stock_desk import config

MAX_WAIT_FOR_OPEN_MIN = 80   # covers the 1-hour shift when US clocks change


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the stock desk (Alpaca paper account)")
    parser.add_argument("--max-hours", type=float, default=5.8, help="stop after this long (GitHub limit is 6)")
    parser.add_argument("--every", type=float, default=5.0, help="minutes between updates")
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()
    started = time.time()
    deadline = started + args.max_hours * 3600

    try:
        api = Alpaca(os.environ.get("ALPACA_API_KEY_ID", ""), os.environ.get("ALPACA_API_SECRET_KEY", ""))
        clock = api.clock()
    except AlpacaError as e:
        sys.exit(f"Can't reach Alpaca: {e}")

    # Wait for the open if it's close; otherwise there's no session to trade.
    if not clock["is_open"]:
        wait = (parse_time(clock["next_open"]) - utcnow()).total_seconds()
        if wait > MAX_WAIT_FOR_OPEN_MIN * 60:
            print(f"Market closed; next open {clock['next_open']}. Nothing to do.")
            return
        print(f"Market opens in {wait / 60:.0f} min; waiting.", flush=True)
        time.sleep(max(0, wait) + 5)
        clock = api.clock()
        if not clock["is_open"]:
            print("Market didn't open (holiday?). Nothing to do.")
            return

    desk = StockDesk(api)
    desk.start()
    user = os.environ.get("NOTIFY_USER", "")
    mention = f"@{user}" if user else ""
    issue = None
    if not args.print_only:
        issue = GitHubIssue(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        day = utcnow().astimezone(NEW_YORK).strftime("%Y-%m-%d")
        issue.find_or_open(f"Stock desk · {day}", (
            f"{mention} Paper trading US stocks today in the Alpaca paper account. "
            f"4 agents with ${config.AGENT_BUDGET_USD:,} each. Updates every {args.every:g} min "
            "while the market is open; everything is sold before the close."))

    def send(text: str) -> None:
        print(text, "\n" + "-" * 60, flush=True)
        if issue:
            try:
                issue.comment(text)
            except Exception as e:  # a failed post must never stop trading
                print(f"post failed: {e!r}", flush=True)

    def account():
        try:
            return api.account()
        except AlpacaError:
            return None

    number, last_event = 0, desk._event_id
    next_report = time.time() + 60
    while time.time() < deadline and desk.minutes_to_close(utcnow()) > -2:
        t0 = time.time()
        try:
            desk.tick()
        except AlpacaError as e:
            desk.status = f"data error (will retry): {e}"
        if time.time() >= next_report:
            number += 1
            send(update_text(desk, number, last_event, mention, account()))
            last_event = desk._event_id
            next_report += args.every * 60
        time.sleep(max(0.0, config.TICK_SECONDS - (time.time() - t0)))

    closed = desk.minutes_to_close(utcnow()) <= 0
    if closed:
        # One last pass so end-of-day sells are recorded before the summary.
        try:
            desk._check_pending()
        except AlpacaError:
            pass
        s = desk.summary()
        send(f"🏁 **Day finished: {money(s['total_pnl'])} ({s['total_pct']:+.2f}%)**\n\n"
             + update_text(desk, number + 1, last_event, mention, account(), title="Final update"))
        if issue:
            try:
                issue.close()
            except Exception as e:
                print(f"close failed: {e!r}")
    else:
        send(update_text(desk, number + 1, last_event, mention, account(), title="Handover")
             + "\n\n_This job hit GitHub's time limit; the afternoon job continues from here._")


if __name__ == "__main__":
    main()
