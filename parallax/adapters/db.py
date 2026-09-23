"""Shared database handle: SQLite on one box, Postgres once we run as containers.

The dashboard journal, the open-position state and the Dhan access token all
used to be local files.  That is correct on a single VPS and quietly fatal on a
PaaS, where the workers run as separate containers each with its own ephemeral
filesystem:

  * every container boots with no token file, generates its own TOTP token, and
    Dhan invalidates the previous one -- so the workers take turns killing each
    other's session and nothing can trade.  It looks like a Dhan outage and is
    not one;
  * the journal and the open-position state vanish on every redeploy, so a live
    condor is forgotten and never exited.

Both are one bug: shared state kept in per-container files.  This module gives
that state a single home.  SQLite stays the default so local development and
the test suite need no server; setting PARALLAX_DB_URL to a postgres:// DSN
switches every caller over.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SQLITE = os.path.join(_REPO_ROOT, "parallax.db")

#: pg_advisory_lock key -- 0x504152414C4C is "PARALL" in ASCII.  Advisory locks
#: share one namespace across a whole Postgres instance, so this keeps us from
#: colliding with any other tenant's locks.
TOKEN_LOCK = 0x504152414C4C

_state_ready = False


# ---------------------------------------------------------------- dialect ---

def database_url() -> str:
    return (os.environ.get("PARALLAX_DB_URL") or "").strip()


def is_postgres() -> bool:
    return database_url().startswith(("postgres://", "postgresql://"))


def sqlite_path() -> str:
    return os.environ.get("PARALLAX_DB") or DEFAULT_SQLITE


def adapt(sql: str) -> str:
    """SQLite uses '?' placeholders, Postgres uses '%s'.

    No statement in this codebase contains a literal '%' or '?' inside a string
    literal, which is what makes a plain substitution safe here.
    """
    return sql.replace("?", "%s") if is_postgres() else sql


def _ph() -> str:
    return "%s" if is_postgres() else "?"


def id_column() -> str:
    """Autoincrement primary-key DDL for the active dialect."""
    return "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def float_column() -> str:
    """REAL affinity in SQLite, 8-byte in Postgres -- never lossy for money."""
    return "DOUBLE PRECISION"


def upsert_sql(table: str, cols, pk) -> str:
    """INSERT-or-replace that means the same thing on both engines."""
    cols, pk = tuple(cols), tuple(pk)
    marks = ",".join([_ph()] * len(cols))
    names = ",".join(cols)
    if not is_postgres():
        return f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})"
    rest = [c for c in cols if c not in pk]
    conflict = f"ON CONFLICT ({','.join(pk)}) DO "
    if rest:
        sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in rest)
        return f"INSERT INTO {table} ({names}) VALUES ({marks}) {conflict}UPDATE SET {sets}"
    return f"INSERT INTO {table} ({names}) VALUES ({marks}) {conflict}NOTHING"


def insert_ignore_sql(table: str, cols) -> str:
    cols = tuple(cols)
    marks = ",".join([_ph()] * len(cols))
    names = ",".join(cols)
    if not is_postgres():
        return f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({marks})"
    return f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT DO NOTHING"


# ------------------------------------------------------------- connection ---

class _PgConn:
    """psycopg connections have no .execute(), sqlite3.Connection does.

    This shim makes the two interchangeable so call sites never branch on the
    backend.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(adapt(sql), params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _pg_driver():
    try:
        import psycopg
        return psycopg
    except ImportError:
        pass
    try:
        import psycopg2
        return psycopg2
    except ImportError as exc:  # pragma: no cover - deployment misconfiguration
        raise RuntimeError(
            "PARALLAX_DB_URL is set but no Postgres driver is installed. "
            "Fix with: pip install 'psycopg[binary]'"
        ) from exc


def _pg_connect():
    dsn = database_url()
    drv = _pg_driver()
    # psycopg2 cannot parse the bare postgres:// scheme some platforms inject.
    if drv.__name__ == "psycopg2" and dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    return drv.connect(dsn)


@contextlib.contextmanager
def connect(path: str | None = None):
    """Yield a connection, committing on clean exit and always closing.

    Closing matters on Postgres: instances have a hard connection limit and the
    workers poll in a loop, so leaking one per call would exhaust it in minutes.
    """
    if is_postgres():
        conn = _pg_connect()
        try:
            with conn:
                yield _PgConn(conn)
        finally:
            conn.close()
        return
    conn = sqlite3.connect(path or sqlite_path())
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ------------------------------------------------- shared key/value state ---

def ensure_schema(path: str | None = None) -> None:
    """Create the journal tables.

    Defined once and shared by the store and by the migration importer.  An
    importer that assumes somebody else already built the schema fails on an
    empty database -- which is precisely the database you migrate INTO.

    `path` must be threaded through: a store pointed at its own SQLite file
    would otherwise get its tables created in the default database and fail
    with "no such table" on first write.
    """
    f = float_column()
    with connect(path) as c:
        c.execute(f"""CREATE TABLE IF NOT EXISTS trades (
            id {id_column()},
            ts TEXT, strategy TEXT, instrument TEXT, side TEXT,
            qty {f}, entry {f}, exit {f}, pnl {f}, outcome TEXT, note TEXT)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS capital (
            ts TEXT PRIMARY KEY, equity {f}, available {f}, margin_used {f})""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS positions (
            instrument TEXT PRIMARY KEY, strategy TEXT, side TEXT, qty {f},
            entry {f}, stop {f}, target {f}, updated TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT)""")


def ensure_state() -> None:
    """Create the shared key/value table once per process."""
    global _state_ready
    if _state_ready:
        return
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY, value TEXT, updated TEXT)""")
    _state_ready = True


def state_get(key: str, default: str | None = None) -> str | None:
    ensure_state()
    with connect() as c:
        row = c.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def state_set(key: str, value: str) -> None:
    ensure_state()
    with connect() as c:
        c.execute(upsert_sql("state", ("key", "value", "updated"), ("key",)),
                  (key, value, datetime.now(timezone.utc).isoformat()))


# --------------------------------------------------------------- locking ----

@contextlib.contextmanager
def advisory_lock(key: int = TOKEN_LOCK):
    """Serialise a critical section across processes.

    No-op on SQLite, which is single-host by definition.  On Postgres this is
    what stops six containers that all boot tokenless from firing six TOTP
    generations at once -- Dhan rate-limits those to one per two minutes and
    every success invalidates the previous token, so the losers would each
    destroy the winner's session.

    The lock lives on a connection of its own that MUST stay open for the whole
    critical section, because pg_advisory_lock is session-scoped: end the
    session and the lock goes with it.  psycopg3's `with connection:` block
    CLOSES the connection on exit (unlike psycopg2, which merely ends the
    transaction), so wrapping this in one dropped the lock the instant it was
    taken and every worker sailed straight through.  Commit explicitly instead.
    """
    if not is_postgres():
        yield
        return
    conn = _pg_connect()
    try:
        conn.cursor().execute("SELECT pg_advisory_lock(%s)", (key,))
        conn.commit()
        try:
            yield
        finally:
            # The body may have left an aborted transaction behind; clear it
            # before touching the connection again.
            try:
                conn.rollback()
            except Exception:
                pass
            conn.cursor().execute("SELECT pg_advisory_unlock(%s)", (key,))
            conn.commit()
    finally:
        conn.close()
