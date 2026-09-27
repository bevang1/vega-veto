"""
Save every decision to a local SQLite file (data/crypto_desk.db).

The dashboard shows the recent past; this file keeps everything, across
runs. Open it with DuckDB (it reads SQLite files directly) to analyse your
agents afterwards, e.g.:

    duckdb -c "INSTALL sqlite; LOAD sqlite;
               SELECT agent, COUNT(*), SUM(pnl_usd)
               FROM sqlite_scan('data/crypto_desk.db', 'events')
               WHERE kind = 'SELL' GROUP BY agent"
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT, market_time REAL, kind TEXT, agent TEXT, symbol TEXT,
    pnl_usd REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    run_id TEXT, market_time REAL, agent TEXT, equity_usd REAL
);
"""


class Store:
    def __init__(self, path: Path | None) -> None:
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.con = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # The engine thread is the only writer, but the connection is
            # created on the main thread, hence check_same_thread=False.
            self.con = sqlite3.connect(path, check_same_thread=False)
            self.con.executescript(SCHEMA)

    def event(self, e: dict) -> None:
        if self.con:
            self.con.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (self.run_id, e["t"], e["kind"], e.get("agent"), e.get("symbol"),
                 e.get("pnl_usd"), json.dumps(e, default=str)))

    def equity(self, t: float, agent: str, equity_usd: float) -> None:
        if self.con:
            self.con.execute("INSERT INTO equity VALUES (?,?,?,?)", (self.run_id, t, agent, equity_usd))

    def commit(self) -> None:
        if self.con:
            self.con.commit()
