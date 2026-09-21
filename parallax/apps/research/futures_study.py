"""Futures leg study: can the NIFTY ICT entry be improved?

Built for honest experimentation, which means two things:

  1. CAUSAL. A signal is detected on the close of bar i and can only be
     filled from bar i+1 on - at a resting limit, or at the next open.
     Nothing after bar i decides anything at bar i.

  2. SEPARATED. Signals depend only on the bars, so they are scanned once
     and then replayed cheaply under many entry/exit/filter variants. Every
     variant is reported on a TRAIN window and a TEST window separately;
     anything that only helps in one of them is noise.

Usage:
    python -m parallax.apps.research.futures_study            # baseline + variants
    python -m parallax.apps.research.futures_study scan       # scan only, cache
"""
from __future__ import annotations

import datetime
import os
import pickle
import sys

from parallax.adapters.broker import PaperBroker
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.contracts import (
    Bar, InstrumentId, InstrumentType, OrderIntent, OrderStatus, OrderType, Side,
    ValidatedOrderIntent, spec_for,
)
from parallax.core.context import build_context
from parallax.core.execution import ExitConfig, ExitManager
from parallax.core.ict import ICTConfig, detect
from parallax.core.perception import bars_per_year, build_market_state
from parallax.core.perception import indicators as ind

CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"
SYMBOL = "NIFTY"
LOTS = 3
LOOKBACK = 600
MIN_WARMUP = 120
POINT = 65.0
GATE_TOL = 0.002
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_signals.pkl")

TRAIN_FROM = datetime.date(2024, 8, 26)
TRAIN_TO = datetime.date(2026, 2, 1)
TEST_FROM = datetime.date(2026, 2, 1)
TEST_TO = datetime.date(2026, 9, 12)


def instrument():
    return InstrumentId(SYMBOL, InstrumentType.INDEX_FUTURE, exchange="NSE")


def resample(bars, factor=3):
    """Aggregate 5m bars into 15m (factor=3) without crossing a session.

    The point of testing this: friction on NIFTY futures is ~3 bp round trip.
    A 5m ICT stop is ~30 points on 24,000 = 12 bp, so costs eat ~25% of the
    risk and the repo's own cost-to-vol gate rejects most signals.  On 15m the
    stop is 2-3x wider, so THE SAME edge would survive friction far better.
    """
    out, cur = [], []

    def flush(g):
        if not g:
            return
        out.append(Bar(ts=g[0].ts, open=g[0].open,
                       high=max(b.high for b in g), low=min(b.low for b in g),
                       close=g[-1].close, volume=sum(b.volume for b in g)))
    for b in bars:
        if cur and b.ts.date() != cur[-1].ts.date():
            flush(cur)
            cur = []
        cur.append(b)
        if len(cur) == factor:
            flush(cur)
            cur = []
    flush(cur)
    return out


def _state(inst, wb, ws, timeframe="5m"):
    ms = build_market_state(inst, wb, timeframe, series=ws, now=wb[-1].ts)
    return build_context({timeframe: ms}, timeframe)


# ---------------------------------------------------------------------------
# 1. signal scan (the expensive part, done once)
# ---------------------------------------------------------------------------

def scan_signals(bars, require_touch=True, timeframe="5m"):
    """Every causal ICT signal in the file: {bar_index: ICTSignal}."""
    inst = instrument()
    cfg = ICTConfig(require_touch=require_touch)
    closes = [b.close for b in bars]
    series_all = ind.precompute(closes, [b.high for b in bars],
                                [b.low for b in bars], [b.volume for b in bars],
                                bars_per_year(timeframe))
    out = {}
    for i in range(MIN_WARMUP, len(bars) - 1):
        start = max(0, i - LOOKBACK + 1)
        wb = bars[start:i + 1]
        if len(wb) < MIN_WARMUP:
            continue
        ws = {k: v[start:i + 1] for k, v in series_all.items()}
        sig = detect(_state(inst, wb, ws, timeframe), wb, cfg)
        if sig is not None:
            out[i] = sig
        if i % 5000 == 0:
            print("  scanned %d bars, %d signals" % (i, len(out)), flush=True)
    return out


