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
    InstrumentId, InstrumentType, OrderIntent, OrderStatus, OrderType, Side,
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
GATE_TOL = 0.002          # no-chase tolerance, matched to the backtest

# Futures exit policy.  Measured over 2 years of NIFTY 5m
# (research/futures_study): the breakeven lock at 0.5R is reached by ordinary
# noise - 0.5R is ~15 points against a ~12-point ATR - so it scratches trades
# that would have run and pays the round trip each time.  Relaxing it is
# monotone in BOTH the train and test windows:
#     lock 0.5 (old)  PF 1.01  maxDD Rs 62,061   train PF 0.76
#     lock 3.0        PF 1.19  maxDD Rs 48,826   train PF 0.91
#     no lock at all  PF 1.20  maxDD Rs 42,365   train PF 0.95
# So the futures exit is the structural stop plus the DOL target, nothing else.
FUTURES_EXIT = ExitConfig(lock_r=float("inf"), trail_r=0.0)


class LiveRunner:
    def __init__(self, poll_seconds: int = 3, max_bar_age: int = 400,
                 lots_futures: int = 3, lots_options: int = 8,
                 bar_seconds: int = 300, paper_slippage: float = 0.5,
                 max_entry_drift: float = 0.002):
        self.poll = poll_seconds
        self.max_bar_age = max_bar_age
        self.bar_seconds = bar_seconds   # 5-minute bars: a bar is only usable
                                         # once its close time has passed
        self.paper_slippage = paper_slippage      # NIFTY points, paper fills only
        self.max_entry_drift = max_entry_drift    # reject a fill this far past entry
        self.eod_exit_hm = (15, 15)               # hard futures time stop (IST)
        self.lots_futures = lots_futures
        self.lots_options = lots_options
        self.store = JournalStore()
        self.telegram = TelegramBot()
        self.ict = ICTConfig()
        self.exit_cfg = FUTURES_EXIT
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
        self._next_token_try = 0.0
        self.refreshed_on = None
        self.gates = {"skipped_stale": 0, "skipped_crossed": 0, "trades": 0,
                      "skipped_rejected": 0, "skipped_drift": 0}

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
        tok, src = refresh_token(cid, env("DHAN_PIN"), env("DHAN_TOTP_SECRET"),
                                 min_hours=(0.0 if force else 12.0))
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
        """A bar is usable only once it has CLOSED and is not yet stale.

        Dhan stamps an intraday bar at its START time.  The old check only
        bounded the age from above, so a bar stamped 09:20 read at 09:22 passed
        as 'fresh' while it was still forming - the engine then acted on a
        half-built candle, which is neither causal nor reproducible.  The
        window is now age in [bar_seconds, max_bar_age].
        """
        now = datetime.now(IST)
        age = (now - bar.ts).total_seconds()
        if age < self.bar_seconds:
            return False
        return age <= self.max_bar_age

    def _fill_price(self, ack, bar, side: Side):
        """The price we actually got, or None if the order did not fill.

        The journal used to be written at sig.entry even though a MARKET order
        was sent seconds after the signal bar closed - by which time the market
        had left that level.  That booked a trade that never happened.
        """
        if ack.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED,
                          OrderStatus.EXPIRED):
            return None
        if ack.avg_price:
            return float(ack.avg_price)
        if ack.status == OrderStatus.FILLED:
            return float(bar.close)
        if self._broker().dry_run:
            # paper: there is no real fill, so price it where the market is now
            # - the signal bar's close, which is the next bar's open - and
            # charge a realistic market-order crossing.
            px = float(bar.close)
            return px + self.paper_slippage if side == Side.BUY else px - self.paper_slippage
        return None

    def _price_ok(self, side: Side, entry: float) -> bool:
        """Entry-validity gate: do not chase a price that already crossed.

        get_quote() returns nothing for the NIFTY index on this account
        (verified live: last=0.0 while intraday_minute_data works fine), so the
        old version fell through to "return True" on every call and the gate
        was inert - live was not applying the same no-chase rule the backtest
        measures.  Fall back to the newest completed bar's close, which is the
        same reference the study gates on.
        """
        px = 0.0
        try:
            q = self._broker().get_quote(str(self.instrument))
            px = q.last or q.bid or q.ask or 0.0
        except Exception:
            px = 0.0
        if not px and self.bars:
            px = float(self.bars[-1].close or 0.0)
        if not px:
            return True                      # genuinely no reference available
        if side == Side.BUY:
            return px <= entry * (1 + GATE_TOL)
        return px >= entry * (1 - GATE_TOL)

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
            detail = (f"{self.lots_options} lots, TP ratchet 50/75/90, SL 2x, "
                      f"max loss ~Rs{(100 - 16) * 65 * self.lots_options:,.0f}/day")
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
                # Daily token refresh, from 08:00 IST until it actually works.
                # This used to mark the day done BEFORE attempting the refresh,
                # so a single failure at 08:00 - a Dhan blip, or the once-per-
                # 2-minute generation limit - left the account with no usable
                # token for the whole session.  It now retries every 2 minutes
                # (which is exactly Dhan's TOTP rate limit) until the token is
                # valid, and only then marks the day complete.
                if now.hour >= 8 and self.refreshed_on != today:
                    if time.time() >= self._next_token_try:
                        self._next_token_try = time.time() + 120
                        try:
                            self._say(self._daily_refresh(now))
                        except Exception as e:
                            self._say("[TOKEN] refresh error: " + str(e)[:90])
                        try:
                            from parallax.adapters.broker.dhan_auth import (
                                token_status as _ts,
                            )
                            st = _ts()
                            if st.get("valid"):
                                self.refreshed_on = today
                                self._say("[TOKEN] ready: %sh left from %s"
                                          % (st.get("hours_left"), st.get("source")))
                        except Exception:
                            pass
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
                    # runs every poll, not only on a new bar: the 15:15 stop
                    # must not wait for the next 5-minute bar to complete
                    if futures_active(today):
                        self._futures_housekeeping(now)
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
                self._close_futures(px, "managed")
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
                             order_type=OrderType.MARKET, price=0.0,
                             idempotency_key="live_" + bar.ts.isoformat(), timestamp=bar.ts)
        ack = self._broker().place_order(ValidatedOrderIntent(intent, True))
        fill = self._fill_price(ack, bar, side)
        if fill is None:
            self.gates["skipped_rejected"] += 1
            self._say(f"[FUT] REJECT {side.value} [{ack.status.value}] "
                      f"{str(ack.message)[:60]}")
            return
        # a fill on the wrong side of the stop, or already at/past the DOL
        # target, is not a trade.  The second case used to open a position and
        # then exit on the very next tick at the target, for a guaranteed loss.
        if (side == Side.BUY and fill <= sig.stop) or (
                side == Side.SELL and fill >= sig.stop):
            self.gates["skipped_drift"] += 1
            self._say(f"[FUT] SKIP {side.value} - fill {fill:.0f} is past the "
                      f"stop {sig.stop:.0f}")
            return
        if (fill >= sig.target) if side == Side.BUY else (fill <= sig.target):
            self.gates["skipped_drift"] += 1
            self._say(f"[FUT] SKIP {side.value} - fill {fill:.0f} is already at "
                      f"the target {sig.target:.0f}, no room left")
            return
        sign = 1.0 if side == Side.BUY else -1.0
        drift = sign * (fill - sig.entry)
        if drift > self.max_entry_drift * sig.entry:
            self.gates["skipped_drift"] += 1
            self._flatten_immediately(side, fill)
            self._say(f"[FUT] SKIP {side.value} - filled {fill:.0f} vs signal "
                      f"{sig.entry:.0f} ({drift:.0f} pts chased)")
            return
        self.active = {"side": side, "entry": fill, "qty": self.lots_futures,
                       "signal_entry": sig.entry,
                       "em": ExitManager(side, fill, sig.stop, self.exit_cfg,
                                         target=sig.target)}
        self._say(f"[FUT] ENTRY {side.value} {self.lots_futures}L signal "
                  f"{sig.entry:.0f} filled {fill:.0f} stop {sig.stop:.0f} "
                  f"tgt {sig.target:.0f} [{ack.status.value}]")

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
                # never stack a second condor on top of a held positional one
                held = [p for p in self.store.positions()
                        if str(p.get("strategy") or "").startswith("options")]
                if held:
                    self._say("[0DTE] skip entry - positional condor already open: "
                              + ", ".join(str(p.get("instrument")) for p in held))
                    self.entered_on = today
                    return
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
            if action != "hold":
                ot.close(action)
                self.gates["trades"] += 1
            elif now.hour == 15 and now.minute >= 15:
                ot.close("eod")
                self.gates["trades"] += 1

    def _close_futures(self, px: float, reason: str) -> None:
        """Exit the futures position, net of fees, and journal the real numbers."""
        if self.active is None:
            return
        a = self.active
        sign = 1.0 if a["side"] == Side.BUY else -1.0
        units = a["qty"] * 65.0
        fees = (a["entry"] + px) * units * 0.0001      # NIFTY spec fee_rate
        pnl = (px - a["entry"]) * sign * units - fees
        try:
            opp = Side.SELL if a["side"] == Side.BUY else Side.BUY
            intent = OrderIntent(
                intent_id="exit_" + str(time.time()), decision_id="ict",
                risk_auth_id="live", instrument=str(self.instrument), side=opp,
                quantity=float(a["qty"]), order_type=OrderType.MARKET, price=0.0,
                idempotency_key="exit_" + str(time.time()),
                timestamp=datetime.now(timezone.utc))
            self._broker().place_order(ValidatedOrderIntent(intent, True))
        except Exception as e:
            print("futures exit order failed:", type(e).__name__, str(e)[:100])
        self.store.record_trade("futures", str(self.instrument), a["side"].value,
                                a["qty"], a["entry"], px, round(pnl, 2),
                                "WIN" if pnl > 0 else "LOSS", "ICT-" + reason)
        self._say(f"[FUT] EXIT {a['side'].value} {a['entry']:.0f}->{px:.0f} "
                  f"Rs{pnl:,.0f} [{reason}]")
        self.active = None
        self.gates["trades"] += 1

    def _futures_housekeeping(self, now) -> None:
        """Hard time stop.  INTRADAY product is auto-squared-off by the broker
        shortly after 15:15 with a penalty, and the backtest assumes a 15:15
        flatten - the runner used to do neither, so a position could be carried
        into the broker's square-off."""
        if self.active is None:
            return
        if (now.hour, now.minute) < self.eod_exit_hm:
            return
        px = self.bars[-1].close if self.bars else 0.0
        if px <= 0:
            return
        self._close_futures(px, "eod")

    def _flatten_immediately(self, side: Side, fill: float) -> None:
        """Undo an entry that was filled too far past the signal."""
        try:
            opp = Side.SELL if side == Side.BUY else Side.BUY
            intent = OrderIntent(
                intent_id="flat_" + str(time.time()), decision_id="ict",
                risk_auth_id="live", instrument=str(self.instrument), side=opp,
                quantity=float(self.lots_futures), order_type=OrderType.MARKET,
                price=0.0, idempotency_key="flat_" + str(time.time()),
                timestamp=datetime.now(timezone.utc))
            self._broker().place_order(ValidatedOrderIntent(intent, True))
        except Exception as e:
            print("flatten failed:", type(e).__name__, str(e)[:100])

    def _say(self, msg: str) -> None:
        # flush: stdout to a pipe is block-buffered, so without this a service
        # that was talking to Telegram looked totally silent in journalctl
        print(msg, flush=True)
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
