"""Deployment preflight -- answer "can this host actually trade?" in one command.

The failure this exists to prevent: every process comes up healthy, the build is
green, and the system does nothing all day.  There are two ways that happens
here and neither prints an error on its own.

1. The Dhan token is an APP token.  TOTP generation mints APP tokens, and they
   CANNOT read market data -- POST /v2/optionchain needs a SELF token.  The
   options leg then reports "chain unavailable" and never opens a position, and
   a restored position cannot be marked or exited.  `daily_refresh` keeps a
   SELF token alive indefinitely via RenewToken, so the token you seed a new
   host with has to be SELF.

2. The journal is not where the workers think it is, so the dashboard shows an
   empty account while positions are live.

Run before the first deploy on a new host, and after any credential change:

    python -m parallax.apps.ops.preflight
    python -m parallax.apps.ops.preflight --strict    # exit 1 on any WARN too
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

#: name -> why it matters.  Values are never printed.
EXPECTED_ENV = {
    "DHAN_CLIENT_ID": "broker account id",
    "DHAN_ACCESS_TOKEN": "must be a SELF token -- see the APP/SELF check below",
    "DHAN_PIN": "with DHAN_TOTP_SECRET, mints a replacement token",
    "DHAN_TOTP_SECRET": "TOTP seed",
    "PARALLAX_DB_URL": "shared journal + token store; without it every container has its own",
    "PARALLAX_TELEGRAM_BOT_TOKEN": "notifications",
    "PARALLAX_TELEGRAM_CHAT_ID": "notifications",
    "DEEPSEEK_API_KEY": "AI veto layer",
}

_fail: list[str] = []
_warn: list[str] = []


def _line(status: str, msg: str) -> None:
    tag = {"ok": "  ok   ", "warn": "  WARN ", "fail": "  FAIL ", "info": "       "}[status]
    print(tag + msg)


def check_env() -> None:
    print("\n== environment ==")
    from parallax.adapters.env import env
    for name, why in EXPECTED_ENV.items():
        if env(name):
            _line("ok", f"{name} is set")
        else:
            _line("warn", f"{name} is NOT set  ({why})")
            _warn.append(f"{name} missing")


def check_database() -> None:
    print("\n== journal ==")
    from parallax.adapters import db
    if db.is_postgres():
        _line("ok", "backend: postgres (shared across processes)")
    else:
        _line("warn", "backend: SQLite at " + db.sqlite_path()
              + "  -- correct on one box, WRONG on a PaaS: each container gets "
                "its own ephemeral file, so the journal resets on redeploy and "
                "every worker authenticates separately")
        _warn.append("not using Postgres")
    try:
        from parallax.web.store import JournalStore
        s = JournalStore()
        p = s.positions()
        _line("ok", f"reachable -- {len(s.trades(limit=100000))} trades, "
                    f"{len(p)} open positions, mode={s.mode()}")
        if p:
            for row in p:
                _line("info", f"open: {row['instrument']} {row['side']} "
                              f"qty {row['qty']} entry {row['entry']}")
    except Exception as exc:
        _line("fail", f"unreachable: {type(exc).__name__}: {str(exc)[:160]}")
        _fail.append("journal unreachable")


def check_token() -> None:
    print("\n== Dhan token ==")
    from parallax.adapters.broker.dhan_auth import token_status
    try:
        st = token_status()
    except Exception as exc:
        _line("fail", f"could not read the token: {type(exc).__name__}: {str(exc)[:160]}")
        _fail.append("token unreadable")
        return

    ttype = (st.get("type") or "").upper()
    hours = st.get("hours_left", -1.0)
    if not st.get("valid"):
        _line("fail", f"no usable token (source={st.get('source')}, hours_left={hours})")
        _fail.append("no valid token")
        return

    _line("ok", f"valid -- {hours}h left, source={st.get('source')}, type={ttype or 'UNKNOWN'}")

    if "APP" in ttype:
        _line("fail", "this is an APP token.  Dhan APP tokens cannot read market "
                      "data: POST /v2/optionchain needs SELF.  The options leg "
                      "will report 'chain unavailable' and never open a position, "
                      "and a restored position cannot be marked or exited.")
        _fail.append("APP token cannot read market data")
    elif "SELF" in ttype:
        _line("ok", "SELF token -- market data available, and daily_refresh keeps "
                    "it alive via RenewToken without another browser login")
    else:
        _line("warn", f"tokenConsumerType is {ttype!r}, which is neither SELF nor "
                      f"APP -- cannot confirm market data will work")
        _warn.append("token type unknown")


def check_schedule() -> None:
    print("\n== schedule (IST) ==")
    from parallax.config.schedule import options_plan
    now = datetime.now(IST)
    _line("info", f"now {now:%Y-%m-%d %H:%M %a} IST")
    for i in range(5):
        d = (now + timedelta(days=i)).date()
        plan = options_plan(d)
        _line("info", f"{d} {d:%a}  options: {plan or 'none'}")


def check_scrip_master() -> None:
    print("\n== scrip master ==")
    from parallax.adapters.broker.dhan import default_scrip_master, ensure_scrip_master
    path = default_scrip_master()
    try:
        ensure_scrip_master(path)
        size = os.path.getsize(path) / (1 << 20)
        _line("ok", f"{path} ({size:.1f} MB) -- without this the futures leg "
                    f"cannot resolve a securityId and silently places nothing")
    except Exception as exc:
        _line("fail", f"unavailable at {path}: {type(exc).__name__}: {str(exc)[:160]}")
        _fail.append("scrip master unavailable")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify this host can trade.")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    ap.add_argument("--skip-net", action="store_true",
                    help="skip the scrip-master download")
    args = ap.parse_args(argv)

    print("PARALLAX preflight")
    check_env()
    check_database()
    check_token()
    check_schedule()
    if not args.skip_net:
        try:
            check_scrip_master()
        except Exception as exc:
            _line("warn", f"scrip master check skipped: {type(exc).__name__}")

    print("\n== verdict ==")
    for f in _fail:
        _line("fail", f)
    for w in _warn:
        _line("warn", w)
    if _fail:
        print("\nNOT READY -- fix the FAIL lines above before trading.")
        return 1
    if _warn and args.strict:
        print("\nNOT READY (--strict) -- warnings above.")
        return 1
    print("\nREADY" + (" (with warnings)" if _warn else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
