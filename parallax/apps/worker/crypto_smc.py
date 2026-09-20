"""Crypto worker - runs the SMC strategy on ONE symbol in paper, demo or live mode.

The symbol is a parameter (--symbol BTCUSD|ETHUSD|XAUTUSD, default BTCUSD), so a second
instance can paper-trade a second market.  Two workers on one journal store must not
tread on each other, so the dashboard state key is per symbol and an exit clears only
its OWN position row -- see state_key and clear_position.

Mode comes from the shared journal store, so the dashboard toggle drives it:
    paper -> fills simulated locally against live Delta prices, nothing sent
    demo  -> orders routed to the Delta TESTNET (cdn-ind.testnet.deltaex.org)
    live  -> orders routed to Delta production (real money)

The strategy state machine is incremental (parallax.core.smc_crypto.SMCCrypto), so a
tick costs O(1) regardless of history length.

Run:  python -m parallax.apps.worker.crypto_smc --once
      python -m parallax.apps.worker.crypto_smc            (loop)
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from parallax.adapters.broker.delta import DeltaBroker
from parallax.adapters.telegram import TelegramBot
from parallax.contracts import (ExecutionMode, OrderIntent, OrderType, Side,
                                ValidatedOrderIntent, new_id)
from parallax.config.crypto import DEFAULT, DEMO_BASE, LIVE_BASE, USD_INR, base_url, product
from parallax.core.smc_crypto import SMCCrypto
from parallax.web.store import JournalStore

CANDLES = "/v2/history/candles"
RES = {"1h": 3600}


def fetch_bars(symbol: str = "BTCUSD", interval: str = "1h", count: int = 900,
               venue: str = DEMO_BASE, attempts: int = 4) -> pd.DataFrame:
    """Public candles - no auth. Retries: the venue drops connections intermittently
    (WinError 10054 seen in the first paper run)."""
    step = RES[interval]
    end = int(time.time())
    start = end - count * step
    url = ("%s%s?resolution=%s&symbol=%s&start=%d&end=%d"
           % (venue, CANDLES, interval, symbol, start, end))
    last = None
    for a in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "parallax/1.0",
                                                        "Accept": "application/json"})
            raw = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            break
        except Exception as e:
            last = e
            if a == attempts - 1:
                raise
            time.sleep(1.5 * (a + 1))
    else:
        raise last
    rows = raw.get("result", []) or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df = (df[["open_time", "open", "high", "low", "close", "volume"]]
            .dropna().drop_duplicates("open_time").sort_values("open_time")
            .reset_index(drop=True))
    # drop the still-forming bar: only completed candles drive decisions
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["open_time"] + pd.Timedelta(seconds=step) <= now]
    return df.set_index("open_time")


class CryptoWorker:
    def __init__(self, store: JournalStore | None = None, symbol: str = "BTCUSD",
                 interval: str = "1h", poll: int = 60):
        self.store = store or JournalStore()
        self.symbol = symbol
        self.interval = interval
        self.poll = poll
        # The contract spec is per symbol: ETHUSD is a 0.01 contract, BTCUSD 0.001.
        self.cfg = replace(DEFAULT, symbol=symbol)
        self.prod = product(symbol)
        self.machine = SMCCrypto(self.cfg)
        self.bars: pd.DataFrame | None = None
        self.broker = None
        self.broker_mode = None
        self.trades_today = 0
        self.tg = TelegramBot()
        self._last_err_notify = 0.0
        self._last_heartbeat = 0.0

    @property
    def state_key(self) -> str:
        """Dashboard state is keyed PER SYMBOL so concurrent workers cannot overwrite
        each other and make the dashboard flicker between two books."""
        return "crypto_state_" + self.symbol

    # -- notifications ----------------------------------------------------
    def notify(self, text: str) -> None:
        if self.tg.configured:
            try:
                self.tg.send(text)
            except Exception as e:
                print("  ! telegram failed:", e, flush=True)

    def notify_error(self, err, every: int = 900) -> None:
        """Throttled so a flaky network cannot spam the channel."""
        now = time.time()
        if now - self._last_err_notify >= every:
            self._last_err_notify = now
            self.notify("[PARALLAX crypto] %s mode: %s\n%s" % (self.mode(), type(err).__name__, err))

    # -- mode / broker ----------------------------------------------------
    def mode(self) -> str:
        return self.store.mode()

    def _broker(self):
        m = self.mode()
        if m == "paper":
            return None
        if self.broker is None or self.broker_mode != m:
            self.broker = DeltaBroker(dry_run=False, base_url=base_url(m))
            self.broker_mode = m
        return self.broker

    def equity(self) -> float:
        m = self.mode()
        if m == "paper":
            return self.store.paper_capital()
        b = self._broker()
        if b is None or not b.configured:
            return 0.0
        try:
            return float((b.get_account() or {}).equity or 0.0)
        except Exception:
            return 0.0

    # -- data -------------------------------------------------------------
    def refresh(self) -> bool:
        """Pull candles; True when a NEW completed bar appeared."""
        venue = DEMO_BASE if self.mode() in ("paper", "demo") else LIVE_BASE
        df = fetch_bars(self.symbol, self.interval, count=900, venue=venue)
        if df.empty:
            return False
        new = self.bars is None or df.index[-1] > self.bars.index[-1]
        self.bars = df
        return new

    # -- execution --------------------------------------------------------
    def _send(self, side: str, qty: float, price=None, otype=OrderType.MARKET):
        """Route an order to Delta.  Never called in paper mode."""
        b = self._broker()
        if b is None or not b.configured:
            print("  ! broker not configured - order suppressed", flush=True)
            return None
        mode = ExecutionMode.DEMO if self.mode() == "demo" else ExecutionMode.LIVE
        intent = OrderIntent(
            intent_id=new_id(), decision_id="crypto_smc", risk_auth_id="crypto_smc",
            instrument=self.symbol,
            side=Side.BUY if side == "buy" else Side.SELL,
            quantity=float(qty), order_type=otype,
            price=(float(price) if price is not None else None),
            mode=mode, idempotency_key=new_id())
        try:
            ack = b.place_order(ValidatedOrderIntent(intent=intent, valid=True,
                                                     errors=[], risk_authorized=True))
            print("  -> %s %s %s @%s : %s" % (self.mode(), side, qty, price, ack), flush=True)
            return ack
        except Exception as e:
            print("  ! order failed: %s" % e, flush=True)
            return None

    def handle(self, dec) -> None:
        m = self.mode()
        if dec.action == "place":
            self.store.set_position(self.symbol, "crypto",
                                    "BUY" if dec.side > 0 else "SELL", 0.0,
                                    dec.price, dec.stop, 0.0)
            self.notify(
                "[PARALLAX crypto] %s ORDER RESTING\n"
                "%s %s\nLimit %.1f\nStop  %.1f\nBar %s" % (
                    m.upper(), self.symbol, "BUY" if dec.side > 0 else "SELL",
                    dec.price, dec.stop, dec.ts))
        elif dec.action == "fill":
            eq = self.equity()
            qty = self.machine.size(eq, dec.price, dec.stop)
            if qty <= 0:
                return
            # defence in depth: never let a sizing bug risk more than 5% of the account
            risk_inr = qty * self.prod.contract_value * abs(dec.price - dec.stop) * USD_INR
            if eq > 0 and risk_inr > 0.05 * eq:
                self.notify("[PARALLAX crypto] SIZE REJECTED\nrisk Rs%.0f = %.1f%% of equity"
                            % (risk_inr, 100.0 * risk_inr / eq))
                return
            if m != "paper":
                if self._send("buy" if dec.side > 0 else "sell", qty) is None:
                    return
            risk = qty * self.prod.contract_value * abs(dec.price - dec.stop) * USD_INR  # rupees
            self.machine.open_position(dec.side, dec.price, qty, dec.stop, risk, ts=dec.ts)
            self.store.set_position(self.symbol, "crypto",
                                    "BUY" if dec.side > 0 else "SELL", qty,
                                    dec.price, dec.stop, 0.0)
            self.notify(
                "[PARALLAX crypto] %s FILLED\n%s %s\nEntry %.1f\nStop  %.1f\n"
                "Qty   %d contracts (%.3f BTC)\nRisk  Rs%.0f (%.2f%% of equity)" % (
                    m.upper(), self.symbol, "BUY" if dec.side > 0 else "SELL",
                    dec.price, dec.stop, int(qty), qty * self.prod.contract_value,
                    risk, 100.0 * risk / eq if eq else 0.0))
        elif dec.action == "exit":
            if m != "paper" and self.machine.position is not None:
                pos = self.machine.position
                self._send("sell" if pos.side > 0 else "buy", pos.qty)
            self.store.record_trade("crypto", self.symbol,
                                    "BUY" if dec.side > 0 else "SELL",
                                    float(getattr(dec, "qty", 0) or 0), 0.0,
                                    dec.exit_price, dec.pnl, dec.reason,
                                    note="R=%+.2f" % dec.r_multiple)
            # clear ONLY this symbol: a global wipe would delete the other worker's position
            self.store.clear_position(self.symbol)
            self.notify(
                "[PARALLAX crypto] %s CLOSED (%s)\n%s %s @ %.1f\nP&L   Rs%+.0f  (%+.2fR)\n"
                "Equity Rs%.0f" % (m.upper(), dec.reason, self.symbol,
                                   "LONG" if dec.side > 0 else "SHORT", dec.exit_price,
                                   dec.pnl, dec.r_multiple, self.equity()))

    # -- one cycle --------------------------------------------------------
    def tick(self) -> list:
        if not self.refresh():
            return []
        equity = self.equity()
        self.store.snapshot_capital(equity, equity, 0.0)
        out = []
        for dec in self.machine.step(self.bars):
            if dec.action != "none":
                self.handle(dec)
                out.append(dec)
        # publish state for the dashboard (single source of truth = the journal store)
        snap = self.machine.snapshot()
        snap["symbol"] = self.symbol
        snap["interval"] = self.interval
        snap["mode"] = self.mode()
        snap["equity"] = equity
        snap["last_price"] = float(self.bars["close"].iloc[-1]) if len(self.bars) else None
        snap["last_bar"] = str(self.bars.index[-1]) if len(self.bars) else None
        snap["events"] = self.machine.events[-8:]
        self.store.set_setting(self.state_key, json.dumps(snap))
        return out

    def run(self):
        print("crypto worker: %s %s | mode=%s" % (self.symbol, self.interval, self.mode()),
              flush=True)
        self.notify("[PARALLAX crypto] worker started\n%s %s | mode %s\n"
                    "paper capital Rs%.0f" % (self.symbol, self.interval,
                                              self.mode().upper(), self.store.paper_capital()))
        while True:
            try:
                for d in self.tick():
                    print("  %s %s side=%d @%.1f" % (d.ts, d.action, d.side, d.price),
                          flush=True)
                # hourly heartbeat so the channel shows the bot is alive
                if time.time() - self._last_heartbeat >= 3600:
                    self._last_heartbeat = time.time()
                    st = self.machine.snapshot()
                    self.notify("[PARALLAX crypto] alive | %s | bias %s | %s | price %.1f"
                                % (self.mode().upper(),
                                   {1: "LONG", -1: "SHORT", 0: "flat"}.get(st["bias"]),
                                   ("POSITION open" if st["position"] else
                                    ("order working" if st["order"] else "flat")),
                                   float(self.bars["close"].iloc[-1]) if self.bars is not None
                                   and len(self.bars) else 0.0))
            except Exception as e:
                print("tick error:", e, flush=True)
                self.notify_error(e)
            time.sleep(self.poll)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--symbol", default="BTCUSD",
                    help="Delta perpetual to trade: BTCUSD (default), ETHUSD, XAUTUSD")
    ap.add_argument("--interval", default="1h")
    a = ap.parse_args()
    w = CryptoWorker(symbol=a.symbol.upper(), interval=a.interval, poll=a.poll)
    if a.once:
        ds = w.tick()          # tick() refreshes internally; do not refresh twice
        print("bars:", 0 if w.bars is None else len(w.bars))
        print("mode:", w.mode(), "| equity: Rs%.0f" % w.equity())
        print("equity snapshot in store:", w.store.capital())
        print("decisions:", len(ds))
        for d in ds:
            print("   %s %s side=%d @%.1f" % (d.ts, d.action, d.side, d.price))
        print("state:", w.machine.snapshot())
    else:
        w.run()


if __name__ == "__main__":
    main()
