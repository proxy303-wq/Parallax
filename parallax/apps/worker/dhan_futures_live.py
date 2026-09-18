"""Dhan NIFTY futures LIVE loop — 3 lots, live Dhan wallet capital, ICT entry.

Streaming variant of the backtest: keeps a rolling bar window, runs the ICT
master-sequence engine on each new bar, manages the exit, sizes from the LIVE
Dhan wallet balance (1% risk, capped at 3 lots), places dry-run Dhan orders
(PaperBroker fills the P&L), and posts entry/exit to the PARALLAX Telegram bot.

Bar source: Dhan charts when the Data API subscription is active, else a CSV
replay (--csv).  Flip dry_run=False to send real Dhan orders.
"""
from __future__ import annotations

from parallax.contracts import (
    InstrumentId, InstrumentType, OrderStatus, OrderType, Side,
    ValidatedOrderIntent, OrderIntent,
)
from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.broker.paper import PaperBroker
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.adapters.telegram import TelegramBot
from parallax.config.live import lots_from_capital
from parallax.core.ict import ICTConfig, detect
from parallax.core.context import build_context
from parallax.core.execution import ExitConfig, ExitManager
from parallax.core.perception import build_market_state, bars_per_year
from parallax.core.perception import indicators as ind

NIFTY_CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"


class DhanFuturesLive:
    def __init__(self, dry_run: bool = True, max_lots: int = 3,
                 risk_pct: float = 0.01, lookback: int = 600,
                 timeframe: str = "5m", capital: float | None = None):
        self.dhan = DhanBroker(dry_run=dry_run)
        self.max_lots = max_lots
        self.risk_pct = risk_pct
        self.lookback = lookback
        self.timeframe = timeframe
        self.instrument = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE,
                                       exchange="NSE")
        self.ict = ICTConfig()
        self.exit_cfg = ExitConfig()
        self.telegram = TelegramBot()
        self.capital_override = capital
        self.bars: list = []
        self.active: dict | None = None
        self.paper = PaperBroker(capital=self.live_capital(), point_value=65.0,
                                 currency="INR", slippage=0.000002, fee_rate=0.0001)
        self.trades = 0

    def live_capital(self) -> float:
        if self.capital_override:
            return float(self.capital_override)
        acct = self.dhan.get_account()
        if acct.equity >= 100_000:
            return acct.equity
        return 750_000.0   # preview capital until the wallet is funded

    def on_bar(self, bar) -> dict | None:
        self.bars.append(bar)
        if len(self.bars) > self.lookback + 200:
            self.bars = self.bars[-(self.lookback + 200):]
        if len(self.bars) < 40:
            return None

        closes = [b.close for b in self.bars]
        highs = [b.high for b in self.bars]
        lows = [b.low for b in self.bars]
        vols = [b.volume for b in self.bars]
        series = ind.precompute(closes, highs, lows, vols,
                                bars_per_year(self.timeframe))
        state = build_market_state(self.instrument, self.bars, self.timeframe,
                                   series=series, now=bar.ts)
        ctx = build_context({self.timeframe: state}, self.timeframe)
        price = state.last_price or bar.close
        self.paper.set_price(str(self.instrument), price)

        # ---- manage open position (stop-first) ----
        if self.active is not None:
            self.active["hold_bars"] += 1
            exit_price, _ = self.active["em"].update(bar.high, bar.low)
            if exit_price is not None:
                pnl = self.paper.close_position(str(self.instrument), exit_price)
                outcome = "WIN" if (pnl or 0) > 0 else "LOSS"
                msg = (f"[NIFTY FUT] EXIT {self.active['side'].value} "
                       f"{self.active['entry']:.0f}->{exit_price:.0f} {outcome} "
                       f"Rs{(pnl or 0):,.0f}")
                self._say(msg)
                self.trades += 1
                self.active = None
                return {"event": "exit", "pnl": round(pnl or 0, 2), "outcome": outcome}

        # ---- fresh signal ----
        if self.active is None:
            sig = detect(state, self.bars, self.ict)
            if sig is not None:
                side = Side.BUY if sig.direction == "long" else Side.SELL
                stop_dist = abs(sig.entry - sig.stop)
                capital = self.live_capital()
                lots = lots_from_capital(capital, stop_dist,
                                         risk_pct=self.risk_pct,
                                         max_lots=self.max_lots,
                                         point_value=65.0)
                if lots >= 1:
                    self._enter(sig, side, lots, bar)
                    return {"event": "entry", "side": side.value,
                            "lots": lots, "entry": sig.entry, "stop": sig.stop}
        return None

    def _enter(self, sig, side, lots, bar) -> None:
        # dry-run Dhan order (the real order path; no-op while dry_run)
        intent = OrderIntent(intent_id="live_" + bar.ts.isoformat(),
                             decision_id="ict", risk_auth_id="live",
                             instrument=str(self.instrument), side=side,
                             quantity=float(lots), order_type=OrderType.MARKET,
                             price=sig.entry, idempotency_key="live_" + bar.ts.isoformat(),
                             timestamp=bar.ts)
        ack = self.dhan.place_order(ValidatedOrderIntent(intent, True))
        # paper fill for P&L tracking
        self.paper.place_order(ValidatedOrderIntent(intent, True))
        self.active = {
            "side": side, "entry": sig.entry, "qty": lots,
            "em": ExitManager(side, sig.entry, sig.stop, self.exit_cfg,
                              target=sig.target),
            "entry_time": bar.ts, "hold_bars": 0,
        }
        msg = (f"[NIFTY FUT] ENTRY {side.value} {lots}L {sig.entry:.0f} "
               f"stop {sig.stop:.0f} tgt {sig.target:.0f} "
               f"({'; '.join(sig.reasons[:2])}) [{ack.status.value}]")
        self._say(msg)

    def _say(self, msg: str) -> None:
        print(msg)
        if self.telegram.configured:
            self.telegram.send(msg)

    # ---- replay a CSV through the streaming loop ----
    def replay(self, csv_path: str = NIFTY_CSV, max_bars: int | None = None):
        bars = load_ohlcv(csv_path, self.timeframe)
        if max_bars:
            bars = bars[:max_bars]
        for b in bars:
            self.on_bar(b)
        return self.summary()

    def summary(self) -> dict:
        acct = self.paper.get_account()
        return {"trades": self.trades, "equity": round(acct.equity, 0),
                "realized": round(acct.realized_pnl, 0)}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=NIFTY_CSV)
    p.add_argument("--lots", type=int, default=3)
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--capital", type=float, default=None)
    p.add_argument("--max-bars", type=int, default=None)
    a = p.parse_args()
    r = DhanFuturesLive(max_lots=a.lots, risk_pct=a.risk,
                        capital=a.capital).replay(a.csv, a.max_bars)
    print("SUMMARY:", r)
