"""Dhan NIFTY futures runner — 3 lots, live Dhan wallet capital, ICT entry.

Replays the ICT master-sequence engine on NIFTY 5m bars, sizes each trade from
the LIVE Dhan wallet balance (1% risk, capped at 3 lots), paper-executes via
the dry-run DhanBroker, and posts a summary + recent-trade feed to the
dedicated PARALLAX Telegram bot.  Flip dry_run=False to send real Dhan orders.

If the Dhan wallet is unfunded (<Rs1L), the sizing preview falls back to the
planned Rs7.5L so the equity curve shows what 3 lots WILL do once funded.
"""
from __future__ import annotations

from parallax.contracts import InstrumentId, InstrumentType
from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.adapters.telegram import TelegramBot
from parallax.apps.research.ict_backtest import ICTBacktestEngine

NIFTY_CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"


class DhanFuturesRunner:
    def __init__(self, dry_run: bool = True, max_lots: int = 3,
                 risk_pct: float = 0.01, capital: float | None = None,
                 timeframe: str = "5m"):
        self.dhan = DhanBroker(dry_run=dry_run)
        self.max_lots = max_lots
        self.risk_pct = risk_pct
        self.capital_override = capital
        self.timeframe = timeframe
        self.instrument = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE,
                                       exchange="NSE")
        self.telegram = TelegramBot()

    def live_capital(self) -> float:
        if self.capital_override:
            return float(self.capital_override)
        acct = self.dhan.get_account()
        return acct.equity if acct.equity > 0 else 0.0

    def _capital_for_run(self) -> tuple[float, str]:
        cap = self.live_capital()
        if cap >= 100_000:
            return cap, "live Dhan wallet"
        return 750_000.0, "preview Rs7.5L (Dhan wallet unfunded)"

    def run_replay(self, csv_path: str = NIFTY_CSV) -> dict:
        capital, src = self._capital_for_run()
        bars = load_ohlcv(csv_path, self.timeframe)
        eng = ICTBacktestEngine(self.instrument, warmup=100, lookback=600,
                                risk_pct=self.risk_pct, max_lots=self.max_lots,
                                capital=capital)
        result = eng.run(bars, self.timeframe)
        m = result.metrics
        summary = {
            "label": "NIFTY FUT",
            "mode": "PAPER" if self.dhan.dry_run else "LIVE",
            "equity": round(result.final_equity, 0),
            "daily_pnl": round(m.get("total_pnl") or 0, 0),
            "open_positions": 0,
            "note": (f"{len(result.trades)} trades | win {m.get('win_rate')} "
                     f"| PF {m.get('profit_factor')} | risk {self.risk_pct:.0%} "
                     f"max {self.max_lots}L | {src}"),
        }
        self._notify(summary, result)
        return summary

    def _notify(self, summary, result) -> None:
        msg = self.telegram.describe_portfolio([summary])
        print(msg)
        if result.trades:
            last = result.trades[-5:]
            for t in last:
                line = (f"  {t.entry_time.date()} {t.side} {t.qty}L "
                        f"{t.entry:.0f}->{t.exit:.0f} {t.outcome} Rs{t.pnl:,.0f}")
                print(line)
        if self.telegram.configured:
            self.telegram.send(msg)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=NIFTY_CSV)
    p.add_argument("--lots", type=int, default=3)
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--capital", type=float, default=None)
    p.add_argument("--dry-run", action="store_true", default=True)
    a = p.parse_args()
    DhanFuturesRunner(dry_run=a.dry_run, max_lots=a.lots, risk_pct=a.risk,
                      capital=a.capital).run_replay(a.csv)
