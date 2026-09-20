"""STRICT walk-forward: NIFTY futures ICT + NIFTY 0DTE option selling, one book.

Why this exists
---------------
The shipped ICT backtest (ict_backtest.ICTBacktestEngine) detects a signal on
bar i and then fills it at signal.entry ON THAT SAME BAR. The ICT entry is a
70.5% retracement of an impulse whose extreme is set by bar i itself, and
detect() only fires when bar i low already reached that level. So the fill uses
the bar's own range to decide where the bar itself traded - the entry level is
only knowable at the close, by which time the touch is history.

The live runner has the same defect in a worse form: it detects on the closed
bar and then sends a MARKET order while recording the entry as sig.entry, so
the live journal books a price that was never available.

This module replays the identical signal logic with the causality fixed:

    signal detected on the close of bar i
      -> model "limit"  : resting limit at sig.entry, fillable from bar i+1 on,
                          cancelled after retrace_lookback bars.
      -> model "market" : market order filled at the OPEN of bar i+1
                          (closest to what the live runner does today)

Nothing after bar i is ever read to decide anything at bar i.

Sizing is 3 NIFTY lots (the live setting). Costs are the repo model: 0.01% of
notional per side + 2e-6 of price slippage, charged on entry and exit, with a
multiplier to stress them.

Usage:  python -m parallax.apps.research.strict_combo
"""
from __future__ import annotations

import datetime
import sys

from parallax.adapters.broker import PaperBroker
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.contracts import (
    InstrumentId, InstrumentType, OrderIntent, OrderStatus, OrderType, Side,
    ValidatedOrderIntent, spec_for,
)
from parallax.core.context import build_context
from parallax.core.execution import ExitConfig, ExitManager
from parallax.core.ict import ICTConfig, detect
from parallax.core.perception import bars_per_year, build_market_state
from parallax.core.perception import indicators as ind

CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"
SYMBOL = "NIFTY"
LOTS = 3                 # live setting in live_runner
LOOKBACK = 600
MIN_WARMUP = 120
AUG_FROM = datetime.date(2026, 8, 1)      # window start (overridable via argv)
AUG_TO = datetime.date(2026, 9, 1)        # window end (exclusive)
GATE_TOL = 0.002         # live_runner._price_ok tolerance


def instrument() -> InstrumentId:
    return InstrumentId(SYMBOL, InstrumentType.INDEX_FUTURE, exchange="NSE")


def _state(inst, wb, ws, timeframe="5m"):
    ms = build_market_state(inst, wb, timeframe, series=ws, now=wb[-1].ts)
    return build_context({timeframe: ms}, timeframe)


