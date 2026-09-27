"""
Start the paper trading desk and its dashboard.

    python3 run.py            live crypto prices (BTC, ETH, SOL, XRP, ...), fake money
    python3 run.py --demo     simulated market (works offline), fake money

Then open http://127.0.0.1:8765 (it opens automatically). Leave it running;
Ctrl+C to stop. No exchange account, no keys, no real money anywhere in this program.
"""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

from paper_trader.demo_feed import DemoFeed
from paper_trader.engine import Engine
from paper_trader.feeds import CoinbaseFeed
from paper_trader.server import serve
from paper_trader.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto majors paper trading desk")
    parser.add_argument("--demo", action="store_true", help="use the simulated market")
    parser.add_argument("--seed", type=int, default=None, help="demo market random seed")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    feed = DemoFeed(seed=args.seed) if args.demo else CoinbaseFeed()
    store = Store(Path(__file__).resolve().parent / "data" / "crypto_desk.db")
    engine = Engine(feed, store)

    threading.Thread(target=engine.run_forever, daemon=True).start()
    server = serve(engine, args.port)
    url = f"http://127.0.0.1:{args.port}"
    mode = ("LIVE: real Coinbase prices for major coins, fake money" if feed.is_live
            else "DEMO: simulated prices, results mean nothing (drop --demo for live)")
    print(f"Paper desk running on {feed.name}\nMode: {mode}\nDashboard: {url}\nCtrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        engine.stopped = True
        print("\nStopped. Trade history is in data/crypto_desk.db")


if __name__ == "__main__":
    main()
