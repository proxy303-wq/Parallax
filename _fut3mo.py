"""Futures P&L for the last 3 months at 3-lot sizing (1% risk, Rs8L capital)."""
from datetime import datetime, timedelta
from parallax.contracts import InstrumentId, InstrumentType
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.apps.research.ict_backtest import ICTBacktestEngine

bars = load_ohlcv(r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv", "5m")
inst = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE, exchange="NSE")
eng = ICTBacktestEngine(inst, warmup=100, lookback=600, risk_pct=0.01, max_lots=3, capital=800_000)
r = eng.run(bars, "5m")

start = datetime(2026, 6, 10)
recent = [t for t in r.trades if t.entry_time.replace(tzinfo=None) >= start]
net = sum(t.pnl for t in recent)
wins = [t for t in recent if t.pnl > 0]
print(f"=== NIFTY FUTURES (3-lot, 1% risk, Rs8L) — last 3 months ===")
print(f"trades={len(recent)} win={len(wins)/len(recent) if recent else 0:.3f} net_pnl=Rs{net:,.0f}")
print(f"avg_qty={sum(t.qty for t in recent)/len(recent) if recent else 0:.1f} lots")
for t in recent:
    print(f"  {t.entry_time.date()} {t.side} {t.qty}L {t.entry:.0f}->{t.exit:.0f} {t.outcome} Rs{t.pnl:,.0f}")