def replay(bars, i0, i1, *, model="limit", gate=True, lots=LOTS,
           slip_mult=1.0, fee_mult=1.0, timeframe="5m"):
    """Causal replay over bars[i0:i1]. Trades RECORDED only if opened in range."""
    inst = instrument()
    spec = spec_for(SYMBOL)
    broker = PaperBroker(capital=1_000_000.0, point_value=spec.point_value,
                         currency=spec.currency,
                         slippage=spec.slippage * slip_mult,
                         fee_rate=spec.fee_rate * fee_mult)
    instr = str(inst)

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]
    series_all = ind.precompute(closes, highs, lows, vols, bars_per_year(timeframe))

    ict_cfg = ICTConfig()
    exit_cfg = ExitConfig()
    ttl = max(2, int(getattr(ict_cfg, "retrace_lookback", 6)))

    trades = []
    active = None
    pending = None
    stats = {"signals": 0, "gated": 0, "filled": 0, "expired": 0, "bars": 0}

    for i in range(i0, i1 + 1):
        if i >= len(bars):
            break
        bar = bars[i]
        stats["bars"] += 1
        last_of_day = (i == len(bars) - 1) or (bars[i + 1].ts.date() != bar.ts.date())

        # ---- 1. manage an open position on THIS bar ------------------------
        if active is not None:
            active["hold"] += 1
            if last_of_day:
                px = active["em"].force_exit(bar.close)
            else:
                px, _ = active["em"].update(bar.high, bar.low)
            if px is not None:
                pnl = broker.close_position(instr, px) or 0.0
                in_window = AUG_FROM <= active["entry_time"].date() < AUG_TO
                if in_window:
                    trades.append({
                        "entry_time": active["entry_time"], "exit_time": bar.ts,
                        "side": active["side"].value, "entry": round(active["entry"], 2),
                        "exit": round(px, 2), "qty": active["qty"],
                        "pnl": round(pnl, 2),
                        "outcome": ("WIN" if pnl > 0 else
                                    ("LOSS" if pnl < 0 else "SCRATCH")),
                        "r": round(active["em"].r_multiple, 3),
                        "reason": active["em"].exit_reason,
                        "hold": active["hold"],
                    })
                active = None

        # ---- 2. work a resting order placed on an EARLIER bar --------------
        if active is None and pending is not None:
            fill = None
            if i > pending["placed_i"]:
                if model == "market":
                    fill = bar.open if i == pending["placed_i"] + 1 else None
                else:
                    if pending["side"] == Side.BUY and bar.low <= pending["entry"]:
                        fill = pending["entry"]
                    elif pending["side"] == Side.SELL and bar.high >= pending["entry"]:
                        fill = pending["entry"]
            if fill is not None:
                ack = broker.place_order(ValidatedOrderIntent(OrderIntent(
                    intent_id="s%d" % i, decision_id="ict", risk_auth_id="strict",
                    instrument=instr, side=pending["side"], quantity=float(lots),
                    order_type=OrderType.MARKET, price=fill,
                    idempotency_key="s%d" % i, timestamp=bar.ts), True))
                if ack.status == OrderStatus.FILLED:
                    entry = ack.avg_price or fill
                    active = {
                        "side": pending["side"], "entry": entry, "qty": lots,
                        "entry_time": bar.ts, "hold": 0,
                        "em": ExitManager(pending["side"], entry, pending["stop"],
                                          exit_cfg, target=pending["target"]),
                    }
                    stats["filled"] += 1
                    pending = None
            elif model == "limit" and (i - pending["placed_i"]) >= ttl:
                pending = None
                stats["expired"] += 1

        # ---- 3. detect on the CLOSED bar i, arm for bar i+1 ----------------
        if active is None and pending is None and i < len(bars) - 1:
            start = max(0, i - LOOKBACK + 1)
            wb = bars[start:i + 1]
            if len(wb) >= MIN_WARMUP:
                ws = {k: v[start:i + 1] for k, v in series_all.items()}
                state = _state(inst, wb, ws, timeframe)
                sig = detect(state, wb, ict_cfg)
                if sig is not None:
                    stats["signals"] += 1
                    side = Side.BUY if sig.direction == "long" else Side.SELL
                    ok = True
                    # cost-to-vol gate, identical to ICTBacktestEngine
                    stop_dist = abs(sig.entry - sig.stop)
                    rt_cost = 2.0 * (spec.fee_rate * fee_mult +
                                     spec.slippage * slip_mult)
                    if stop_dist <= 0 or (rt_cost / (stop_dist / max(sig.entry, 1e-9))) > 0.2:
                        ok = False
                        stats["cost_gated"] = stats.get("cost_gated", 0) + 1
                    if gate and ok:
                        # live_runner._price_ok, with the closed bar close as
                        # the stand-in for the live quote it would have read
                        if not (bar.close <= sig.entry * (1 + GATE_TOL)
                                if side == Side.BUY
                                else bar.close >= sig.entry * (1 - GATE_TOL)):
                            ok = False
                            stats["price_gated"] = stats.get("price_gated", 0) + 1
                    if not ok:
                        pass
                    else:
                        pending = {
                            "placed_i": i, "side": side, "entry": sig.entry,
                            "stop": sig.stop, "target": sig.target, "ttl": ttl,
                        }

    return trades, stats


def summarise(name, trades):
    n = len(trades)
    if not n:
        print("%-26s n=0" % name)
        return 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    pf = (gross_w / gross_l) if gross_l else float("inf")
    total = sum(t["pnl"] for t in trades)
    print("%-26s n=%2d win=%.3f PF=%-5s total=Rs%9s  avg=Rs%7s" % (
        name, n, len(wins) / n, ("%.2f" % pf), format(int(total), ","),
        format(int(total / n), ",")))
    return total


