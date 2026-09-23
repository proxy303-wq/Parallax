"""Journal store -- the dashboard's single source of truth.

Trades, capital snapshots and open positions are recorded by the live workers
and read by the web dashboard.

Backed by SQLite on a single box and by Postgres once the workers run as
separate containers; see parallax/adapters/db.py for why.  Call sites stay
dialect-agnostic -- only the DDL and the upserts differ, and both come from
that module.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from parallax.adapters import db

DB_PATH = db.DEFAULT_SQLITE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JournalStore:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._init()

    def _connect(self):
        """Connection context manager; the path is ignored under Postgres."""
        return db.connect(self.path)

    def _init_settings(self):
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT)""")
            c.execute(db.insert_ignore_sql("settings", ("key", "value")), ("mode", "paper"))
            c.execute(db.insert_ignore_sql("settings", ("key", "value")),
                      ("paper_capital", "800000"))

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as c:
            c.execute(db.upsert_sql("settings", ("key", "value"), ("key",)), (key, value))

    def mode(self) -> str:
        return self.get_setting("mode", "paper")

    def mode_is_live(self) -> bool:
        return self.mode() == "live"

    def paper_capital(self) -> float:
        """Fixed capital used in PAPER mode (default Rs8,00,000)."""
        try:
            return float(self.get_setting("paper_capital", "800000") or 800000)
        except (TypeError, ValueError):
            return 800000.0

    def set_paper_capital(self, value: float) -> float:
        v = float(value)
        self.set_setting("paper_capital", str(v))
        return v

    def effective_capital(self, dhan_equity: float = 0.0) -> float:
        """PAPER -> the fixed paper capital; LIVE -> the real Dhan balance."""
        if self.mode_is_live():
            return float(dhan_equity or 0.0)
        return self.paper_capital()

    # Three modes.  paper = local simulation, demo = Delta testnet, live = real money.
    MODES = ("paper", "demo", "live")

    def set_mode(self, mode: str) -> str:
        s = str(mode).lower()
        m = "live" if s.startswith("live") else ("demo" if s.startswith("demo") else "paper")
        self.set_setting("mode", m)
        return m

    def next_mode(self) -> str:
        """Cycle paper -> demo -> live -> paper (what the dashboard button does)."""
        try:
            i = self.MODES.index(self.mode())
        except ValueError:
            i = 0
        return self.MODES[(i + 1) % len(self.MODES)]

    def _init(self):
        db.ensure_schema(self.path)
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
            c.execute(db.upsert_sql(
                "capital", ("ts", "equity", "available", "margin_used"), ("ts",)),
                (_now(), equity, available, margin_used))

    def set_position(self, instrument, strategy, side, qty, entry, stop, target):
        with self._connect() as c:
            c.execute(db.upsert_sql(
                "positions",
                ("instrument", "strategy", "side", "qty", "entry", "stop", "target", "updated"),
                ("instrument",)),
                (instrument, strategy, side, qty, entry, stop, target, _now()))

    def clear_positions(self):
        with self._connect() as c:
            c.execute("DELETE FROM positions")

    def clear_position(self, instrument):
        """Clear ONE instrument's row.

        A worker closing its own trade must never wipe the table: with two crypto
        workers sharing this journal, a global DELETE erases the other symbol's
        open position and the dashboard silently shows nothing while a position
        is live.
        """
        with self._connect() as c:
            c.execute("DELETE FROM positions WHERE instrument=?", (instrument,))

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
