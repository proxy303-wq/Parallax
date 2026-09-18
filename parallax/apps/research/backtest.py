"""Event-driven backtest using the same feature/structure/exit code as live (§15).

Replays the full perception -> context -> hypotheses -> decision -> risk ->
execution chain over historical bars with realistic costs and *managed exits*
(lock-profit + trailing), a stop-first conservative convention, and a
day-boundary flatten for index futures.  Every trade records its excursion
(MFE/MAE) and exit reason so the "why" is measurable, not argued.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from parallax.contracts import (
    DecisionClass, ExecutionMode, InstrumentId, OrderStatus, RiskConfig, Side,
    spec_for,
)
from parallax.adapters.broker import PaperBroker
from parallax.core.context import build_context
from parallax.core.decision import DecisionConfig, DecisionEngine
from parallax.core.execution import ExecutionBoundary, ExitConfig, ExitManager
from parallax.core.hypotheses import HypothesisEngine
from parallax.core.metacognition import MetacognitionEngine
from parallax.core.perception import build_market_state, bars_per_year
from parallax.core.perception import indicators as ind
from parallax.core.risk import RiskContext, RiskEngine


@dataclass
class Trade:
    entry_time: object
    exit_time: object
    side: str
    entry: float
    exit: float
    qty: int
    pnl: float
    outcome: str
    decision_id: str = ""
    # diagnostics
    regime: str = ""
    trend: str = ""
    rsi: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    risk_points: float = 0.0       # 1R in price units
    mfe_r: float = 0.0
    mae_r: float = 0.0
    r_multiple: float = 0.0        # gross R (before fees/slippage)
    exit_reason: str = ""
    hold_bars: int = 0
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple] = field(default_factory=list)
    final_equity: float = 0.0
    metrics: dict = field(default_factory=dict)


class BacktestEngine:
    def __init__(self, instrument: InstrumentId,
                 risk_config: RiskConfig | None = None,
                 decision_config: DecisionConfig | None = None,
                 exit_config: ExitConfig | None = None,
                 fee_rate: float | None = None,
                 slippage: float | None = None,
                 lookback: int = 600, warmup: int = 100):
        self.instrument = instrument
        spec = spec_for(instrument.symbol)
        self.spec = spec
        fee = spec.fee_rate if fee_rate is None else fee_rate
        slip = spec.slippage if slippage is None else slippage
        self.risk_config = risk_config or RiskConfig(
            capital=spec.default_capital, point_value=spec.point_value,
            lot_size=spec.lot_size, min_step=spec.min_step,
            fee_rate=fee, slippage=slip)
        self.lookback = lookback
        self.warmup = warmup
        self.exit_config = exit_config or ExitConfig()
        self.broker = PaperBroker(capital=self.risk_config.capital,
                                  point_value=spec.point_value,
                                  currency=spec.currency,
                                  slippage=slip, fee_rate=fee)
        self.hypotheses = HypothesisEngine()
        self.metacognition = MetacognitionEngine()
        if decision_config is None:
            decision_config = DecisionConfig(atr_stop_mult=spec.stop_mult)
        self.decision = DecisionEngine(decision_config, self.metacognition)
        self.risk = RiskEngine(self.risk_config)
        self.execution = ExecutionBoundary(self.broker)
        self._flatten = spec.flatten_at_session_end

    def run(self, bars, timeframe: str = "5m", trade_from: int = 0) -> BacktestResult:
        result = BacktestResult()
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        vols = [b.volume for b in bars]
        series = ind.precompute(closes, highs, lows, vols,
                                bars_per_year(timeframe))

        active: dict | None = None
        daily_pnl = 0.0
        monthly_pnl = 0.0
        trades_today = 0
        consecutive_losses = 0
        current_day = None
        instr = str(self.instrument)

        for i, bar in enumerate(bars):
            day = bar.ts.date()
            if day != current_day:
                daily_pnl = 0.0
                trades_today = 0
                current_day = day

            if i < max(self.warmup, trade_from):
                # warm-up only: indicators/structure are built, but no trading yet
                self.broker.set_price(instr, bar.close)
                result.equity_curve.append((bar.ts, self.broker.get_account().equity))
                continue

            start = max(0, i - self.lookback + 1)
            window_bars = bars[start:i + 1]
            window_series = {k: v[start:i + 1] for k, v in series.items()}
            state = self._state(window_bars, window_series, timeframe)
            price = state.last_price or bar.close
            self.broker.set_price(instr, price)

            # manage open position (managed exits, stop-first convention)
            if active is not None:
                active["hold_bars"] += 1
                last_bar_of_day = (i == len(bars) - 1) or (
                    bars[i + 1].ts.date() != bar.ts.date())
                if self._flatten and last_bar_of_day:
                    exit_price = active["em"].force_exit(bar.close)
                else:
                    exit_price, _ = active["em"].update(bar.high, bar.low)
                if exit_price is not None:
                    self._close_trade(result, active, bar.ts, exit_price)
                    pnl = result.trades[-1].pnl
                    outcome = result.trades[-1].outcome
                    consecutive_losses = consecutive_losses + 1 if outcome == "LOSS" else 0
                    daily_pnl += pnl
                    monthly_pnl += pnl
                    active = None

            # fresh decision when flat
            if active is None:
                hyps = self.hypotheses.generate(state)
                dec = self.decision.decide(
                    state, hyps,
                    recent_outcomes=[t.outcome for t in result.trades[-10:]],
                    trades_today=trades_today, kill_switch=False)
                if dec.decision_class == DecisionClass.TRADE:
                    ctx = RiskContext(
                        equity=self.broker.get_account().equity,
                        daily_pnl=daily_pnl, monthly_pnl=monthly_pnl,
                        consecutive_losses=consecutive_losses,
                        open_positions=len(self.broker.get_positions()),
                        spread=state.price.spread if state.price else None)
                    verdict = self.risk.evaluate(dec, state, ctx)
                    if verdict.approved:
                        ack = self.execution.execute(dec, verdict, instr,
                                                     ExecutionMode.PAPER)
                        if ack.status == OrderStatus.FILLED:
                            side = Side.BUY if dec.action.value == "BUY" else Side.SELL
                            em = ExitManager(side, dec.risk_plan.entry_price or price,
                                             dec.risk_plan.stop_loss,
                                             self.exit_config,
                                             target=dec.risk_plan.target)
                            active = {
                                "decision_id": dec.decision_id,
                                "side": side,
                                "entry": dec.risk_plan.entry_price or price,
                                "qty": verdict.position_size,
                                "entry_time": bar.ts,
                                "confidence": dec.confidence,
                                "regime": state.regime.regime.value,
                                "trend": state.structure.trend,
                                "rsi": state.indicators.rsi,
                                "adx": state.indicators.adx,
                                "atr": state.indicators.atr or 0.0,
                                "risk_points": em.risk,
                                "em": em,
                                "hold_bars": 0,
                            }
                            trades_today += 1

            result.equity_curve.append((bar.ts, self.broker.get_account().equity))

        result.final_equity = self.broker.get_account().equity
        result.metrics = compute_metrics(result.trades, result.final_equity,
                                         self.risk_config.capital)
        return result

    def _close_trade(self, result: BacktestResult, active: dict,
                     exit_time, exit_price: float) -> None:
        pnl = self.broker.close_position(str(self.instrument), exit_price) or 0.0
        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "SCRATCH")
        em: ExitManager = active["em"]
        result.trades.append(Trade(
            entry_time=active["entry_time"], exit_time=exit_time,
            side=active["side"].value, entry=active["entry"], exit=exit_price,
            qty=active["qty"], pnl=round(pnl, 2), outcome=outcome,
            decision_id=active["decision_id"], regime=active["regime"],
            trend=active["trend"], rsi=active["rsi"], adx=active["adx"],
            atr=active["atr"], risk_points=active["risk_points"],
            mfe_r=round(em.mfe_r, 3), mae_r=round(em.mae_r, 3),
            r_multiple=round(em.r_multiple, 3), exit_reason=em.exit_reason,
            hold_bars=active["hold_bars"], confidence=active["confidence"],
        ))

    def _state(self, bars, series, timeframe: str):
        # historical replay: "now" is the bar's own timestamp, so data is never
        # spuriously stale relative to wall-clock time
        ms = build_market_state(self.instrument, bars, timeframe, series=series,
                                now=bars[-1].ts)
        return build_context({timeframe: ms}, timeframe)


def compute_metrics(trades: list[Trade], final_equity: float,
                    start_capital: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": None, "profit_factor": None,
                "total_pnl": 0.0, "return_pct": 0.0, "max_drawdown_pct": 0.0,
                "avg_trade": 0.0, "expectancy": 0.0, "avg_r": 0.0,
                "median_mfe_r": None, "median_mae_r": None}
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = len(wins) / n
    total_pnl = sum(t.pnl for t in trades)

    cum = start_capital
    peak = cum
    max_dd = 0.0
    for t in trades:
        cum += t.pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, (peak - cum) / peak)

    mfe = sorted(t.mfe_r for t in trades)
    mae = sorted(t.mae_r for t in trades)

    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "total_pnl": round(total_pnl, 2),
        "return_pct": round((final_equity - start_capital) / start_capital * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_trade": round(total_pnl / n, 2),
        "expectancy": round(total_pnl / n, 2),
        "avg_r": round(sum(t.r_multiple for t in trades) / n, 3),
        "median_mfe_r": round(mfe[n // 2], 3),
        "median_mae_r": round(mae[n // 2], 3),
    }
