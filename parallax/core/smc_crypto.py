"""SMC Fair-Value-Gap retest for BTCUSD - live twin of the validated backtest.

Backtest: the btc_jas_compare research folder (REPORT.md sections 13-17).
This module reproduces its signal logic and keeps live and backtest in step.

CAUSALITY - two separate lags, both found empirically and easy to get wrong:

  1. CONFIRMATION. smc.bos_choch() writes its mark at last_positions[-2], so a BOS at
     bar p is only written once the loop has advanced past the NEXT swing. Measured:
     a value read at output index j first becomes final when the array extends to
     j + swing_length + 1. Reading the last index of a growing prefix therefore never
     sees a BOS at all (measured: 0 fires in 420 bars where the full array has 4).

  2. WINDOW STABILITY. The mark is anchored to the end of the array it was given, so a
     SLIDING window makes the marks chase the window edge and they never become
     readable (measured: 0 signals in 900 bars at every window size 300..6000). The
     fix is a window whose END advances but whose rows are only consumed once CONFIRM
     bars have passed - see SMCCrypto.step().

The SIGNAL LAG defaults to 2 * swing_length. Using swing_length alone reads the BOS one
full swing early (a real look-ahead); measured impact on the backtest was negligible
(return +1822 -> +1850 pct, drawdown -18.95 -> -15.03 pct), so conclusions hold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from parallax.adapters.smc import smc
from parallax.config.crypto import SMCConfig, USD_INR, product

# Feature window. Rows are consumed CONFIRM bars behind the window end, so the window
# only has to be long enough for swing detection to be well conditioned.
WINDOW = 400
CONFIRM = 60          # >= swing_length + 1, with margin


# --------------------------------------------------------------------------- features
def atr_wilder(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def features(bars: pd.DataFrame, swing_length: int = 20, lag: int | None = None) -> pd.DataFrame:
    """Causal SMC features (identical to the backtest strat_c.features)."""
    ohlc = bars[["open", "high", "low", "close", "volume"]].copy()
    L = lag if lag is not None else 2 * swing_length
    shl = smc.swing_highs_lows(ohlc, swing_length=swing_length)
    bc = smc.bos_choch(ohlc, shl, close_break=True)
    fv = smc.fvg(ohlc, join_consecutive=False)

    idx = bars.index
    def col(df, name):
        # the library returns a fresh RangeIndex -> re-attach values positionally
        return pd.Series(np.asarray(df[name], dtype=float), index=idx)

    f = pd.DataFrame(index=idx)
    f["bos"] = col(bc, "BOS").shift(L)
    f["choch"] = col(bc, "CHOCH").shift(L)
    f["fvg"] = col(fv, "FVG").shift(1)
    f["fvg_top"] = col(fv, "Top").shift(1)
    f["fvg_bot"] = col(fv, "Bottom").shift(1)
    return f


def signals_limit(bars: pd.DataFrame, feat: pd.DataFrame, swing_length: int = 20,
                  zone: str = "fvg", zone_ttl: int = 150, buffer_atr: float = 0.25,
                  allow_short: bool = True):
    """Reference port of the backtested generator - used for parity tests, not live."""
    close = bars["close"].to_numpy(float)
    atr = atr_wilder(bars, 14).to_numpy(float)
    bos = feat["bos"].to_numpy(float); choch = feat["choch"].to_numpy(float)
    fvv = feat["fvg"].to_numpy(float)
    fvt, fvb = feat["fvg_top"].to_numpy(float), feat["fvg_bot"].to_numpy(float)
    n = len(bars)
    place = np.zeros(n); lmt = np.full(n, np.nan); stp = np.full(n, np.nan)
    bias = 0; z_top = z_bot = np.nan; z_i = -10**9; z_kind = None; z_dir = 0; armed = False
    for i in range(n):
        if bos[i] == 1 or choch[i] == 1: bias = 1
        elif bos[i] == -1 or choch[i] == -1: bias = -1
        new_zone = False
        if fvv[i] == 1 and np.isfinite(fvt[i]):
            z_top, z_bot, z_i, z_kind, z_dir = fvt[i], fvb[i], i, "fvg", 1; new_zone = True
        elif allow_short and fvv[i] == -1 and np.isfinite(fvt[i]):
            z_top, z_bot, z_i, z_kind, z_dir = fvt[i], fvb[i], i, "fvg", -1; new_zone = True
        if new_zone: armed = False
        if z_kind is not None:
            broken = (close[i] < z_bot - 1e-9) if z_dir > 0 else (close[i] > z_top + 1e-9)
            if (i - z_i) > zone_ttl or broken:
                z_top = z_bot = np.nan; z_kind = None; armed = False
        if z_kind is not None and not armed and bias == z_dir and np.isfinite(atr[i]):
            place[i] = z_dir
            lmt[i] = z_top if z_dir > 0 else z_bot
            stp[i] = (z_bot - buffer_atr * atr[i]) if z_dir > 0 else (z_top + buffer_atr * atr[i])
            armed = True
    return (pd.Series(place, index=bars.index), pd.Series(lmt, index=bars.index),
            pd.Series(stp, index=bars.index))


# --------------------------------------------------------------------------- state
@dataclass
class Zone:
    top: float
    bot: float
    idx: int
    direction: int
    kind: str = "fvg"


@dataclass
class PendingOrder:
    side: int
    price: float
    stop: float
    bars_left: int
    placed_bar: int = 0


@dataclass
class Position:
    side: int
    entry: float
    qty: float
    stop: float
    risk: float
    initial_stop: float
    peak: float
    trough: float
    opened_ts: object = None
    opened_bar: int = 0

    def unrealized(self, price: float) -> float:
        return (price - self.entry) * self.qty * self.side


@dataclass
class Decision:
    action: str = "none"        # none | place | fill | exit
    reason: str = ""
    side: int = 0
    price: float = 0.0
    stop: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    r_multiple: float = 0.0
    ts: object = None


class SMCCrypto:
    """Incremental SMC state machine.

    step(bars) is given the WHOLE history each call; only bars past the CONFIRM delay
    are processed, and each bar is processed exactly once. Cost is O(WINDOW) per call
    no matter how long the history grows.
    """

    def __init__(self, cfg: SMCConfig | None = None, window: int = WINDOW,
                 confirm: int = CONFIRM):
        self.cfg = cfg or SMCConfig()
        self.prod = product(self.cfg.symbol)   # contract size differs per symbol
        self.window = window
        self.confirm = confirm
        self.bias = 0
        self.zone: Zone | None = None
        self.armed = False
        self.order: PendingOrder | None = None
        self.position: Position | None = None
        self.bar_count = -1
        self.processed_ts = None
        self.events: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _log(self, msg: str):
        self.events.append(msg)
        if len(self.events) > 300:
            self.events = self.events[-300:]

    def _zone_expired(self, close: float, bar_index: int) -> bool:
        z = self.zone
        if z is None:
            return True
        if (bar_index - z.idx) > self.cfg.zone_ttl:
            return True
        if z.direction > 0 and close < z.bot - 1e-9:
            return True
        if z.direction < 0 and close > z.top + 1e-9:
            return True
        return False

    # -- main ------------------------------------------------------------
    def step(self, bars: pd.DataFrame) -> list[Decision]:
        """Process newly confirmed bars; returns the Decisions produced."""
        c = self.cfg
        n = len(bars)
        if n < self.window:
            return []
        if self.bar_count < 0:
            self.bootstrap(bars)      # one-time warm-up so we start with real state
        win = bars.tail(self.window)
        f = features(win, c.swing_length)
        atr = atr_wilder(win, c.atr_period).to_numpy(float)

        last_confirmable = n - 1 - self.confirm   # rows at/behind this are final
        base = n - self.window                    # absolute index of win row 0
        first = max(self.bar_count + 1, base)
        if first > last_confirmable:
            return []

        out: list[Decision] = []
        for abs_i in range(first, last_confirmable + 1):
            k = abs_i - base
            out.append(self._process(abs_i, win.iloc[k], f.iloc[k], float(atr[k])))
        self.processed_ts = win.index[last_confirmable - base]
        return out

    def _process(self, bar_index: int, row: pd.Series, frow: pd.Series,
                 atr_i: float) -> Decision:
        c = self.cfg
        o = float(row["open"]); h = float(row["high"])
        low = float(row["low"]); cl = float(row["close"])
        ts = row.name
        dec = Decision(ts=ts)
        a = float(atr_i) if np.isfinite(atr_i) else float("nan")
        self.bar_count = bar_index

        # ---- 0. resting limit order ----
        if self.order is not None and self.position is None:
            od = self.order
            hit = (low <= od.price) if od.side > 0 else (h >= od.price)
            if hit:
                fill_px = min(od.price, o) if od.side > 0 else max(od.price, o)
                # ATR floor applied at FILL time, exactly as engine.run() does
                dist = abs(fill_px - od.stop)
                if np.isfinite(a):
                    dist = max(dist, c.stop_atr_floor * a)
                stop = fill_px - od.side * dist
                dec.action = "fill"
                dec.side = od.side
                dec.price = fill_px
                dec.stop = stop
                self.order = None
                self._log("limit filled %.1f side=%d stop=%.1f" % (fill_px, od.side, stop))
                return dec
            od.bars_left -= 1
            if od.bars_left <= 0:
                self._log("limit expired unfilled @%.1f" % od.price)
                self.order = None

        # ---- 1. intrabar stop, then target (stop-first) ----
        if self.position is not None:
            pos = self.position
            if pos.side > 0 and low <= pos.stop:
                return self._exit(dec, pos.stop, "stop")
            if pos.side < 0 and h >= pos.stop:
                return self._exit(dec, pos.stop, "stop")
            if c.target_R:
                tgt = pos.entry + pos.side * c.target_R * abs(pos.entry - pos.initial_stop)
                if pos.side > 0 and h >= tgt:
                    return self._exit(dec, tgt, "target")
                if pos.side < 0 and low <= tgt:
                    return self._exit(dec, tgt, "target")

        # ---- 2. trail AFTER the intrabar check ----
        if self.position is not None and np.isfinite(a):
            pos = self.position
            if pos.side > 0:
                pos.peak = max(pos.peak, h)
                pos.stop = max(pos.stop, pos.peak - c.trail_atr * a)
            else:
                pos.trough = min(pos.trough, low)
                pos.stop = min(pos.stop, pos.trough + c.trail_atr * a)

        # ---- 3. decide (arm a fresh resting order) ----
        if self.position is None and self.order is None:
            bos = float(frow["bos"]) if np.isfinite(frow["bos"]) else 0.0
            choch = float(frow["choch"]) if np.isfinite(frow["choch"]) else 0.0
            if bos == 1 or choch == 1:
                self.bias = 1
            elif bos == -1 or choch == -1:
                self.bias = -1

            fv = float(frow["fvg"]) if np.isfinite(frow["fvg"]) else 0.0
            if fv == 1 or (c.allow_short and fv == -1):
                self.zone = Zone(top=float(frow["fvg_top"]), bot=float(frow["fvg_bot"]),
                                 idx=bar_index, direction=int(fv))
                self.armed = False

            if self.zone is not None and self._zone_expired(cl, bar_index):
                self.zone = None
                self.armed = False

            if (self.zone is not None and not self.armed
                    and self.bias == self.zone.direction and np.isfinite(a)):
                z = self.zone
                limit_px = z.top if z.direction > 0 else z.bot
                if z.direction > 0:
                    raw_stop = z.bot - c.buffer_atr * a
                else:
                    raw_stop = z.top + c.buffer_atr * a
                self.order = PendingOrder(side=z.direction, price=limit_px, stop=raw_stop,
                                          bars_left=c.limit_ttl, placed_bar=bar_index)
                self.armed = True
                dec.action = "place"
                dec.side = z.direction
                dec.price = limit_px
                dec.stop = raw_stop
                self._log("armed limit %.1f stop %.1f dir=%d" % (limit_px, raw_stop, z.direction))
        return dec

    def _exit(self, dec: Decision, price: float, reason: str) -> Decision:
        """P&L in RUPEES.

        pos.qty is in Delta CONTRACTS (0.001 for BTCUSD, 0.01 for ETHUSD) and prices are
        USD, so the move is scaled by this symbol's contract_value and then converted at
        USD_INR.  Omitting either factor mis-states P&L by 1000x and in the wrong currency
        -- the R multiple stays correct because it is a ratio, which is exactly why this
        hid for so long.  Using the WRONG symbol's contract_value keeps the P&L right but
        reports the wrong contract count, so the spec is looked up, never assumed.
        """
        pos = self.position
        self.position = None
        dec.action = "exit"
        dec.reason = reason
        dec.side = pos.side
        dec.exit_price = price
        dec.pnl = ((price - pos.entry) * pos.qty * pos.side
                   * self.prod.contract_value * USD_INR)
        dec.r_multiple = dec.pnl / pos.risk if pos.risk else 0.0
        self._log("exit %s @%.1f pnl=%.2f R=%+.2f" % (reason, price, dec.pnl, dec.r_multiple))
        return dec

    # -- execution helpers -------------------------------------------------
    def open_position(self, side: int, entry: float, qty: float, stop: float,
                      risk: float, ts=None):
        self.position = Position(side=side, entry=entry, qty=qty, stop=stop, risk=risk,
                                 initial_stop=stop, peak=entry, trough=entry,
                                 opened_ts=ts, opened_bar=self.bar_count)

    def size(self, equity_inr: float, entry: float, stop: float) -> float:
        """Risk-based size in Delta contracts (0.001 BTC / 0.01 ETH / 0.001 XAUT).

        equity_inr is the account balance in RUPEES while entry/stop are USD prices, so
        the balance is converted to USD before any division.  Skipping that conversion
        divides INR risk by a USD distance and over-sizes by ~88x.
        """
        dist = abs(entry - stop)
        if dist <= 0 or equity_inr <= 0:
            return 0.0
        equity_usd = equity_inr / USD_INR
        risk_usd = equity_usd * self.cfg.risk_pct
        qty = risk_usd / dist                                  # base units (BTC/ETH)
        qty = min(qty,
                  (equity_usd * self.cfg.max_leverage) / entry,  # leverage cap
                  self.prod.max_notional_usd / entry)            # venue cap
        return max(0.0, float(math.floor(qty / self.prod.contract_value)))

    def bootstrap(self, bars: pd.DataFrame) -> None:
        """One-time warm-up: replay the whole history through the SIGNAL state only
        (bias / zone / armed) so the incremental machine starts with the same state the
        backtest has at that bar. Without this the machine begins cold and emits
        different signals for the first few hundred bars.
        """
        c = self.cfg
        if len(bars) < 2:
            return
        f = features(bars, c.swing_length)
        bias = 0
        z_top = z_bot = float("nan")
        z_i = -10**9; z_kind = None; z_dir = 0; armed = False
        for i in range(len(bars)):
            if f["bos"].iloc[i] == 1 or f["choch"].iloc[i] == 1:
                bias = 1
            elif f["bos"].iloc[i] == -1 or f["choch"].iloc[i] == -1:
                bias = -1
            fv = f["fvg"].iloc[i] if np.isfinite(f["fvg"].iloc[i]) else 0.0
            new_zone = False
            if fv == 1 and np.isfinite(f["fvg_top"].iloc[i]):
                z_top = float(f["fvg_top"].iloc[i]); z_bot = float(f["fvg_bot"].iloc[i])
                z_i = i; z_kind = "fvg"; z_dir = 1; new_zone = True
            elif c.allow_short and fv == -1 and np.isfinite(f["fvg_top"].iloc[i]):
                z_top = float(f["fvg_top"].iloc[i]); z_bot = float(f["fvg_bot"].iloc[i])
                z_i = i; z_kind = "fvg"; z_dir = -1; new_zone = True
            if new_zone:
                armed = False
            if z_kind is not None:
                cl = float(bars["close"].iloc[i])
                broken = (cl < z_bot - 1e-9) if z_dir > 0 else (cl > z_top + 1e-9)
                if (i - z_i) > c.zone_ttl or broken:
                    z_kind = None; armed = False
            if z_kind is not None and not armed and bias == z_dir:
                armed = True
        self.bias = bias
        self.zone = None if z_kind is None else Zone(top=z_top, bot=z_bot, idx=z_i, direction=z_dir)
        self.armed = armed
        self.bar_count = len(bars) - 1
        self.processed_ts = bars.index[-1]
    def snapshot(self) -> dict:
        return dict(bias=self.bias,
                    zone=(None if self.zone is None else
                          dict(top=self.zone.top, bot=self.zone.bot,
                               direction=self.zone.direction, idx=self.zone.idx)),
                    armed=self.armed,
                    order=(None if self.order is None else
                           dict(side=self.order.side, price=self.order.price,
                                stop=self.order.stop, bars_left=self.order.bars_left)),
                    position=(None if self.position is None else
                              dict(side=self.position.side, entry=self.position.entry,
                                   qty=self.position.qty, stop=self.position.stop,
                                   risk=self.position.risk)),
                    bars_processed=self.bar_count,
                    last_bar=str(self.processed_ts) if self.processed_ts is not None else None)