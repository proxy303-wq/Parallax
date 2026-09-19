"""PARALLAX live runner - NIFTY futures + 0DTE options workers.

Polls Dhan for completed 5-minute bars every few seconds and applies the
fast-execution gates:
  1. NEW-BAR ONLY  - act only when a genuinely new completed bar appears
  2. FRESHNESS     - reject a bar that is older than max_bar_age seconds
  3. ENTRY VALIDITY- re-check the live price before ordering; skip if price
                     has crossed the signal entry (no chasing)
Routing: futures on non-expiry days, options on the 0DTE expiry day
(session scheduler).  Mode (paper/live) is read from the journal store.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from parallax.contracts import (
    InstrumentId, InstrumentType, OrderIntent, OrderType, Side,
    ValidatedOrderIntent,
)
from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.telegram import TelegramBot
from parallax.config.schedule import futures_active, options_active
from parallax.core.context import build_context
from parallax.core.execution import ExitConfig, ExitManager
from parallax.core.ict import ICTConfig, detect
from parallax.core.perception import bars_per_year, build_market_state
from parallax.core.perception import indicators as ind
from parallax.web.store import JournalStore

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_SCrip = "13"


class LiveRunner:
    def __init__(self, poll_seconds: int = 3, max_bar_age: int = 400,
                 lots_futures: int = 3, lots_options: int = 8):
        self.poll = poll_seconds
        self.max_bar_age = max_bar_age
        self.lots_futures = lots_futures
        self.lots_options = lots_options
        self.store = JournalStore()
        self.telegram = TelegramBot()
        self.ict = ICTConfig()
        self.exit_cfg = ExitConfig()
        self.instrument = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE, exchange="NSE")
        self.bars: list = []
        self.active: dict | None = None
        self.last_bar_ts = None
        self.options_trader = None
        self.entered_on = None
        self.armed_on = None
        self.eod_on = None
        self._broker_cache = None
        self._broker_mode = None
        self._last_token_check = 0.0
        self.refreshed_on = None
        self.gates = {"skipped_stale": 0, "skipped_crossed": 0, "trades": 0}

    # ---- mode / broker ---------------------------------------------------
    def _broker(self) -> DhanBroker:
        """Cached broker (one client per mode).  Rebuilt on a mode change or
        after a token refresh so the SDK always holds a valid token."""
        live = self.store.mode() == "live"
        if self._broker_cache is None or self._broker_mode != live:
            self._broker_cache = DhanBroker(dry_run=not live)
            self._broker_mode = live
        return self._broker_cache

    def _snapshot_capital(self) -> float:
        """PAPER -> the fixed paper capital (Rs8L by default); LIVE -> the real
        Dhan wallet balance.  Written to the store so the dashboard agrees."""
        mode = self.store.mode()
        if mode != "live":
            cap = self.store.paper_capital()
            self.store.snapshot_capital(cap, cap, 0.0)
            return cap
        try:
            acct = self._broker().get_account()
            self.store.snapshot_capital(acct.equity, acct.cash, acct.margin_used)
            return acct.equity
        except Exception:
            return 0.0

    def _daily_refresh(self, now) -> str:
        """08:00 token refresh (daily_refresh: RenewToken -> TOTP)."""
        from parallax.adapters.broker.dhan_auth import daily_refresh, token_status
        from parallax.adapters.env import env
        tok, src = daily_refresh(env("DHAN_CLIENT_ID"), env("DHAN_PIN"),
                                 env("DHAN_TOTP_SECRET"))
        self._broker_cache = None
        st = token_status()
        return (f"[TOKEN] 08:00 refresh: {src} -> {st.get('type') or '?'} "
                f"{st.get('hours_left')}h")

    def _ensure_token(self, force: bool = False) -> dict:
        """Proactively refresh the Dhan token before it lapses.

        Fires when the token has <12h left (or when forced after a failed
        validation), so the TOTP path - which invalidates the previous token -
        is not called needlessly."""
        from parallax.adapters.broker.dhan_auth import refresh_token, token_status
        from parallax.adapters.env import env
        st = token_status()
        if not force and st.get("hours_left", 99) >= 12:
            return st
        cid = env("DHAN_CLIENT_ID")
        tok, src = refresh_token(cid, env("DHAN_PIN"), env("DHAN_TOTP_SECRET"))
        self._broker_cache = None
        self._say("[TOKEN] " + str(src) + " (was " + str(st.get("hours_left")) + "h left)")
        return token_status()

    # ---- data ------------------------------------------------------------
    def _fetch_bars(self, days_back: int = 8):
        """Completed 5-min NIFTY bars over the last few trading days.

        Pulls a multi-day range (Dhan returns the recent sessions) so the
        indicators - SMA20/SMA50, ATR(14) and the swing/liquidity structure -
        are fully warmed from the FIRST bar of the session.  Without this the
        morning would cold-start on 1-2 bars and produce garbage signals."""
        b = self._broker()
        api = b._api_client()
        now = datetime.now(IST)
        frm = (now.date() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to = now.strftime("%Y-%m-%d")
        res = api.intraday_minute_data(NIFTY_SCrip, "IDX_I", "INDEX",
                                       frm + " 09:15:00", to + " 15:30:00", "5")
        data = (res or {}).get("data") or {}
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        vols = data.get("volume") or []
        ts = data.get("timestamp") or []
        from parallax.contracts import Bar
        out = []
        for i in range(min(len(opens), len(ts))):
            out.append(Bar(ts=datetime.fromtimestamp(float(ts[i]), tz=IST),
                           open=float(opens[i]), high=float(highs[i]),
                           low=float(lows[i]), close=float(closes[i]),
                           volume=float(vols[i]) if i < len(vols) else 0.0))
        return out

    # ---- gates -----------------------------------------------------------
    def _fresh(self, bar) -> bool:
        age = (datetime.now(IST) - bar.ts).total_seconds()
        return age <= self.max_bar_age

    def _price_ok(self, side: Side, entry: float) -> bool:
        """Entry-validity gate: do not chase a price that already crossed."""
        b = self._broker()
        try:
            q = b.get_quote(str(self.instrument))
            px = q.last or q.bid or q.ask
        except Exception:
            return True
        if not px:
            return True
        if side == Side.BUY:
            return px <= entry * 1.002      # allow 0.2% tolerance
        return px >= entry * 0.998

    # ---- daily briefings -------------------------------------------------
    def _armed_message(self, now) -> str:
        today = now.date()
        mode = self.store.mode().upper()
        if futures_active(today):
            engine = "FUTURES - NIFTY ICT scalper"
            detail = f"{self.lots_futures} lots, intraday"
            note = "0DTE engine is silent today (not an expiry day)."
        elif options_active(today):
            engine = "OPTIONS - NIFTY 0DTE hedged short strangle"
            detail = f"{self.lots_options} lots, TP 50% / SL 2x"
            note = "Futures engine is silent today (0DTE expiry day)."
        else:
            engine = "NONE"
            detail = "-"
            note = "No engine scheduled."
        equity = self._snapshot_capital()
        cap_src = "Dhan wallet" if self.store.mode_is_live() else "paper capital"
        try:
            st = self._ensure_token()
            tk = "Token " + str(st.get("type") or "?") + " " + str(st.get("hours_left")) + "h"
        except Exception:
            tk = "Token unknown"
        return ("PARALLAX armed [" + mode + "]" + chr(10)
                + today.strftime("%a %d %b") + " - " + engine + chr(10)
                + detail + chr(10) + note + chr(10)
                + f"Equity Rs{equity:,.0f} (" + cap_src + ")" + chr(10) + tk)

    def _eod_message(self, now) -> str:
        s = self.store.summary()
        g = self.gates
        return ("PARALLAX session close" + chr(10)
                + now.strftime("%a %d %b") + chr(10)
                + f"Trades today: {g['trades']}" + chr(10)
                + f"Total P&L Rs{s['total_pnl']:+,.0f} ({s['n_trades']} trades)" + chr(10)
                + f"Gates: stale {g['skipped_stale']}, crossed {g['skipped_crossed']}")

    # ---- main loop -------------------------------------------------------
    def run(self, max_seconds: int | None = None) -> dict:
        t0 = time.time()
        self._say(f"PARALLAX live runner online [{self.store.mode().upper()}]")
        # startup: validate the Dhan token and refresh if it is no longer accepted
        try:
            self._broker().get_account()
        except Exception as e:
            self._say("[TOKEN] validation failed - refreshing")
            try:
                self._ensure_token(force=True)
            except Exception as e2:
                self._say("[TOKEN] refresh error: " + str(e2)[:80])
        while True:
            try:
                now = datetime.now(IST)
                today = now.date()
                # 08:00 daily token refresh (before the session)
                if now.hour == 8 and self.refreshed_on != today:
                    self.refreshed_on = today
                    try:
                        self._say(self._daily_refresh(now))
                    except Exception as e:
                        self._say("[TOKEN] 08:00 refresh error: " + str(e)[:90])
                # periodic token health: refresh well before expiry, any hour
                if time.time() - self._last_token_check > 1200:
                    self._last_token_check = time.time()
                    try:
                        self._ensure_token()
                    except Exception as e:
                        print("token check error:", type(e).__name__, str(e)[:100])
                if today.weekday() < 5:
                    if now.hour == 9 and 15 <= now.minute <= 25 and self.armed_on != today:
                        self._say(self._armed_message(now))
                        self.armed_on = today
                    if now.hour == 15 and now.minute >= 20 and self.eod_on != today:
                        self._say(self._eod_message(now))
                        self.eod_on = today
                if today.weekday() < 5 and now.hour >= 9 and now.hour < 16:
                    bars = self._fetch_bars()
                    if bars:
                        newest = bars[-1]
                        if newest.ts != self.last_bar_ts:
                            self.last_bar_ts = newest.ts
                            if self._fresh(newest):
                                self.bars = bars[-800:]
                                if len(self.bars) < 60:
                                    self._say("[WARMUP] only " + str(len(self.bars))
                                              + " bars - indicators not warm, skipping")
                                    self.last_bar_ts = newest.ts
                                    continue
                                if futures_active(today):
                                    self._futures_tick(newest)
                                elif options_active(today):
                                    self._options_tick(newest)
                            else:
                                self.gates["skipped_stale"] += 1
            except Exception as e:
                print("runner error:", type(e).__name__, str(e)[:140])
            if max_seconds and time.time() - t0 > max_seconds:
                break
            time.sleep(self.poll)
        return self.gates

    def _futures_tick(self, bar) -> None:
        closes = [b.close for b in self.bars]
        highs = [b.high for b in self.bars]
        lows = [b.low for b in self.bars]
        vols = [b.volume for b in self.bars]
        series = ind.precompute(closes, highs, lows, vols, bars_per_year("5m"))
        state = build_market_state(self.instrument, self.bars, "5m",
                                   series=series, now=bar.ts)
        state = build_context({"5m": state}, "5m")
        if self.active is not None:
            px, _ = self.active["em"].update(bar.high, bar.low)
            if px is not None:
                pnl = (px - self.active["entry"]) * (1 if self.active["side"] == Side.BUY else -1) * self.active["qty"] * 65
                self.store.record_trade("futures", str(self.instrument),
                                        self.active["side"].value, self.active["qty"],
                                        self.active["entry"], px, round(pnl, 2),
                                        "WIN" if pnl > 0 else "LOSS", "ICT")
                self._say(f"[FUT] EXIT {self.active['side'].value} {self.active['entry']:.0f}->{px:.0f} Rs{pnl:,.0f}")
                self.active = None
                self.gates["trades"] += 1
            return
        sig = detect(state, self.bars, self.ict)
        if sig is None:
            return
        side = Side.BUY if sig.direction == "long" else Side.SELL
        if not self._price_ok(side, sig.entry):
            self.gates["skipped_crossed"] += 1
            self._say(f"[FUT] SKIP {side.value} - price crossed {sig.entry:.0f} (no chase)")
            return
        intent = OrderIntent(intent_id="live_" + bar.ts.isoformat(), decision_id="ict",
                             risk_auth_id="live", instrument=str(self.instrument),
                             side=side, quantity=float(self.lots_futures),
                             order_type=OrderType.MARKET, price=sig.entry,
                             idempotency_key="live_" + bar.ts.isoformat(), timestamp=bar.ts)
        ack = self._broker().place_order(ValidatedOrderIntent(intent, True))
        self.active = {"side": side, "entry": sig.entry, "qty": self.lots_futures,
                       "em": ExitManager(side, sig.entry, sig.stop, self.exit_cfg,
                                         target=sig.target)}
        self._say(f"[FUT] ENTRY {side.value} {self.lots_futures}L {sig.entry:.0f} "
                  f"stop {sig.stop:.0f} tgt {sig.target:.0f} [{ack.status.value}]")

    def _options_tick(self, bar) -> None:
        """0DTE condor: enter once near the open, manage intraday, exit at
        TP / SL / session close.  Same gates and mode as the futures path."""
        from parallax.apps.worker.dhan_options_live import ZeroDteCondor
        if self.options_trader is None:
            self.options_trader = ZeroDteCondor(broker=self._broker(),
                                                lots=self.lots_options)
        ot = self.options_trader
        ot.broker.dry_run = (self.store.mode() != "live")
        now = datetime.now(IST)
        today = now.date()

        # -------- entry: one shot inside the open window --------
        if ot.active is None and self.entered_on != today:
            if now.hour == 9 and 20 <= now.minute <= 40:
                plan = ot.select()
                if plan.get("legs"):
                    ot.enter(plan)
                    self.entered_on = today
                    self.gates["trades"] += 1
                else:
                    self._say("[0DTE] no trade: " + str(plan.get("reason")))
                    self.entered_on = today
            return

        # -------- management: TP / SL / end of day --------
        if ot.active is not None:
            action = ot.manage()
            if action in ("tp", "sl"):
                ot.close(action)
                self.gates["trades"] += 1
            elif now.hour == 15 and now.minute >= 15:
                ot.close("eod")
                self.gates["trades"] += 1

    def _say(self, msg: str) -> None:
        print(msg)
        if self.telegram.configured:
            self.telegram.send(msg)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=None)
    p.add_argument("--poll", type=int, default=3)
    a = p.parse_args()
    r = LiveRunner(poll_seconds=a.poll)
    print("gates:", r.run(a.seconds))
