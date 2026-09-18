"""research-lab — backtest, walk-forward validation, diagnostics (§15)."""
from .backtest import BacktestEngine, BacktestResult, compute_metrics
from .diagnostics import excursion_report, full_report, print_report, trade_table
from .walkforward import walk_forward

__all__ = ["BacktestEngine", "BacktestResult", "compute_metrics",
           "excursion_report", "full_report", "print_report", "trade_table",
           "walk_forward"]
