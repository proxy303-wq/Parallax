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
        self.gates = {"skipped_stale": 0, "skipped_crossed": 0, "trades": 0}

    # ---- mode / broker ---------------------------------------------------
    def _broker(self) -> DhanBroker:
        live = self.store.mode() == "live"
        return DhanBroker(dry_run=not live)

    # ---- data ------------------------------------------------------------
    def _fetch_bars(self, day=None):
        """Completed 5-min NIFTY bars for the given day from Dhan charts."""
        b = self._broker()
        api = b._api_client()
        d = (day or datetime.now(IST)).strftime("%Y-%m-%d")
        res = api.intraday_minute_data(NIFTY_SCrip, "IDX_I", "INDEX",
                                       d + " 09:15:00", d + " 15:30:00", "5")
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

    # ---- main loop -------------------------------------------------------
    def run(self, max_seconds: int | None = None) -> dict:
        t0 = time.time()
        self._say(f"PARALLAX live runner online [{self.store.mode().upper()}]")
        while True:
            try:
                now = datetime.now(IST)
                today = now.date()
                if today.weekday() < 5 and now.hour >= 9 and now.hour < 16:
                    bars = self._fetch_bars(now)
                    if bars:
                        newest = bars[-1]
                        if newest.ts != self.last_bar_ts:
                            self.last_bar_ts = newest.ts
                            if self._fresh(newest):
                                self.bars = bars[-800:]
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
        # 0DTE condor entry/management lives in dhan_options_live; the runner
        # only gates whether it may act today (session scheduler).
        return

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
