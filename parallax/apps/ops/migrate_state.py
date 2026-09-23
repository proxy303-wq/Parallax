"""Move PARALLAX's durable state between hosts.

Only five small things actually have to survive a migration:

    parallax.db            the journal -- trades, capital, open positions
    options_hold_*.json    live condor state; lose these and a worker forgets
                           an open position and never exits it
    .dhan_token.txt        the shared Dhan credential
    .dhan_token_generated  the one-generation-per-day guard

Everything else is cache that rebuilds itself: the 32MB scrip master and the
87MB research ladder caches.  Moving them only makes the migration slower and
more fragile.

The token travels too.  A worker that boots without one tries to generate one,
and every TOTP generation invalidates the previous token -- so a migration that
drops the token turns into six workers taking turns destroying each other's
session.

    python -m parallax.apps.ops.migrate_state export --out bundle.json
    PARALLAX_DB_URL=postgres://... python -m parallax.apps.ops.migrate_state import --in bundle.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

BUNDLE_VERSION = 1

_TRADE_COLS = ("id", "ts", "strategy", "instrument", "side", "qty",
               "entry", "exit", "pnl", "outcome", "note")
_ROW_COLS = {
    "capital": ("ts", "equity", "available", "margin_used"),
    "positions": ("instrument", "strategy", "side", "qty", "entry",
                  "stop", "target", "updated"),
}


def _read_table(conn, table, cols):
    try:
        rows = conn.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall()
    except sqlite3.Error:
        return []
    return [dict(zip(cols, r)) for r in rows]


def export_bundle(db_path, state_dir=".", include_token=True):
    if not os.path.exists(db_path):
        raise SystemExit(f"no such journal: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        bundle = {
            "version": BUNDLE_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": db_path,
            "settings": {k: v for k, v in _read_table(conn, "settings", ("key", "value"))
                         and [(r["key"], r["value"]) for r in _read_table(conn, "settings", ("key", "value"))]},
            "trades": _read_table(conn, "trades", _TRADE_COLS),
            "capital": _read_table(conn, "capital", _ROW_COLS["capital"]),
            "positions": _read_table(conn, "positions", _ROW_COLS["positions"]),
        }
    finally:
        conn.close()

    holds = {}
    for path in sorted(glob.glob(os.path.join(state_dir, "options_hold_*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                holds[os.path.basename(path)] = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  ! skipping unreadable {path}: {exc}")
    bundle["hold_files"] = holds

    bundle["token"] = None
    bundle["token_generated_at"] = None
    if include_token:
        tok_path = os.path.join(state_dir, ".dhan_token.txt")
        if os.path.exists(tok_path):
            bundle["token"] = open(tok_path, encoding="utf-8-sig").read().strip() or None
        stamp = os.path.join(state_dir, ".dhan_token_generated")
        if os.path.exists(stamp):
            try:
                bundle["token_generated_at"] = float(
                    open(stamp, encoding="utf-8").read().strip())
            except (OSError, ValueError):
                pass
    return bundle


def import_bundle(bundle, state_dir=".", dry_run=False, restore_token=True):
    from parallax.adapters import db

    if bundle.get("version") != BUNDLE_VERSION:
        raise SystemExit(f"unsupported bundle version: {bundle.get('version')!r}")

    # We may be the first thing to touch this database, so build the schema
    # rather than assuming a store instance already did.
    db.ensure_schema()

    writes = 0
    with db.connect() as c:
        for key, value in (bundle.get("settings") or {}).items():
            c.execute(db.upsert_sql("settings", ("key", "value"), ("key",)), (key, value))
            writes += 1

        for row in bundle.get("trades") or []:
            c.execute(db.insert_ignore_sql("trades", _TRADE_COLS),
                      tuple(row.get(col) for col in _TRADE_COLS))
            writes += 1

        for row in bundle.get("capital") or []:
            cols = _ROW_COLS["capital"]
            c.execute(db.upsert_sql("capital", cols, ("ts",)),
                      tuple(row.get(col) for col in cols))
            writes += 1

        for row in bundle.get("positions") or []:
            cols = _ROW_COLS["positions"]
            c.execute(db.upsert_sql("positions", cols, ("instrument",)),
                      tuple(row.get(col) for col in cols))
            writes += 1

    if db.is_postgres():
        # Inserting explicit ids leaves the sequence at 1, so the next live
        # trade would collide on the primary key.  Advance it past the imported
        # rows or the first real trade after cutover fails.
        with db.connect() as c:
            c.execute("SELECT setval(pg_get_serial_sequence('trades','id'), "
                      "COALESCE((SELECT MAX(id) FROM trades), 1))")

    if restore_token and bundle.get("token"):
        db.state_set("dhan_access_token", bundle["token"])
    if restore_token and bundle.get("token_generated_at"):
        db.state_set("dhan_token_generated_at", str(bundle["token_generated_at"]))

    if not dry_run:
        for name, payload in (bundle.get("hold_files") or {}).items():
            path = os.path.join(state_dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            print(f"  restored {path}")

    return {
        "settings": len(bundle.get("settings") or {}),
        "trades": len(bundle.get("trades") or []),
        "capital": len(bundle.get("capital") or []),
        "positions": len(bundle.get("positions") or []),
        "hold_files": len(bundle.get("hold_files") or {}),
        "token": bool(bundle.get("token")),
        "writes": writes,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Migrate PARALLAX durable state.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export", help="read the local journal into a JSON bundle")
    ex.add_argument("--db", default=os.environ.get("PARALLAX_DB", "parallax.db"))
    ex.add_argument("--state-dir", default=".")
    ex.add_argument("--out", required=True)
    ex.add_argument("--no-token", action="store_true",
                    help="omit the Dhan credential from the bundle")

    im = sub.add_parser("import", help="write a bundle into the configured database")
    im.add_argument("--in", dest="inp", required=True)
    im.add_argument("--state-dir", default=".")
    im.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "export":
        bundle = export_bundle(args.db, args.state_dir, include_token=not args.no_token)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=1)
        os.chmod(args.out, 0o600)
        print(f"exported -> {args.out}")
        for k in ("settings", "trades", "capital", "positions", "hold_files"):
            v = bundle.get(k) or {}
            print(f"  {k:12s} {len(v)}")
        print(f"  {'token':12s} {'yes' if bundle.get('token') else 'no'}")
        return 0

    with open(args.inp, encoding="utf-8") as fh:
        bundle = json.load(fh)
    stats = import_bundle(bundle, args.state_dir, dry_run=args.dry_run)
    print(("dry-run " if args.dry_run else "") + "imported: " + ", ".join(
        f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
