"""SQLite journal store — the dashboard's single source of truth.

Trades, capital snapshots and open positions are recorded by the live workers
and read by the web dashboard.  Uses stdlib sqlite3 (no ORM).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("PARALLAX_DB", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "parallax.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JournalStore:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_settings(self):
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT)""")
            c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('mode','paper')")

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

    def mode(self) -> str:
        return self.get_setting("mode", "paper")

    def mode_is_live(self) -> bool:
        return self.mode() == "live"

    def set_mode(self, mode: str) -> str:
        m = "live" if str(mode).lower().startswith("live") else "paper"
        self.set_setting("mode", m)
        return m

    def _init(self):
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, strategy TEXT, instrument TEXT, side TEXT,
                qty REAL, entry REAL, exit REAL, pnl REAL, outcome TEXT, note TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS capital (
                ts TEXT PRIMARY KEY, equity REAL, available REAL, margin_used REAL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS positions (
                instrument TEXT PRIMARY KEY, strategy TEXT, side TEXT, qty REAL,
                entry REAL, stop REAL, target REAL, updated TEXT)""")
        self._init_settings()

    # ---- writes (called by the live workers) ----------------------------
    def record_trade(self, strategy, instrument, side, qty, entry, exit_price,
                     pnl, outcome, note=""):
        with self._connect() as c:
            c.execute("""INSERT INTO trades (ts,strategy,instrument,side,qty,entry,exit,pnl,outcome,note)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (_now(), strategy, instrument, side, qty, entry, exit_price,
                       pnl, outcome, note))

    def snapshot_capital(self, equity, available, margin_used=0.0):
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO capital VALUES (?,?,?,?)",
                      (_now(), equity, available, margin_used))

    def set_position(self, instrument, strategy, side, qty, entry, stop, target):
        with self._connect() as c:
            c.execute("""INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?)""",
                      (instrument, strategy, side, qty, entry, stop, target, _now()))

    def clear_positions(self):
        with self._connect() as c:
            c.execute("DELETE FROM positions")

    # ---- reads (dashboard) ---------------------------------------------
    def trades(self, strategy=None, limit=100):
        q = "SELECT ts,strategy,instrument,side,qty,entry,exit,pnl,outcome,note FROM trades"
        args = []
        if strategy:
            q += " WHERE strategy=?"
            args.append(strategy)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as c:
            rows = c.execute(q, args).fetchall()
        return [dict(zip(("ts","strategy","instrument","side","qty","entry","exit","pnl","outcome","note"), r)) for r in rows]

    def capital(self):
        with self._connect() as c:
            r = c.execute("SELECT ts,equity,available,margin_used FROM capital ORDER BY ts DESC LIMIT 1").fetchone()
        if not r:
            return {"ts": None, "equity": 0.0, "available": 0.0, "margin_used": 0.0}
        return dict(zip(("ts","equity","available","margin_used"), r))

    def positions(self):
        with self._connect() as c:
            rows = c.execute("SELECT instrument,strategy,side,qty,entry,stop,target,updated FROM positions").fetchall()
        return [dict(zip(("instrument","strategy","side","qty","entry","stop","target","updated"), r)) for r in rows]

    def summary(self) -> dict:
        """Aggregate P&L + stats for the dashboard."""
        with self._connect() as c:
            rows = c.execute("SELECT strategy, outcome, pnl FROM trades").fetchall()
        total = sum(r[2] for r in rows)
        by_strategy = {}
        for s, o, p in rows:
            d = by_strategy.setdefault(s, {"pnl": 0.0, "wins": 0, "losses": 0, "n": 0})
            d["pnl"] += p; d["n"] += 1
            d["wins" if p > 0 else "losses"] += 1
        for d in by_strategy.values():
            d["win_rate"] = round(d["wins"] / d["n"], 3) if d["n"] else 0.0
            d["pnl"] = round(d["pnl"], 0)
        return {"total_pnl": round(total, 0), "by_strategy": by_strategy,
                "n_trades": len(rows)}
