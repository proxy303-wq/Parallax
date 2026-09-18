"""ICT/SMC backtest — replays the master-sequence entry engine with realistic costs.

Reuses the same PaperBroker + ExitManager + cost model as the main backtest, but
the entry is the deterministic ICT sequence (sweep → displacement → MSS → FVG →
retracement → DOL) instead of the hypothesis/decision chain.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from parallax.contracts import (
    ExecutionMode, InstrumentId, OrderStatus, Side, spec_for,
)
from parallax.adapters.broker import PaperBroker
from parallax.core.context import build_context
from parallax.core.execution import ExitConfig, ExitManager
from parallax.core.ict import ICTConfig, detect
from parallax.core.perception import build_market_state, bars_per_year
from parallax.core.perception import indicators as ind

from .backtest import BacktestResult, Trade, compute_metrics


class ICTBacktestEngine:
    def __init__(self, instrument: InstrumentId,
                 ict_config: ICTConfig | None = None,
                 exit_config: ExitConfig | None = None,
                 lookback: int = 600, warmup: int = 100,
                 risk_pct: float = 0.005, fixed_lots: int | None = None):
        spec = spec_for(instrument.symbol)
        self.instrument = instrument
        self.ict_config = ict_config or ICTConfig()
        self.exit_config = exit_config or ExitConfig()
        self.lookback = lookback
        self.warmup = warmup
        self.risk_pct = risk_pct
        self.fixed_lots = fixed_lots
        self.spec = spec
        self.broker = PaperBroker(capital=spec.default_capital,
                                  point_value=spec.point_value,
                                  currency=spec.currency,
                                  slippage=spec.slippage, fee_rate=spec.fee_rate)
        self._flatten = spec.flatten_at_session_end

    def run(self, bars, timeframe: str = "5m") -> BacktestResult:
        result = BacktestResult()
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        vols = [b.volume for b in bars]
        series = ind.precompute(closes, highs, lows, vols, bars_per_year(timeframe))

        active: dict | None = None
        daily_pnl = 0.0
        trades_today = 0
        current_day = None
        instr = str(self.instrument)

        for i, bar in enumerate(bars):
            day = bar.ts.date()
            if day != current_day:
                daily_pnl = 0.0
                trades_today = 0
                current_day = day

            if i < max(self.warmup, 40):
                self.broker.set_price(instr, bar.close)
                result.equity_curve.append((bar.ts, self.broker.get_account().equity))
                continue

            start = max(0, i - self.lookback + 1)
            wb = bars[start:i + 1]
            ws = {k: v[start:i + 1] for k, v in series.items()}
            state = self._state(wb, ws, timeframe)
            price = state.last_price or bar.close
            self.broker.set_price(instr, price)

            # manage open position (managed exits, stop-first)
            if active is not None:
                active["hold_bars"] += 1
                last_of_day = (i == len(bars) - 1) or (bars[i + 1].ts.date() != bar.ts.date())
                if self._flatten and last_of_day:
                    exit_price = active["em"].force_exit(bar.close)
                else:
                    exit_price, _ = active["em"].update(bar.high, bar.low)
                if exit_price is not None:
                    pnl = self.broker.close_position(instr, exit_price) or 0.0
                    outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "SCRATCH")
                    daily_pnl += pnl
                    result.trades.append(Trade(
                        entry_time=active["entry_time"], exit_time=bar.ts,
                        side=active["side"].value, entry=active["entry"],
                        exit=exit_price, qty=active["qty"], pnl=round(pnl, 2),
                        outcome=outcome, decision_id=active["signal_id"],
                        mfe_r=round(active["em"].mfe_r, 3),
                        mae_r=round(active["em"].mae_r, 3),
                        r_multiple=round(active["em"].r_multiple, 3),
                        exit_reason=active["em"].exit_reason,
                        hold_bars=active["hold_bars"]))
                    active = None

            # fresh ICT signal when flat
            if active is None:
                signal = detect(state, wb, self.ict_config)
                if signal is not None:
                    side = Side.BUY if signal.direction == "long" else Side.SELL
                    stop_dist = abs(signal.entry - signal.stop)
                    if stop_dist > 0:
                        # sizing
                        equity = self.broker.get_account().equity
                        risk_budget = equity * self.risk_pct
                        per_unit = stop_dist * self.spec.point_value
                        step = max(self.spec.min_step, 1e-9)
                        if self.fixed_lots is not None:
                            qty = self.fixed_lots
                        else:
                            qty = math.floor(risk_budget / per_unit / step) * step
                        # cost-to-vol gate (same as the main risk engine)
                        stop_pct = stop_dist / max(signal.entry, 1e-9)
                        rt_cost = 2.0 * (self.spec.fee_rate + self.spec.slippage)
                        if qty >= step and (rt_cost / stop_pct) <= 0.2:
                            em = ExitManager(side, signal.entry, signal.stop,
                                             self.exit_config, target=signal.target)
                            # place order via the broker (paper fill at entry)
                            from parallax.contracts import OrderIntent, OrderType, ValidatedOrderIntent
                            intent = OrderIntent(
                                intent_id="ict_" + bar.ts.isoformat(),
                                decision_id="ict_sig", risk_auth_id="ict",
                                instrument=instr, side=side, quantity=qty,
                                order_type=OrderType.MARKET, price=signal.entry,
                                idempotency_key="ict_" + bar.ts.isoformat(),
                                timestamp=bar.ts)
                            ack = self.broker.place_order(ValidatedOrderIntent(intent, True))
                            if ack.status == OrderStatus.FILLED:
                                active = {
                                    "signal_id": "ict", "side": side,
                                    "entry": ack.avg_price or signal.entry,
                                    "qty": qty, "entry_time": bar.ts,
                                    "em": em, "hold_bars": 0}
                                trades_today += 1

            result.equity_curve.append((bar.ts, self.broker.get_account().equity))

        result.final_equity = self.broker.get_account().equity
        result.metrics = compute_metrics(result.trades, result.final_equity,
                                         self.spec.default_capital)
        return result

    def _state(self, bars, series, timeframe: str):
        ms = build_market_state(self.instrument, bars, timeframe, series=series,
                                now=bars[-1].ts)
        return build_context({timeframe: ms}, timeframe)
