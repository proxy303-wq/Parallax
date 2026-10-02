"""One-off PAPER probe of the 4-leg "positive payoff" screen structure.

Origin.  On 2026-10-01 a strategy builder showed this NIFTY 6 Oct position:

    SELL 21550 CE / BUY 22000 CE / BUY 21550 PE / SELL 21800 PE   (5 lots)

with a quoted net credit of 712.55 points and Max Loss displayed as zero, so
the payoff chart appeared to sit above zero at every settlement price.

That credit does not survive executable prices.  It rests on one leg, the
21550 CE, which traded ZERO contracts that day, has 390 OI, is quoted
845.10 bid / 1146.30 ask, and whose last trade sat 266 points above its own
no-arbitrage ceiling (C(21550) - C(21950) must be <= 400; the mid implies
480.05).  At the bid the credit is 376.75 and the floor is MINUS 73.25.

So the question is empirical, not arithmetic: what does this structure
actually cost, and what does it actually pay, when you have to cross the
spread?  This probe answers it on the next session and holds to settlement.

It is PAPER ONLY.  It never constructs a broker, never sends an order, and
needs no margin.  It writes a JSON record and telegrams every line.

Two variants are opened side by side, because the screen's strikes were
absolute numbers:

    fixed     the exact screen strikes (21550 / 21800 / 22000)
    restruck  the same offsets in strikes below the entry ATM

Entry is recorded on three price bases so the gap between the screen and the
market is measured rather than argued:

    ltp          what a strategy builder displays
    mid          the middle of the book
    executable   sell at bid, buy at ask   <- what you would actually get

Run:  python -m parallax.apps.ops.box_probe --selftest
      python -m parallax.apps.ops.box_probe --once      (enter or settle now)
      python -m parallax.apps.ops.box_probe             (wait, then hold)
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone

from parallax.adapters.broker.dhan_auth import daily_refresh
from parallax.adapters.env import env
from parallax.adapters.market_data.dhan_options import fetch_option_chain
from parallax.adapters.telegram import TelegramBot

IST = timezone(timedelta(hours=5, minutes=30))

SYMBOL = "NIFTY"
EXPIRY = "2026-10-06"
STEP = 50.0
LOT = 65
LOTS = 5
STATE_PATH = "/opt/parallax/probe_state.json"

#: Entry on Monday 5 October 2026 at 09:30 IST, settle at the 6 October expiry.
ENTRY_DATE = (2026, 10, 5)
ENTRY_HHMM = (9, 30)
SETTLE_DATE = (2026, 10, 6)
SETTLE_HHMM = (15, 32)

#: The screen position, in whole strikes below the screen ATM of 22400.
DOC_OFFSETS = {"call_short": -17, "call_long": -8, "put_long": -17, "put_short": -12}

#: The screen position, as absolute strikes.
FIXED_STRIKES = {"call_short": 21550.0, "call_long": 22000.0,
                 "put_long": 21550.0, "put_short": 21800.0}


#: The token this run is using, once refresh_auth has proved it works.
_TOKEN = None


def refresh_auth(say=None):
    """Mint or adopt a token Dhan actually accepts.

    Plain resolve_token is not enough.  A token is revoked the moment a newer
    one is minted, but it still parses with a future exp, so every local check
    passes and /optionchain then returns None for the whole session - a silent
    no-trade, which is how 2026-10-01 was lost.  daily_refresh probes
    /fundlimit and re-mints on rejection, the only test that catches it.
    """
    global _TOKEN
    try:
        tok, src = daily_refresh(env("DHAN_CLIENT_ID"), env("DHAN_PIN"),
                                 env("DHAN_TOTP_SECRET"), notify=print)
        _TOKEN = tok
        if say:
            say("PROBE token: " + str(src))
    except Exception as exc:
        if say:
            say("PROBE token refresh failed: %s" % exc)
    return _TOKEN


def legs(strikes: dict) -> list:
    """(action, strike, option_type) in hedge-first order."""
    return [("S", float(strikes["call_short"]), "CE"),
            ("B", float(strikes["call_long"]), "CE"),
            ("B", float(strikes["put_long"]), "PE"),
            ("S", float(strikes["put_short"]), "PE")]


def intrinsic(settlement: float, book: list) -> float:
    """Expiry value of the book, per unit, credit excluded."""
    total = 0.0
    for action, strike, otype in book:
        if otype == "CE":
            value = max(0.0, settlement - strike)
        else:
            value = max(0.0, strike - settlement)
        total += value if action == "B" else -value
    return total


def payoff_bounds(book: list, credit: float, lo: float = 15000.0, hi: float = 30000.0) -> dict:
    """Worst and best expiry P&L, found by sampling the whole settlement axis."""
    worst = best = None
    s = lo
    while s <= hi:
        v = credit + intrinsic(s, book)
        if worst is None or v < worst[0]:
            worst = (v, s)
        if best is None or v > best[0]:
            best = (v, s)
        s += STEP / 2.0
    return {"floor": worst[0], "floor_at": worst[1], "ceiling": best[0], "ceiling_at": best[1]}


#: An ATM straddle costs 0.7979 sigma (2*phi(0)*sigma*sqrt(T)), so inverting it
#: gives one standard deviation of expected move without needing an IV.
ATM_STRADDLE_RATIO = 0.79788

#: The screen's legs, re-expressed as multiples of one sigma.  Derived from the
#: screen itself: spot 22421.95, straddle 260.52, so 1 sigma = 326.5 points and
#: 21550/21800/22000 sit at -2.67 / -1.90 / -1.29 sigma.
DOC_SIGMA = {"call_short": -2.67, "call_long": -1.29,
             "put_long": -2.67, "put_short": -1.90}


def snap(strike: float, step: float = STEP) -> float:
    return round(strike / step) * step


def expected_move(rows: dict, atm: float) -> float:
    """One standard deviation of expected move, from the ATM straddle."""
    ce, pe = quote(rows, atm, "CE"), quote(rows, atm, "PE")
    if not ce or not pe:
        return 0.0
    straddle = ((ce["bid"] + ce["ask"]) / 2.0) + ((pe["bid"] + pe["ask"]) / 2.0)
    return straddle / ATM_STRADDLE_RATIO


def quote(rows: dict, strike: float, otype: str) -> dict | None:
    return rows.get((float(strike), otype))


def credit_on(rows: dict, book: list, basis: str) -> float:
    """Net credit per unit under one price basis.  Raises if a leg is missing."""
    total = 0.0
    for action, strike, otype in book:
        r = quote(rows, strike, otype)
        if not r:
            raise KeyError("no quote for %g %s" % (strike, otype))
        if basis == "ltp":
            px = float(r["ltp"])
        elif basis == "mid":
            px = (float(r["bid"]) + float(r["ask"])) / 2.0
        elif basis == "executable":
            px = float(r["bid"]) if action == "S" else float(r["ask"])
        else:
            raise ValueError(basis)
        total += px if action == "S" else -px
    return total


def describe(rows: dict, strikes: dict, label: str) -> dict:
    book = legs(strikes)
    out = {"label": label, "strikes": {k: float(v) for k, v in strikes.items()}, "legs": []}
    for action, strike, otype in book:
        r = quote(rows, strike, otype)
        out["legs"].append({
            "action": "SELL" if action == "S" else "BUY", "strike": strike, "type": otype,
            "ltp": float(r["ltp"]), "bid": float(r["bid"]), "ask": float(r["ask"]),
            "volume": int(r["volume"]), "oi": int(r["oi"]),
            "executable": float(r["bid"]) if action == "S" else float(r["ask"]),
        })
    for basis in ("ltp", "mid", "executable"):
        c = credit_on(rows, book, basis)
        b = payoff_bounds(book, c)
        out[basis] = {"credit": c, "floor": b["floor"], "ceiling": b["ceiling"],
                      "credit_rupees": c * LOT * LOTS,
                      "floor_rupees": b["floor"] * LOT * LOTS}
    return out


def format_entry(rec: dict) -> str:
    extra = ""
    if rec.get("sigma"):
        extra = "   straddle %.2f  1 sigma %.1f pts (%.2f%%)" % (
            rec.get("straddle", 0.0), rec["sigma"], 100.0 * rec["sigma"] / rec["spot"])
    lines = ["", "PAPER PROBE  " + rec["variant"].upper() + "   " + SYMBOL + " " + EXPIRY,
             "  entry spot %.2f   ATM %.0f   %d lots%s" % (rec["spot"], rec["atm"], LOTS, extra)]
    for leg in rec["legs"]:
        lines.append("   %-4s %-2s %7.0f  screen %8.2f   bid %8.2f  ask %8.2f   vol %d"
                     % (leg["action"], leg["type"], leg["strike"], leg["ltp"],
                        leg["bid"], leg["ask"], leg["volume"]))
    for basis in ("ltp", "mid", "executable"):
        d = rec[basis]
        lines.append("   %-11s credit %8.2f  floor %+9.2f  ceiling %+9.2f  -> floor Rs.%s"
                     % (basis, d["credit"], d["floor"], d["ceiling"],
                        format(int(round(d["floor_rupees"])), ",")))
    gap = rec["ltp"]["floor_rupees"] - rec["executable"]["floor_rupees"]
    lines.append("   the screen overstates the floor by Rs.%s" % format(int(round(gap)), ","))
    return chr(10).join(lines)


def now_ist() -> datetime:
    return datetime.now(IST)


def read_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def write_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
    except Exception:
        pass


def fetch_rows(expiry: str = EXPIRY, tries: int = 20, say=None):
    for i in range(tries):
        chain = fetch_option_chain(SYMBOL, expiry=expiry, token=_TOKEN)
        if chain and chain.get("rows"):
            rows = {}
            for r in chain["rows"]:
                rows[(float(r["strike"]), r["option_type"])] = r
            return chain["spot"], rows
        # An empty chain is the symptom of a revoked token far more often than
        # of a rate limit, and re-minting is the only thing that clears it.
        if i % 3 == 0:
            refresh_auth(say)
        time.sleep(20)
    return None, None


def do_entry(say) -> dict:
    state = read_state()
    if state.get("entries"):
        say("PROBE: already entered at %s - not entering twice" % state.get("entered_at", "?"))
        return state
    refresh_auth(say)
    spot, rows = fetch_rows(say=say)
    if not spot:
        say("PROBE: option chain unavailable, no entry")
        return {}
    atm = round(spot / STEP) * STEP
    restruck = {k: atm + off * STEP for k, off in DOC_OFFSETS.items()}
    # Where the screen's legs actually sat, in units of the market's own expected
    # move.  The screen was 1.3 to 2.7 sigma BELOW spot - not the money at all -
    # and that is invisible when the strikes are copied as round numbers.
    sig = expected_move(rows, atm)
    sigma_strikes = {k: snap(atm + sig_mult * sig) for k, sig_mult in DOC_SIGMA.items()}
    state["straddle"] = sig * ATM_STRADDLE_RATIO
    state["sigma"] = sig
    state["entries"] = []
    for label, strikes in (("fixed", FIXED_STRIKES), ("restruck", restruck),
                           ("sigma", sigma_strikes)):
        try:
            rec = describe(rows, strikes, label)
        except KeyError as exc:
            say("PROBE: %s variant skipped - %s" % (label, exc))
            continue
        rec["variant"] = label
        rec["spot"] = spot
        rec["atm"] = atm
        rec["lots"] = LOTS
        rec["expiry"] = EXPIRY
        rec["sigma"] = sig
        rec["straddle"] = sig * ATM_STRADDLE_RATIO
        rec["entered"] = now_ist().strftime("%Y-%m-%d %H:%M:%S")
        state["entries"].append(rec)
        say(format_entry(rec))
    state["spot"] = spot
    state["entered_at"] = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    write_state(state)
    return state


def do_settle(say) -> dict:
    state = read_state()
    entries = state.get("entries") or []
    if not entries:
        say("PROBE: nothing to settle")
        return state
    refresh_auth(say)
    spot, rows = fetch_rows(say=say)
    if not spot:
        say("PROBE: no settlement price available")
        return state
    say("")
    say("PAPER PROBE SETTLEMENT   %s settles %.2f" % (EXPIRY, spot))
    total = 0.0
    for rec in entries:
        book = [(l["action"][0], l["strike"], l["type"]) for l in rec["legs"]]
        iv = intrinsic(spot, book)
        pnl = (rec["executable"]["credit"] + iv) * LOT * LOTS
        total += pnl
        say("   %-9s credit %7.2f  expiry value %+8.2f  ->  P&L Rs.%s   (screen would say Rs.%s)"
            % (rec["variant"], rec["executable"]["credit"], iv, format(int(round(pnl)), ","),
               format(int(round((rec["ltp"]["credit"] + iv) * LOT * LOTS)), ",")))
    say("   combined PAPER result  Rs.%s" % format(int(round(total)), ","))
    rec_out = dict(state)
    rec_out["settlement"] = spot
    rec_out["paper_pnl"] = total
    rec_out["settled"] = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    write_state(rec_out)
    return rec_out


def wait_until(year, month, day, hh, mm, say) -> None:
    target = datetime(year, month, day, hh, mm, tzinfo=IST)
    while now_ist() < target:
        left = (target - now_ist()).total_seconds()
        if left > 3600:
            say("PROBE: waiting %.1f h for %s" % (left / 3600.0, target.strftime("%a %d %b %H:%M IST")))
            time.sleep(min(left - 3300, 3300))
        else:
            time.sleep(min(left, 30))
    return


def selftest() -> int:
    """The structure's own numbers, from the screen, worked by hand."""
    book = legs(FIXED_STRIKES)
    c = 1181.35 + 8.00 - 472.50 - 4.30
    problems = []
    if abs(c - 712.55) > 0.005:
        problems.append("screen credit %r != 712.55" % c)
    checks = [(21000.0, 462.55), (21550.0, 462.55), (21800.0, 462.55),
              (21900.0, 362.55), (22000.0, 262.55), (24000.0, 262.55)]
    for settlement, want in checks:
        got = c + intrinsic(settlement, book)
        if abs(got - want) > 0.005:
            problems.append("at %g got %r want %r" % (settlement, got, want))
    b = payoff_bounds(book, c)
    if abs(b["floor"] - 262.55) > 0.005:
        problems.append("floor %r != 262.55" % b["floor"])
    if abs(b["ceiling"] - 462.55) > 0.005:
        problems.append("ceiling %r != 462.55 (the screen claims 712.55)" % b["ceiling"])
    for p in problems:
        print("FAIL " + p)
    if not problems:
        print("ok  credit 712.55 | floor 262.55 | ceiling 462.55 (NOT 712.55)")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--once", action="store_true", help="enter now, or settle now if already entered")
    ap.add_argument("--force-settle", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    bot = TelegramBot()
    def say(msg):
        print(msg, flush=True)
        if getattr(bot, "configured", False):
            bot.send(msg)

    if args.once:
        state = read_state()
        if args.force_settle or state.get("entries"):
            do_settle(say)
        else:
            do_entry(say)
        return 0

    say("PROBE armed: paper only, no orders, no broker.")
    wait_until(*ENTRY_DATE, *ENTRY_HHMM, say)
    # If the host was down at 09:30, entering hours later would be a different
    # trade - the whole structure is struck against the opening print.
    if now_ist() <= datetime(ENTRY_DATE[0], ENTRY_DATE[1], ENTRY_DATE[2], 10, 5, tzinfo=IST):
        do_entry(say)
    else:
        say("PROBE: started past the entry window - skipped entry, will still settle")
    wait_until(*SETTLE_DATE, *SETTLE_HHMM, say)
    wait_until(*SETTLE_DATE, *SETTLE_HHMM, say)
    do_settle(say)
    say("PROBE done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
