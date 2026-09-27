"""Morning Telegram report: how far each index is expected to move today.

Sent at 09:20 IST -- the minute the calibration table is keyed to -- and so ten
minutes before the worker enters at 09:30.

TWO RULERS, ON PURPOSE.  Every index gets the ATM straddle, which is the market's
own expected move and needs no calibration at all.  Where the empirical table
covers the index, the measured percentile bands are added.

    bands      47% of sessions inside p25-p75, 80% inside p10-p90   <- held up
    p50 point  mean abs error 62.8 pts against a naive 53.8        <- lost
    win rate   NIFTY -8.7, SENSEX +11.4, opposite signs            <- noise

So the p50 is printed as context and the 80% band as the actual statement, and
no win rate appears anywhere: that number did not survive out of sample.

BANKNIFTY and BANKEX have no table (monthly expiries give too few samples, and
BANKEX serves bad prints at the strikes we trade).  They still get the straddle,
and since BANKNIFTY is what trades on a Tuesday that is the case that matters --
which is why the straddle line is never suppressed.

Usage:
    python -m parallax.apps.ops.morning_report          # build and send
    python -m parallax.apps.ops.morning_report --dry    # print only
"""
from __future__ import annotations

import datetime
import sys
import time

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
INDICES = ("NIFTY", "SENSEX", "BANKNIFTY", "BANKEX")
#: the option-chain endpoint rate-limits hard and answers 401 when it does, so
#: the four calls are spread rather than fired together
PAUSE_S = 20.0
UNIT_DIR = "/etc/systemd/system"
#: blank line between indices, for scannability on a phone
OUT_GAP = True


def _num(x) -> str:
    return format(int(round(float(x))), ",")


def _live_config() -> dict:
    """lots / short_off actually running, read out of the unit files themselves.

    Copying them in here would drift the first time a unit is edited, and the
    report would then describe a book that is not the one trading.  Absent or
    unreadable units simply drop the line.
    """
    import re
    out = {}
    for name in ("nifty", "sensex", "banknifty", "bankex"):
        try:
            with open("%s/parallax-opt-%s.service" % (UNIT_DIR, name)) as fh:
                m = re.search(r"--index\s+(\w+).*?--lots\s+(\d+).*?--short-off\s+(\d+)",
                              fh.read(), re.S)
        except OSError:
            continue
        if m:
            out[m.group(1).upper()] = {"lots": int(m.group(2)),
                                       "short_off": int(m.group(3))}
    return out


def build_report(today: datetime.date | None = None, pause: float = PAUSE_S,
                 sleep=time.sleep, forecast_fn=None, live: dict | None = None) -> str:
    from parallax.config.schedule import options_plan
    if forecast_fn is None:
        from parallax.adapters.market_data.range_indicator import forecast as forecast_fn
    if live is None:
        live = _live_config()
    today = today or datetime.datetime.now(IST).date()
    plan = options_plan(today)
    trading = str(plan[0]).upper() if plan else None

    out = ["PARALLAX range report  " + today.strftime("%a %d %b")]
    out.append("today: %s 0DTE - enter 09:30, settle 15:30" % plan[0] if plan
               else "today: no options (futures day)")
    out.append("")

    for i, sym in enumerate(INDICES):
        if i and pause:
            sleep(pause)
        try:
            f = forecast_fn(sym)
        except Exception as e:
            out.append("%-9s  chain error: %s" % (sym, str(e)[:40]))
            continue
        if not f:
            out.append("%-9s  no chain available" % sym)
            continue

        tag = "   <== trades today" if sym == trading else ""
        straddle = float(f["straddle"])
        move_p50 = None
        if f.get("calibrated"):
            rb, mb = f["range_bands"], f["move_bands"]
            move_p50 = mb[50]
            out.append("%-9s  spot %s   straddle %s   %d dte%s" % (
                sym, _num(f["spot"]), _num(straddle), f["dte"], tag))
            out.append("           range     p50 %6s   80%% %6s - %-6s" % (
                _num(rb[50]), _num(rb[10]), _num(rb[90])))
            out.append("           net move  p50 %6s   80%% %6s - %-6s" % (
                _num(mb[50]), _num(mb[10]), _num(mb[90])))
        else:
            out.append("%-9s  spot %s   straddle %s   no table (monthly expiries)%s"
                       % (sym, _num(f["spot"]), _num(straddle), tag))

        cfg = live.get(sym)
        if cfg and sym == trading:
            sp = cfg["short_off"] * f["step"]
            ref = ("p50 move %s" % _num(move_p50)) if move_p50 else                   ("straddle %s" % _num(straddle))
            out.append("           book      %d lots, shorts %d strikes out = %s pts"
                       "   vs %s" % (cfg["lots"], cfg["short_off"], _num(sp), ref))
        if i + 1 < len(INDICES) and OUT_GAP:
            out.append("")
    return "\n".join(out)


def main() -> None:
    msg = build_report()
    print(msg, flush=True)
    if "--dry" in sys.argv:
        return
    try:
        from parallax.adapters.telegram import TelegramBot
        tb = TelegramBot()
        if tb.configured:
            tb.send(msg)
            print("[report] sent to telegram", flush=True)
        else:
            print("[report] telegram not configured - printed only", flush=True)
    except Exception as e:
        print("[report] telegram failed: %s %s" % (type(e).__name__, str(e)[:80]),
              flush=True)


if __name__ == "__main__":
    main()
