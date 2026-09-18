"""Walk-forward validation (§15).

Rolling out-of-sample test: for each step, the indicators/structure warm up on
the prior (train) bars and the system only trades on the held-out (test) bars.
This avoids both look-ahead and the cold-start warm-up artifact that
systematically distorts indicator values (EMA/RSI/ADX) at window boundaries.
"""
from __future__ import annotations

from parallax.contracts import InstrumentId, RiskConfig
from parallax.core.decision import DecisionConfig

from .backtest import BacktestEngine


def walk_forward(instrument: InstrumentId, bars, timeframe: str = "5m",
                 train_size: int = 500, test_size: int = 300,
                 risk_config: RiskConfig | None = None,
                 decision_config: DecisionConfig | None = None) -> list[dict]:
    windows: list[dict] = []
    i = 0
    n = len(bars)
    window_no = 0
    while i + train_size + test_size <= n:
        seg = bars[i:i + train_size + test_size]   # train (warm-up) + test (OOS)
        eng = BacktestEngine(instrument, risk_config, decision_config,
                             warmup=min(200, max(1, train_size)))
        result = eng.run(seg, timeframe, trade_from=train_size)
        windows.append({
            "window": window_no,
            "start": seg[train_size].ts.isoformat(),
            "end": seg[-1].ts.isoformat(),
            "metrics": result.metrics,
        })
        i += test_size
        window_no += 1
    return windows
