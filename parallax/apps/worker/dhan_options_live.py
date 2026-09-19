"""0DTE NIFTY iron condor - live trader (TP@50%, SL@2x, IV-rank filter).

Intraday (INTRADAY) options selling on the NIFTY weekly (Tuesday) expiry:
at the open, sell ATM-2 put + ATM+2 call and buy ATM-4 put + ATM+4 call.
Credit uses the REAL bid (shorts) / ask (hedges) from the live option chain.
Skips unless entry IV > realised vol (trailing daily closes from Dhan).
Manages intraday: TP at 50% credit, SL at 2x credit, else exit at the close.
Mode (paper/live) is read from the journal store.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, timezone

from parallax.contracts import InstrumentId, InstrumentType, OrderStatus, Side
from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.market_data.dhan_options import fetch_option_chain
from parallax.adapters.telegram import TelegramBot
from parallax.core.options.contracts import OptionContract

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_SCRIP = "13"
LOT = 65


def _realized_vol(closes):
    if len(closes) < 11:
        return 0.0
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    m = sum(rets) / len(rets)
    v = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(v) * math.sqrt(252)


class ZeroDteCondor:
    def __init__(self, broker=None, lots=8, tp=0.5, sl=2.0, step=50.0,
                 dry_run=True):
        self.broker = broker or DhanBroker(dry_run=dry_run)
        self.lots = lots
        self.tp = tp
        self.sl = sl
        self.step = step
        self.telegram = TelegramBot()
        self.active: dict | None = None
        self.last_value: float | None = None
        self.last_pnl: float = 0.0
        self.feed = None
        from parallax.web.store import JournalStore
        self.journal = JournalStore()

    # ---- data -------------------------------------------------------------
    def daily_closes(self, days: int = 40) -> list:
        """Trailing daily closes from Dhan historical (for the IV-rank filter)."""
        try:
            api = self.broker._api_client()
            to_d = date.today()
            from_d = to_d - timedelta(days=days * 2)
            res = api.historical_daily_data(NIFTY_SCRIP, "IDX_I", "INDEX",
                                            from_d.isoformat(), to_d.isoformat())
            d = (res or {}).get("data") or {}
            return [float(c) for c in (d.get("close") or [])]
        except Exception:
            return []

    # ---- selection --------------------------------------------------------
    def select(self, symbol: str = "NIFTY", expiry=None, force: bool = False) -> dict:
        from parallax.config.schedule import options_active
        if not options_active(datetime.now(IST)):
            return {"reason": "not a 0DTE expiry day"}
        chain = fetch_option_chain(symbol, expiry=expiry)
        if not chain:
            return {"reason": "chain unavailable"}
        spot = chain["spot"]
        atm = round(spot / self.step) * self.step
        rows = {(r["strike"], r["option_type"]): r for r in chain["rows"]}
        spec = {"put_short": (atm - 2 * self.step, "PE"),
                "call_short": (atm + 2 * self.step, "CE"),
                "put_hedge": (atm - 4 * self.step, "PE"),
                "call_hedge": (atm + 4 * self.step, "CE")}
        legs = {}
        for name, (k, ot) in spec.items():
            r = rows.get((k, ot))
            if not r:
                return {"reason": f"missing leg {k} {ot}"}
            legs[name] = {"strike": k, "type": ot, "bid": r["bid"], "ask": r["ask"],
                          "iv": r["iv"], "ltp": r["ltp"],
                          "security_id": r["security_id"]}
        credit = (legs["put_short"]["bid"] + legs["call_short"]["bid"]
                  - legs["put_hedge"]["ask"] - legs["call_hedge"]["ask"])
        iv = statistics.mean([legs["put_short"]["iv"], legs["call_short"]["iv"]])
        closes = self.daily_closes()
        rv = _realized_vol(closes[-21:]) if closes else 0.0
        if not force and rv > 0 and iv <= rv:
            return {"reason": f"IV {iv:.1%} <= realised {rv:.1%} - skip"}
        return {"spot": spot, "atm": atm, "credit": round(credit, 2),
                "iv": round(iv, 4), "realized": round(rv, 4), "legs": legs,
                "reason": "selected"}

    # ---- execution --------------------------------------------------------
    def enter(self, plan: dict) -> list:
        acks = []
        for name, l in plan["legs"].items():
            side = "SELL" if name.endswith("short") else "BUY"
            c = OptionContract(symbol="NIFTY", strike=l["strike"], expiry="",
                               option_type=l["type"], lot_size=LOT,
                               security_id=l["security_id"], trading_symbol="")
            ack = self.broker.place_option_order(c, side, self.lots, "MARKET")
            acks.append((name, ack))
        self.active = {"plan": plan, "entry_time": datetime.now(timezone.utc)}
        self.last_value = plan["credit"]
        self.last_pnl = 0.0
        self._start_feed(plan)
        self.journal.set_position("NIFTY 0DTE", "options", "SELL", self.lots,
                                  plan["credit"], 0.0, 0.0)
        self._say(f"[0DTE] ENTER {self.lots}L condor ATM{plan['atm']:.0f} "
                  f"credit {plan['credit']}pts iv {plan['iv']:.1%} rv {plan['realized']:.1%} "
                  f"[{self.broker.dry_run and 'PAPER' or 'LIVE'}]")
        return acks

    # ---- live feed --------------------------------------------------------
    def _start_feed(self, plan: dict) -> None:
        """Subscribe the 4 legs on the Dhan WebSocket for real-time premiums."""
        try:
            from parallax.adapters.market_data.dhan_feed import DhanMarketFeed
            from parallax.adapters.broker.dhan_auth import resolve_token
            from parallax.adapters.env import env
            cid = env("DHAN_CLIENT_ID")
            tok, _ = resolve_token(cid, env("DHAN_ACCESS_TOKEN"),
                                   env("DHAN_PIN"), env("DHAN_TOTP_SECRET"))
            insts = [("NSE_FNO", str(l["security_id"])) for l in plan["legs"].values()]
            self.feed = DhanMarketFeed(tok, cid, insts)
            self.feed.start()
        except Exception as e:
            self.feed = None
            self._say("[0DTE] WS feed unavailable: " + str(e)[:70])

    def _stop_feed(self) -> None:
        if self.feed is not None:
            try:
                self.feed.stop()
            except Exception:
                pass
            self.feed = None

    # ---- management -------------------------------------------------------
    def value_now(self) -> float | None:
        """Current condor value in points (cost to close).

        Prefers the live WebSocket prices (real-time, no rate limit) and falls
        back to the REST option chain if the feed has no tick yet."""
        if not self.active:
            return None
        plan = self.active["plan"]
        if self.feed is not None:
            ltp = {n: self.feed.ltp(plan["legs"][n]["security_id"])
                   for n in ("put_short", "call_short", "put_hedge", "call_hedge")}
            if all(v is not None for v in ltp.values()):
                val = (-(ltp["put_short"] + ltp["call_short"])
                       + ltp["put_hedge"] + ltp["call_hedge"])
                self.last_value = round(val, 2)
                self.last_pnl = round((plan["credit"] - val) * LOT * self.lots, 2)
                return self.last_value
        chain = fetch_option_chain("NIFTY")
        if not chain:
            return None
        rows = {(r["strike"], r["option_type"]): r for r in chain["rows"]}
        val = 0.0
        for name, sign in (("put_short", -1), ("call_short", -1),
                           ("put_hedge", 1), ("call_hedge", 1)):
            l = plan["legs"][name]
            r = rows.get((l["strike"], l["type"]))
            if not r:
                return None
            px = r["ask"] if sign < 0 else r["bid"]
            val += sign * px
        self.last_value = round(val, 2)
        self.last_pnl = round((plan["credit"] - val) * LOT * self.lots, 2)
        return self.last_value

    def manage(self) -> str:
        val = self.value_now()
        if val is None or not self.active:
            return "hold"
        credit = self.active["plan"]["credit"]
        if (credit - val) >= self.tp * credit:
            return "tp"
        if (credit - val) <= -self.sl * credit:
            return "sl"
        return "hold"

    def close(self, reason: str) -> dict:
        if not self.active:
            return {}
        plan = self.active["plan"]
        for name, l in plan["legs"].items():
            side = "BUY" if name.endswith("short") else "SELL"
            c = OptionContract(symbol="NIFTY", strike=l["strike"], expiry="",
                               option_type=l["type"], lot_size=LOT,
                               security_id=l["security_id"], trading_symbol="")
            self.broker.place_option_order(c, side, self.lots, "MARKET")
        pnl = self.last_pnl if self.last_pnl else plan["credit"] * LOT * self.lots
        if reason == "tp":
            pnl = self.tp * plan["credit"] * LOT * self.lots
        elif reason == "sl":
            pnl = -self.sl * plan["credit"] * LOT * self.lots
        self.journal.record_trade("options", "NIFTY 0DTE", "SELL", self.lots,
                                  plan["credit"], round(self.last_value or 0, 2),
                                  round(pnl, 2), "WIN" if pnl > 0 else "LOSS",
                                  f"condor {reason}")
        self.journal.clear_positions()
        self._stop_feed()
        self._say(f"[0DTE] CLOSE {reason} pnl Rs{pnl:,.0f}")
        self.active = None
        return {"reason": reason, "pnl": round(pnl, 2)}

    def _say(self, msg: str) -> None:
        print(msg)
        if self.telegram.configured:
            self.telegram.send(msg)