def load_signals(bars, require_touch=True, tag="5m"):
    key = ("touch" if require_touch else "notouch") + "_" + tag
    path = CACHE.replace(".pkl", "_%s.pkl" % key)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        if d.get("n") == len(bars):
            print("loaded %d signals from cache" % len(d["sig"]), flush=True)
            return d["sig"]
    print("scanning signals (require_touch=%s, tf=%s) ..." % (require_touch, tag),
          flush=True)
    sig = scan_signals(bars, require_touch, timeframe=tag)
    with open(path, "wb") as fh:
        pickle.dump({"n": len(bars), "sig": sig}, fh)
    print("scanned %d signals" % len(sig), flush=True)
    return sig


# ---------------------------------------------------------------------------
# 2. replay (cheap, re-run for every variant)
# ---------------------------------------------------------------------------

def replay(bars, sigmap, i0, i1, *, model="limit", gate=True, lots=LOTS,
           slip_mult=1.0, fee_mult=1.0, min_rr=None, target_r_cap=None,
           skip_first_bars=0, skip_last_bars=0, exit_cfg=None,
           session_pos=None, session_len=None, long_only=False,
           short_only=False):
    inst = instrument()
    spec = spec_for(SYMBOL)
    broker = PaperBroker(capital=1_000_000.0, point_value=spec.point_value,
                         currency=spec.currency,
                         slippage=spec.slippage * slip_mult,
                         fee_rate=spec.fee_rate * fee_mult)
    instr = str(inst)
    ict_cfg = ICTConfig()
    exit_cfg = exit_cfg or ExitConfig()
    ttl = max(2, int(ict_cfg.retrace_lookback))

    trades = []
    active = None
    pending = None
    st = {"signals": 0, "cost_gated": 0, "price_gated": 0, "filled": 0,
          "expired": 0, "no_room": 0, "time_filtered": 0}

    for i in range(i0, i1 + 1):
        if i >= len(bars):
            break
        bar = bars[i]
        last_of_day = (i == len(bars) - 1) or (bars[i + 1].ts.date() != bar.ts.date())

        if active is not None:
            active["hold"] += 1
            if last_of_day:
                px = active["em"].force_exit(bar.close)
            else:
                px, _ = active["em"].update(bar.high, bar.low)
            if px is not None:
                pnl = broker.close_position(instr, px) or 0.0
                _sg = 1.0 if active["side"] == Side.BUY else -1.0
                _se = active.get("signal_entry", active["entry"])
                _fees = ((_se + px) * active["qty"] * spec.point_value *
                         spec.fee_rate * fee_mult)
                pnl_sig = _sg * (px - _se) * active["qty"] * spec.point_value - _fees
                trades.append({
                    "entry_time": active["entry_time"], "exit_time": bar.ts,
                    "side": active["side"].value,
                    "entry": round(active["entry"], 2), "exit": round(px, 2),
                    "signal_entry": round(_se, 2),
                    "qty": active["qty"], "pnl": round(pnl, 2),
                    "pnl_sig": round(pnl_sig, 2),
                    "r": round(active["em"].r_multiple, 3),
                    "reason": active["em"].exit_reason, "hold": active["hold"],
                })
                active = None

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
                    intent_id="s%d" % i, decision_id="ict", risk_auth_id="study",
                    instrument=instr, side=pending["side"], quantity=float(lots),
                    order_type=OrderType.MARKET, price=fill,
                    idempotency_key="s%d" % i, timestamp=bar.ts), True))
                if ack.status == OrderStatus.FILLED:
                    entry = ack.avg_price or fill
                    _sgn = 1.0 if pending["side"] == Side.BUY else -1.0
                    tgt, stop = pending["target"], pending["stop"]
                    if target_r_cap is not None:
                        risk = abs(entry - stop)
                        capped = entry + _sgn * target_r_cap * risk
                        if _sgn * (capped - tgt) < 0:
                            tgt = capped
                    if _sgn * (tgt - entry) <= 0:
                        st["no_room"] += 1
                        broker.close_position(instr, entry)
                        pending = None
                        continue
                    active = {"side": pending["side"], "entry": entry, "qty": lots,
                              "entry_time": bar.ts, "hold": 0,
                              "signal_entry": pending["entry"],
                              "em": ExitManager(pending["side"], entry, stop,
                                                exit_cfg, target=tgt)}
                    st["filled"] += 1
                    pending = None
            elif model == "limit" and (i - pending["placed_i"]) >= ttl:
                pending = None
                st["expired"] += 1
            elif model == "market" and i > pending["placed_i"] + 1:
                pending = None          # the next bar never came (session ended)
                st["expired"] += 1

        if active is None and pending is None and i in sigmap:
            sig = sigmap[i]
            st["signals"] += 1
            side = Side.BUY if sig.direction == "long" else Side.SELL
            if (long_only and side != Side.BUY) or (short_only and side != Side.SELL):
                continue
            stop_dist = abs(sig.entry - sig.stop)
            rt_cost = 2.0 * (spec.fee_rate * fee_mult + spec.slippage * slip_mult)
            ok = stop_dist > 0 and (rt_cost / (stop_dist / max(sig.entry, 1e-9))) <= 0.2
            if not ok:
                st["cost_gated"] += 1
            if ok and min_rr is not None:
                rr = abs(sig.target - sig.entry) / max(stop_dist, 1e-9)
                if rr < min_rr:
                    ok = False
                    st["cost_gated"] += 1
            if ok and session_pos is not None:
                p_ = session_pos[i]
                n_ = session_len[i]
                if p_ < skip_first_bars or p_ >= (n_ - skip_last_bars):
                    ok = False
                    st["time_filtered"] += 1
            if ok and gate:
                if not (bar.close <= sig.entry * (1 + GATE_TOL) if side == Side.BUY
                        else bar.close >= sig.entry * (1 - GATE_TOL)):
                    ok = False
                    st["price_gated"] += 1
            if ok:
                pending = {"placed_i": i, "side": side, "entry": sig.entry,
                           "stop": sig.stop, "target": sig.target, "ttl": ttl}

    return trades, st


