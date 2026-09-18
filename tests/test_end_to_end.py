"""End-to-end backtest + executive replay tests."""
from parallax.contracts import InstrumentId, InstrumentType
from parallax.apps.research import BacktestEngine

from conftest import make_bars


def test_backtest_runs_and_is_deterministic():
    inst = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE)
    bars = make_bars(seed=3, drift=2.0, n=1200)

    e1 = BacktestEngine(inst, warmup=100)
    r1 = e1.run(bars, "5m")
    e2 = BacktestEngine(inst, warmup=100)
    r2 = e2.run(bars, "5m")

    assert "trades" in r1.metrics
    assert r1.metrics["trades"] == r2.metrics["trades"]
    assert r1.final_equity == r2.final_equity


def test_backtest_respects_risk_limit():
    inst = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE)
    bars = make_bars(seed=5, drift=1.0, n=1500)
    r = BacktestEngine(inst, warmup=100).run(bars, "5m")
    # no single trade should exceed the 0.5% risk budget by more than a
    # factor reflecting a full stop-out (risk_amount <= 0.5% of 500k)
    for t in r.trades:
        assert abs(t.pnl) <= 500_000 * 0.005 * 2 + 1


def test_executive_replay_produces_decisions():
    from parallax.executive import ParallaxExecutive
    inst = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE)
    bars = make_bars(seed=3, drift=2.0, n=800)
    ex = ParallaxExecutive(inst, lookback=600)
    for i in range(100, len(bars)):
        ex.evaluate(bars[max(0, i - 599):i + 1])
    summary = ex.summary()
    assert summary["equity"] is not None
    assert summary["system_state"] in {
        "WAITING", "POSITION_ACTIVE", "REFLECTING", "OBSERVING", "PAUSED"}
