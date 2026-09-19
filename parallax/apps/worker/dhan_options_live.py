"""0DTE NIFTY iron condor — live paper trader (TP@50%, SL@2x, IV-rank filter).

Intraday (INTRADAY) options selling on the NIFTY weekly (Tuesday) expiry:
at the open, sell ATM-2 put + ATM+2 call, buy ATM-4 put + ATM+4 call (3 lots).
Credit uses the REAL bid (shorts) / ask (hedges) from the live option chain.
Skips the trade unless entry IV > realised vol.  Manages intraday: take profit
at 50% credit, stop at 2x credit, else hold to the close.  Dry-run by default.
"""
from __future__ import annotations

import math
import statistics
import time as _t
from datetime import datetime, timezone, timedelta

from parallax.contracts import InstrumentId, InstrumentType, OrderStatus, Side
from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.market_data.dhan_options import fetch_option_chain, fetch_expiries
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.adapters.telegram import TelegramBot
from parallax.core.options import optmath as om

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"


def _realized_vol(closes):
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(rets) < 10:
        return 0.0
    m = sum(rets) / len(rets)
    v = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(v) * math.sqrt(252)


class ZeroDteCondorPaper:
    def __init__(self, broker=None, lots=8, tp=0.5, sl=2.0, step=50.0,
                 dry_run=True):
        self.broker = broker or DhanBroker(dry_run=dry_run)
        self.lots = lots
        self.tp = tp
        self.sl = sl
        self.step = step
        self.telegram = TelegramBot()
        self.active: dict | None = None

    # ---- entry selection -------------------------------------------------
    def select(self, symbol="NIFTY", expiry=None, force=False) -> dict | None:
        """Fetch the live chain and pick the ATM-2/+2 wings + ATM-4/+4 hedges.
        Returns {strikes, credit, iv, realized, legs, reason} or None."""
        from parallax.config.schedule import options_active
        if not options_active(datetime.now(timezone.utc)):
            return {"reason": "not a 0DTE expiry day"}
        chain = fetch_option_chain(symbol, expiry=expiry)
        if not chain:
            return {"reason": "chain unavailable"}
        spot = chain["spot"]
        atm = round(spot / self.step) * self.step
        rows = {(r["strike"], r["option_type"]): r for r in chain["rows"]}
        wings = {
            ("put_short", (atm - 2 * self.step, "PE")),
            ("call_short", (atm + 2 * self.step, "CE")),
            ("put_hedge", (atm - 4 * self.step, "PE")),
            ("call_hedge", (atm + 4 * self.step, "CE")),
        }
        legs = {}
        for name, (k, ot) in wings:
            r = rows.get((k, ot))
            if not r:
                return {"reason": f"missing leg {k} {ot}"}
            legs[name] = {"strike": k, "type": ot, "bid": r["bid"],
                          "ask": r["ask"], "iv": r["iv"], "ltp": r["ltp"],
                          "security_id": r["security_id"]}
        # credit: shorts fill at bid, hedges fill at ask
        credit = (legs["put_short"]["bid"] + legs["call_short"]["bid"]
                  - legs["put_hedge"]["ask"] - legs["call_hedge"]["ask"])
        iv = statistics.mean([legs["put_short"]["iv"], legs["call_short"]["iv"]])
        # realised vol from DAILY closes (trailing ~20 sessions)
        from collections import OrderedDict
        bars = load_ohlcv(NIFTY_CSV, "5m")
        dmap = OrderedDict()
        for b in bars:
            dmap[b.ts.date()] = b.close
        rv = _realized_vol(list(dmap.values())[-21:])
        if not force and rv > 0 and iv <= rv:
            return {"reason": f"IV {iv:.1%} <= realised {rv:.1%} - skip"}
        return {"spot": spot, "atm": atm, "credit": round(credit, 2),
                "iv": round(iv, 4), "realized": round(rv, 4), "legs": legs,
                "reason": "selected"}

    # ---- execution -------------------------------------------------------
    def enter(self, plan: dict) -> None:
        legs = plan["legs"]
        order_side = {"put_short": ("SELL", legs["put_short"]),
                      "call_short": ("SELL", legs["call_short"]),
                      "put_hedge": ("BUY", legs["put_hedge"]),
                      "call_hedge": ("BUY", legs["call_hedge"])}
        acks = []
        for name, (side, leg) in order_side.items():
            from parallax.core.options.contracts import OptionContract
            c = OptionContract(symbol="NIFTY", strike=leg["strike"],
                               expiry="", option_type=leg["type"],
                               lot_size=65, security_id=leg["security_id"],
                               trading_symbol="")
            ack = self.broker.place_option_order(c, side, self.lots, "MARKET")
            acks.append((name, ack))
        self.active = {"plan": plan, "entry_time": datetime.now(timezone.utc),
                       "entered": True}
        msg = (f"[NIFTY 0DTE] ENTER 3L condor ATM{plan['atm']:.0f} "
               f"credit {plan['credit']}pts iv {plan['iv']:.1%}")
        self._say(msg)
        return acks

    # ---- management ------------------------------------------------------
    def manage(self) -> str | None:
        """Fetch the live chain, mark the condor, apply TP/SL.  Returns the
        action taken (tp/sl/hold) or None."""
        if not self.active:
            return None
        plan = self.active["plan"]
        chain = fetch_option_chain("NIFTY")
        if not chain:
            return None
        rows = {(r["strike"], r["option_type"]): r for r in chain["rows"]}
        legs = plan["legs"]
        # current value: shorts at ask (buy back), hedges at bid (sell)
        val = 0.0
        for name, sign in (("put_short", -1), ("call_short", -1),
                           ("put_hedge", 1), ("call_hedge", 1)):
            l = legs[name]
            r = rows.get((l["strike"], l["type"]))
            if not r:
                return None
            px = r["ask"] if sign < 0 else r["bid"]   # buy back short / sell hedge
            val += sign * px
        credit = plan["credit"]
        pnl = credit - val
        if pnl >= self.tp * credit:
            return "tp"
        if pnl <= -self.sl * credit:
            return "sl"
        return "hold"

    def close(self, reason: str) -> dict:
        """Square off (dry-run) and report."""
        if not self.active:
            return {}
        plan = self.active["plan"]
        legs = plan["legs"]
        side = {"put_short": "BUY", "call_short": "BUY",
                "put_hedge": "SELL", "call_hedge": "SELL"}
        from parallax.core.options.contracts import OptionContract
        for name, l in legs.items():
            c = OptionContract(symbol="NIFTY", strike=l["strike"], expiry="",
                               option_type=l["type"], lot_size=65,
                               security_id=l["security_id"], trading_symbol="")
            self.broker.place_option_order(c, side[name], self.lots, "MARKET")
        pnl = plan["credit"] * 65 * self.lots  # placeholder; real P&L from fills
        self._say(f"[NIFTY 0DTE] CLOSE ({reason})")
        self.active = None
        return {"reason": reason}

    def _say(self, msg: str) -> None:
        print(msg)
        if self.telegram.configured:
            self.telegram.send(msg)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore IV filter")
    a = p.parse_args()
    t = ZeroDteCondorPaper()
    plan = t.select(force=a.force)
    print("SELECT:", plan.get("reason"), plan.get("spot", ""),
          plan.get("atm", ""), "credit", plan.get("credit", ""),
          "iv", f"{plan.get('iv', 0):.1%}" if plan.get("iv") else "",
          "rv", f"{plan.get('realized', 0):.1%}" if plan.get("realized") else "")
    if plan.get("legs") and a.once:
        acks = t.enter(plan)
        for name, ack in acks:
            print(f"  {name}: {ack.status.value} {ack.message[:60]}")