def summarise(name, trades, base=None):
    n = len(trades)
    if not n:
        print("%-30s n=0" % name)
        return 0.0, 0.0, 0.0
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    pf = (gw / gl) if gl else float("inf")
    tot = sum(t["pnl"] for t in trades)
    eq = 0.0; peak = 0.0; dd = 0.0
    for t in trades:
        eq += t["pnl"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    pb = ""
    if base is not None:
        pb = "  %+.0f%%" % (100.0 * (tot - base) / abs(base)) if base else ""
    print("%-30s n=%3d win=%.3f PF=%-6s tot=Rs%9s maxDD=Rs%8s%s" % (
        name, n, len(wins) / n, ("%.2f" % pf) if pf != float("inf") else "inf",
        format(int(tot), ","), format(int(dd), ","), pb))
    return tot, pf, dd


def window(trades, frm, to):
    return [t for t in trades if frm <= t["entry_time"].date() < to]


def main() -> None:
    tf = "5m"
    if len(sys.argv) > 1 and sys.argv[1] in ("5m", "15m"):
        tf = sys.argv[1]
    global MIN_WARMUP, LOOKBACK
    if tf == "15m":
        MIN_WARMUP = 200
    print("loading bars (%s) ..." % tf, flush=True)
    bars = load_ohlcv(CSV, "5m")
    if tf == "15m":
        bars = resample(bars, 3)
    print("bars=%d  %s .. %s" % (len(bars), bars[0].ts, bars[-1].ts), flush=True)

    # session position/length per bar (for time-of-day filters)
    session_pos = {}
    session_len = {}
    k = 0; cur = None; run = []
    for i, b in enumerate(bars):
        if cur is not None and b.ts.date() != cur:
            for j, idx in enumerate(run):
                session_pos[idx] = j; session_len[idx] = len(run)
            run = []
        cur = b.ts.date(); run.append(i)
    for j, idx in enumerate(run):
        session_pos[idx] = j; session_len[idx] = len(run)

    sigmap = load_signals(bars, require_touch=True, tag=tf)
    if tf == "15m":
        print("15m: %d bars, %d signals" % (len(bars), len(sigmap)), flush=True)
    if "scan" in sys.argv:
        return

    i0 = MIN_WARMUP
    i1 = len(bars) - 1

    print()
    print("=== FULL HISTORY %s .. %s ===" % (bars[i0].ts.date(), bars[i1].ts.date()))
    base = None
    for model in ("limit", "market"):
        for gate in (False, True):
            tr, st = replay(bars, sigmap, i0, i1, model=model, gate=gate,
                            session_pos=session_pos, session_len=session_len)
            tag = "baseline %s/gate=%s" % (model, "on" if gate else "off")
            tot, pf, dd = summarise(tag, tr, base)
            if base is None:
                base = tot
            print("      sig=%d cost=%d price=%d time=%d fill=%d exp=%d noroom=%d" % (
                st["signals"], st["cost_gated"], st["price_gated"],
                st["time_filtered"], st["filled"], st["expired"], st["no_room"]))

    print()
    print("=== VARIANTS on identical signals (market entry, live gates) ===")
    print("    train %s..%s | test %s..%s" % (TRAIN_FROM, TRAIN_TO, TEST_FROM, TEST_TO))
    V = [
        ("baseline", {}),
        ("skip first 3 bars", {"skip_first_bars": 3}),
        ("skip last 6 bars", {"skip_last_bars": 6}),
        ("skip first 3 + last 6", {"skip_first_bars": 3, "skip_last_bars": 6}),
        ("skip first 6 bars", {"skip_first_bars": 6}),
        ("min_rr 2.0", {"min_rr": 2.0}),
        ("min_rr 3.0", {"min_rr": 3.0}),
        ("target capped at 2R", {"target_r_cap": 2.0}),
        ("target capped at 3R", {"target_r_cap": 3.0}),
        ("long only", {"long_only": True}),
        ("short only", {"short_only": True}),
        ("limit entry", {"model": "limit"}),
        # exit management.  lock_r 0.5 arms a breakeven stop after only half an
        # R of heat - on 5m noise that is reached constantly, so many trades
        # that would have run are scratched at zero.  Tested on both windows.
        ("exit lock_r 1.0", {"exit_cfg": ExitConfig(lock_r=1.0, trail_r=0.5)}),
        ("exit lock_r 1.5", {"exit_cfg": ExitConfig(lock_r=1.5, trail_r=0.5)}),
        ("exit trail_r 1.0", {"exit_cfg": ExitConfig(lock_r=0.5, trail_r=1.0)}),
        ("exit lock 1.0 trail 1.0",
         {"exit_cfg": ExitConfig(lock_r=1.0, trail_r=1.0)}),
        ("exit lock 1.0 + floor 0.5",
         {"exit_cfg": ExitConfig(lock_r=1.0, floor_r=0.5, trail_r=0.5)}),
        ("no lock, no trail",
         {"exit_cfg": ExitConfig(lock_r=99.0, trail_r=0.0)}),
    ]
    for name, kw in V:
        kw = dict(kw)
        model = kw.pop("model", "market")
        tr, st = replay(bars, sigmap, i0, i1, model=model, gate=True,
                        session_pos=session_pos, session_len=session_len, **kw)
        trn = window(tr, TRAIN_FROM, TRAIN_TO)
        tst = window(tr, TEST_FROM, TEST_TO)
        print("  %s" % name)
        summarise("    train", trn)
        summarise("    test", tst)


if __name__ == "__main__":
    main()
