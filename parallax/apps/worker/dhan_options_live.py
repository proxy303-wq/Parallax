"""0DTE NIFTY iron condor - live trader (ratchet TP, SL@2x, IV-rank filter).

Intraday (INTRADAY) options selling on the NIFTY weekly (Tuesday) expiry:
at the open, sell ATM-2 put + ATM+2 call and buy ATM-4 put + ATM+4 call.
Credit uses the REAL bid (shorts) / ask (hedges) from the live option chain.
Skips unless entry IV > realised vol (trailing daily closes from Dhan).
Manages intraday with a RATCHET take-profit: once the position has been up 50%
the floor locks at 50% of credit, at 80% it locks 75%, at 95% it locks 90%.
A fixed take-profit (tp_mode="fixed") is still supported.
Stop is SL x credit; anything still open exits at the session close.
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
                 dry_run=True, tp_mode="ratchet", hold_to_expiry=False,
                 instrument_name="NIFTY 0DTE"):
        self.broker = broker or DhanBroker(dry_run=dry_run)
        self.lots = lots
        self.tp = tp
        self.sl = sl
        self.step = step
        self.tp_mode = tp_mode          # "ratchet" | "fixed"
        self.hold_to_expiry = hold_to_expiry
        self.instrument_name = instrument_name
        self.peak_pct = 0.0             # best profit (% of credit) seen this trade
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
        if not force:
            # force is the explicit override: a positional condor may be opened
            # on any day, not only on a scheduled 0DTE expiry
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
                "expiry": chain.get("expiry") or "", "reason": "selected"}

    # ---- execution --------------------------------------------------------
    #: Buy the hedges before selling the shorts.
    #:
    #: Order matters more than anything else here.  Dhan margins each leg as it
    #: arrives, so a short placed before its hedge is NAKED - 8 lots of naked
    #: ATM-2 NIFTY put prices at ~Rs 13.1 lakh (queried), and two of them at
    #: ~Rs 26.2 lakh.  An Rs 8L account cannot post that, so short-first entry
    #: is simply rejected.  With the long legs already in the account SPAN nets
    #: the spread and the identical condor needs only its defined-risk ceiling,
    #: ~Rs 32-44k.  Hedges-first is also the safer failure mode: if the shorts
    #: then fail you are left long two cheap options with a bounded debit,
    #: instead of short two naked ones.
    LEG_ORDER = ("put_hedge", "call_hedge", "put_short", "call_short")

    @staticmethod
    def _leg_ok(ack) -> bool:
        return str(getattr(ack, "status", "")).split(".")[-1].upper() not in (
            "REJECTED", "CANCELLED", "EXPIRED")

    def _contract(self, leg):
        return OptionContract(symbol="NIFTY", strike=leg["strike"], expiry="",
                              option_type=leg["type"], lot_size=LOT,
                              security_id=leg["security_id"], trading_symbol="")

    def _unwind(self, placed) -> None:
        """Reverse the legs already sent, newest first."""
        for c, side in reversed(placed):
            try:
                self.broker.place_option_order(
                    c, "SELL" if side == "BUY" else "BUY", self.lots, "MARKET")
            except Exception as e:
                self._say("[0DTE] UNWIND FAILED " + str(e)[:60])

    def _available_margin(self) -> float:
        try:
            return float(getattr(self.broker.get_account(), "available", 0.0) or 0.0)
        except Exception:
            return 0.0

    def enter(self, plan: dict) -> list:
        legs = plan["legs"]
        avail = self._available_margin()
        # The Dhan API blocks margin per leg, so the *order* is what earns the
        # hedge benefit: the long legs must already be in the account before the
        # shorts are sent.  Report the balance so a margin rejection is obvious.
        self._say(f"[0DTE] entry {self.lots}L condor, available margin "
                  f"Rs{avail:,.0f}")
        order = [n for n in self.LEG_ORDER if n in legs]
        order += [n for n in legs if n not in order]
        acks = []
        placed = []
        for name in order:
            l = legs[name]
            side = "SELL" if name.endswith("short") else "BUY"
            c = self._contract(l)
            ack = self.broker.place_option_order(c, side, self.lots, "MARKET")
            acks.append((name, ack))
            if not self._leg_ok(ack):
                # We cannot place a basket through the Dhan API - each leg is a
                # separate order and margin is blocked per leg (Dhan feature
                # request, MadeForTrade #59802).  So a rejection mid-entry must
                # not leave a half-open position; unwind what did go through
                # and say so loudly instead of looking like "no trade today".
                self._unwind(placed)
                self._say(f"[0DTE] ENTER FAILED {name} [{getattr(ack, 'status', '?')}] "
                          f"{str(getattr(ack, 'message', ''))[:70]} - unwound "
                          f"{len(placed)} leg(s)")
                return acks
            placed.append((c, side))
        self.active = {"plan": plan, "entry_time": datetime.now(timezone.utc)}
        self.last_value = plan["credit"]
        self.last_pnl = 0.0
        self.peak_pct = 0.0
        self._start_feed(plan)
        self.journal.set_position(self.instrument_name,
                                  "options-hold" if self.hold_to_expiry else "options",
                                  "SELL", self.lots, plan["credit"], 0.0, 0.0)
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
                # COST TO CLOSE = shorts - hedges.  Buy the shorts back, sell
                # the hedges.  This was negated, which made a freshly opened
                # condor read as ~-credit and therefore as +200% profit, and
                # booked a phantom ~2x the credit into the journal on every
                # trade.  Same sign error that was found in the backtest study.
                val = (ltp["put_short"] + ltp["call_short"]
                       - ltp["put_hedge"] - ltp["call_hedge"])
                self.last_value = round(val, 2)
                self.last_pnl = round((plan["credit"] - val) * LOT * self.lots, 2)
                return self.last_value
        chain = fetch_option_chain("NIFTY")
        if not chain:
            return None
        rows = {(r["strike"], r["option_type"]): r for r in chain["rows"]}
        val = 0.0
        for name, sign in (("put_short", 1), ("call_short", 1),
                           ("put_hedge", -1), ("call_hedge", -1)):
            l = plan["legs"][name]
            r = rows.get((l["strike"], l["type"]))
            if not r:
                return None
            # buy the short back at the ask, sell the hedge at the bid
            px = r["ask"] if sign > 0 else r["bid"]
            val += sign * px
        self.last_value = round(val, 2)
        self.last_pnl = round((plan["credit"] - val) * LOT * self.lots, 2)
        return self.last_value

    @staticmethod
    def ratchet_floor(peak: float) -> float:
        """Locked-in profit floor (% of credit) given the best profit seen."""
        if peak >= 0.95:
            return 0.90
        if peak >= 0.80:
            return 0.75
        if peak >= 0.50:
            return 0.50
        return 0.0

    def expiry_date(self):
        """The contract expiry as a date, or None."""
        exp = (self.active or {}).get("plan", {}).get("expiry") or ""
        try:
            return datetime.strptime(str(exp)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def expiry_reached(self, now) -> bool:
        """True once the contract has stopped trading.

        On expiry day the exit is the 15:15 IST session close; on any later day
        the position is overdue and is flattened immediately.
        """
        d = self.expiry_date()
        if d is None:
            return False
        return now.date() > d or (now.date() == d and (now.hour, now.minute) >= (15, 15))

    def manage(self) -> str:
        val = self.value_now()
        if val is None or not self.active:
            return "hold"
        if self.hold_to_expiry:
            # A hedged condor's loss is bounded by the wing width, so there is
            # no stop to defend: carry the position to the expiry close and
            # collect the whole credit.  No ratchet, no SL.
            return "hold"
        credit = self.active["plan"]["credit"]
        prof = (credit - val) / credit if credit else 0.0
        self.peak_pct = max(self.peak_pct, prof)
        if prof <= -self.sl:
            return "sl"
        if self.tp_mode == "ratchet":
            floor = self.ratchet_floor(self.peak_pct)
            if floor > 0 and prof <= floor:
                return "ratchet"
            return "hold"
        if prof >= self.tp:
            return "tp"
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
        elif reason == "ratchet":
            pnl = self.ratchet_floor(self.peak_pct) * plan["credit"] * LOT * self.lots
        elif reason == "sl":
            pnl = -self.sl * plan["credit"] * LOT * self.lots
        self.journal.record_trade("options", self.instrument_name, "SELL", self.lots,
                                  plan["credit"], round(self.last_value or 0, 2),
                                  round(pnl, 2), "WIN" if pnl > 0 else "LOSS",
                                  f"condor {reason}")
        # clear only OUR row: clear_positions() is a global DELETE, which would
        # erase the crypto workers' open positions from the dashboard
        self.journal.clear_position(self.instrument_name)
        self._stop_feed()
        self._say(f"[0DTE] CLOSE {reason} pnl Rs{pnl:,.0f}")
        self.active = None
        return {"reason": reason, "pnl": round(pnl, 2)}

    def _say(self, msg: str) -> None:
        print(msg)
        if self.telegram.configured:
            self.telegram.send(msg)
