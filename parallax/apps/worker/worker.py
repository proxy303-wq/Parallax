"""parallax-worker — replay/loop runner.

Drives the executive over a stream of bars and emits Telegram messages from
structured state.  The same loop shape is used in live mode (bars arrive from
a feed instead of a replay list).
"""
from __future__ import annotations

from parallax.contracts import DecisionClass, ExecutionMode, InstrumentId, RiskConfig
from parallax.core.decision import DecisionConfig
from parallax.executive import ParallaxExecutive
from parallax.adapters.telegram import TelegramBot


def replay_bars(instrument: InstrumentId, bars, timeframe: str = "5m",
                risk_config: RiskConfig | None = None,
                decision_config: DecisionConfig | None = None,
                mode: ExecutionMode = ExecutionMode.PAPER,
                telegram: TelegramBot | None = None,
                warmup: int = 100,
                quiet: bool = False) -> ParallaxExecutive:
    """Replay bars through the executive, emitting Telegram messages on
    TRADE / EXIT / ABORT decisions, then a day summary."""
    ex = ParallaxExecutive(instrument, risk_config, decision_config, mode,
                           timeframe=timeframe)
    for i, bar in enumerate(bars):
        if i < warmup:
            continue
        window = bars[max(0, i - ex.lookback + 1):i + 1]
        result = ex.evaluate(window, timeframe)
        if telegram and result is not None and result.decision.decision_class in (
                DecisionClass.TRADE, DecisionClass.EXIT, DecisionClass.ABORT):
            telegram.send(telegram.describe_decision(result.decision))
    if telegram:
        telegram.send(telegram.describe_summary(ex.summary()))
    return ex