def shipped_comparison(bars, i0, last_aug):
    """Control: the SHIPPED engine on the identical bars.

    It fills on the same bar that set the impulse extreme, and then manages that
    same bar's range - so it books the bar's high as instant MFE. This is the
    number that was quoted as the futures edge; it is not reachable live.
    """
    from parallax.apps.research.ict_backtest import ICTBacktestEngine

    inst = instrument()
    eng = ICTBacktestEngine(inst, fixed_lots=LOTS, warmup=MIN_WARMUP,
                            capital=1_000_000.0)
    slice_ = bars[i0:last_aug + 1]
    res = eng.run(slice_, "5m")
    allt = res.trades
    print("    (all trades in the replay window, before the August filter: %d)" % len(allt))
    tr = [t for t in allt if AUG_FROM <= t.entry_time.date() < AUG_TO]
    n = len(tr)
    if not n:
        print("shipped ICTBacktestEngine   n=0")
        return []
    wins = [t for t in tr if t.pnl > 0]
    gw = sum(t.pnl for t in wins)
    gl = -sum(t.pnl for t in tr if t.pnl < 0)
    tot = sum(t.pnl for t in tr)
    print("shipped ICTBacktestEngine   n=%2d win=%.3f PF=%-5s total=Rs%9s  avg=Rs%7s" % (
        n, len(wins) / n, ("%.2f" % (gw / gl)) if gl else "inf",
        format(int(tot), ","), format(int(tot / n), ",")))
    print("    same-bar fills: %d/%d" % (
        sum(1 for t in tr if t.hold_bars == 0) + sum(1 for t in tr if t.hold_bars == 1),
        n))
    return tr


def main() -> None:
    global AUG_FROM, AUG_TO
    if len(sys.argv) >= 3:
        AUG_FROM = datetime.date.fromisoformat(sys.argv[1])
        AUG_TO = datetime.date.fromisoformat(sys.argv[2])
    print("window %s .. %s" % (AUG_FROM, AUG_TO), flush=True)
    print("loading bars ...", flush=True)
    bars = load_ohlcv(CSV, "5m")
    print("bars=%d  %s .. %s" % (len(bars), bars[0].ts, bars[-1].ts), flush=True)

    idx = [k for k, b in enumerate(bars) if AUG_FROM <= b.ts.date() < AUG_TO]
    if not idx:
        print("no bars in window")
        return
    first_aug, last_aug = idx[0], idx[-1]
    i0 = max(MIN_WARMUP, first_aug - LOOKBACK)
    print("august bars=%d  %s .. %s   loop %d..%d" % (
        len(idx), bars[first_aug].ts, bars[last_aug].ts, i0, last_aug), flush=True)

    print()
    print("=== NIFTY futures ICT, causal replay, %d lots ===" % LOTS)
    results = {}
    for model in ("limit", "market"):
        for gate in (False, True):
            trades, stats = replay(bars, i0, last_aug, model=model, gate=gate)
            tag = "%s/gate=%s" % (model, "on" if gate else "off")
            total = summarise(tag, trades)
            print("    signals=%d cost_gated=%d price_gated=%d filled=%d "
                  "expired=%d  trades=%d" % (
                      stats["signals"], stats.get("cost_gated", 0),
                      stats.get("price_gated", 0),
                      stats["filled"], stats["expired"], len(trades)))
            results[(model, gate)] = (trades, total)

    print()
    print("=== cost stress (limit, gate off) ===")
    for sm in (1.0, 5.0, 10.0):
        tr, _ = replay(bars, i0, last_aug, model="limit", gate=False, slip_mult=sm)
        summarise("slippage x%.0f" % sm, tr)

    print()
    print("=== CONTROL: the shipped (non-causal) engine on the same bars ===")
    shipped_comparison(bars, i0, last_aug)

    for model in ("limit", "market"):
        print()
        print("=== per-trade detail: %s / gate off ===" % model)
        for t in results[(model, False)][0]:
            print("  %s %-4s in=%9.2f out=%9.2f %-8s r=%6.2f Rs%8s -> %s" % (
                t["entry_time"].strftime("%m-%d %H:%M"), t["side"], t["entry"],
                t["exit"], t["reason"][:8], t["r"],
                format(int(t["pnl"]), ","), t["exit_time"].strftime("%m-%d %H:%M")))


if __name__ == "__main__":
    main()
