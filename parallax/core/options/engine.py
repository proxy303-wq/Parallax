"""Index-options decision engine — deterministic sell (short premium) and
buy (directional long) selection for NIFTY / FINNIFTY.

Sell: short strangle (or iron condor spread when sell_use_spreads) at the
sell_delta strikes, chosen via optmath.delta_strike and rounded to the strike
ladder.  Probability of profit = P(spot inside the break-evens at expiry).

Buy: single long call/put at buy_delta in the direction of the bias.

Premium and Greeks come from Black-76 (r=q=0); a live chain can override the
premiums with market prices.  Everything is deterministic and testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass

from parallax.core.options.contracts import (
    OptionContract, OptionLeg, OptionStrategy, OptionsConfig,
)
from parallax.core.options import optmath as om


@dataclass
class ChainContext:
    spot: float
    sigma: float          # decimal vol (IV for live, realised for backtest)
    dte: int
    strike_step: float = 50.0
    expiry: str = ""
    symbol: str = "NIFTY"
    lot_size: int = 0     # 0 = resolve per-contract


class OptionsEngine:
    def __init__(self, config: OptionsConfig | None = None):
        self.config = config or OptionsConfig()

    # ---- leg helpers ----------------------------------------------------
    def _leg(self, ctx: ChainContext, strike: float, option_type: str,
             side: int, qty: int = 1, premium: float | None = None,
             lot_size: int | None = None) -> OptionLeg:
        c = OptionContract(
            symbol=ctx.symbol, strike=round(strike, 2), expiry=ctx.expiry,
            option_type=option_type,
            lot_size=lot_size or ctx.lot_size or 0,
            security_id=0, trading_symbol="", strike_step=ctx.strike_step)
        p = premium if premium is not None else om.bs_price(
            ctx.spot, c.strike, ctx.dte / 365.0, ctx.sigma, c.flag())
        return OptionLeg(contract=c, side=side, quantity=qty, premium=round(p, 2))

    def _strike(self, ctx: ChainContext, target_delta: float, side: str) -> float:
        k = om.delta_strike(ctx.spot, ctx.sigma, ctx.dte, target_delta, side)
        if k is None:
            return ctx.spot
        mode = "floor" if side == "put" else "ceil"
        return om.round_to_step(k, ctx.strike_step, mode)

    # ---- sell: short strangle / iron condor -----------------------------
    def sell_strangle(self, ctx: ChainContext) -> OptionStrategy:
        cfg = self.config
        put_k = self._strike(ctx, cfg.sell_delta, "put")
        call_k = self._strike(ctx, cfg.sell_delta, "call")
        if call_k <= put_k:
            return OptionStrategy(name="short-strangle", reason="strikes invalid")
        legs = [
            self._leg(ctx, put_k, "PE", -1),
            self._leg(ctx, call_k, "CE", -1),
        ]
        if cfg.sell_use_spreads:
            hedge_put = om.round_to_step(
                put_k - cfg.spread_width_steps * ctx.strike_step, ctx.strike_step, "floor")
            hedge_call = om.round_to_step(
                call_k + cfg.spread_width_steps * ctx.strike_step, ctx.strike_step, "ceil")
            legs.append(self._leg(ctx, hedge_put, "PE", 1))
            legs.append(self._leg(ctx, hedge_call, "CE", 1))
        s = OptionStrategy(name="iron-condor" if cfg.sell_use_spreads else "short-strangle",
                           legs=legs, reason=f"short {cfg.sell_delta:.0%} delta wings")
        return self._finalize(s, ctx)

    # ---- buy: directional long ------------------------------------------
    def buy_directional(self, ctx: ChainContext, bias: str) -> OptionStrategy:
        cfg = self.config
        if bias == "bearish":
            k = self._strike(ctx, cfg.buy_delta, "put")
            legs = [self._leg(ctx, k, "PE", 1)]
            name = "long-put"
        else:
            k = self._strike(ctx, cfg.buy_delta, "call")
            legs = [self._leg(ctx, k, "CE", 1)]
            name = "long-call"
        s = OptionStrategy(name=name, legs=legs,
                           reason=f"directional {bias} @ {cfg.buy_delta:.0%} delta")
        return self._finalize(s, ctx)

    # ---- strategy metrics -----------------------------------------------
    def _finalize(self, s: OptionStrategy, ctx: ChainContext) -> OptionStrategy:
        T = ctx.dte / 365.0
        net_premium = 0.0
        net_delta = 0.0
        net_theta = 0.0
        net_vega = 0.0
        shorts = [l for l in s.legs if l.side < 0]
        longs = [l for l in s.legs if l.side > 0]
        for l in s.legs:
            ls = l.contract.lot_size or ctx.lot_size or 1
            # raw LONG greeks; the leg side is applied exactly once below
            g = om.bs_greeks(ctx.spot, l.contract.strike, T, ctx.sigma,
                             l.contract.flag())
            net_premium += l.signed_premium_value()
            net_delta += l.side * l.quantity * ls * g["delta"]
            net_theta += l.side * l.quantity * ls * g["theta_day"]
            net_vega += l.side * l.quantity * ls * g["vega_pct"]

        s.net_premium = round(net_premium, 2)
        s.net_delta = round(net_delta, 3)
        s.net_theta_day = round(net_theta, 2)
        s.net_vega = round(net_vega, 2)

        # break-evens (short premium) and max loss
        if shorts and not longs:
            # naked strangle
            put_k = min(l.contract.strike for l in shorts if l.contract.flag() == "p")
            call_k = max(l.contract.strike for l in shorts if l.contract.flag() == "c")
            credit_pts = net_premium / max(ctx.lot_size, 1)
            s.break_even = (round(put_k - credit_pts, 1), round(call_k + credit_pts, 1))
            s.max_loss = float("inf")
            s.max_profit = net_premium
            s.prob_profit = om.prob_in_band(s.break_even[0], s.break_even[1],
                                            ctx.spot, ctx.sigma, ctx.dte)
        elif longs and not shorts:
            # long single leg
            s.max_loss = -net_premium            # premium paid (net_premium negative)
            s.max_profit = float("inf")
            s.break_even = ()
            s.prob_profit = om.prob_expire_above(
                s.legs[0].contract.strike, ctx.spot, ctx.sigma, ctx.dte)                 if s.legs[0].contract.flag() == "c" else om.prob_expire_below(
                s.legs[0].contract.strike, ctx.spot, ctx.sigma, ctx.dte)
        else:
            # defined-risk spread: width - credit per side
            put_legs = [l for l in s.legs if l.contract.flag() == "p"]
            call_legs = [l for l in s.legs if l.contract.flag() == "c"]
            width = 0.0
            if put_legs:
                ks = sorted(l.contract.strike for l in put_legs)
                width += (ks[-1] - ks[0]) * (put_legs[0].contract.lot_size or ctx.lot_size)
            if call_legs:
                ks = sorted(l.contract.strike for l in call_legs)
                width += (ks[-1] - ks[0]) * (call_legs[0].contract.lot_size or ctx.lot_size)
            s.max_profit = net_premium
            s.max_loss = max(0.0, width - net_premium)
            # break-evens from short strikes +/- credit_pts
            if put_legs:
                short_put = min(l.contract.strike for l in put_legs if l.side < 0)
                s.break_even += (round(short_put - net_premium / max(ctx.lot_size, 1), 1),)
            if call_legs:
                short_call = max(l.contract.strike for l in call_legs if l.side < 0)
                s.break_even += (round(short_call + net_premium / max(ctx.lot_size, 1), 1),)
            lo = min(s.break_even) if s.break_even else -1e18
            hi = max(s.break_even) if s.break_even else 1e18
            s.prob_profit = om.prob_in_band(lo, hi, ctx.spot, ctx.sigma, ctx.dte)

        return s

    # ---- top-level ------------------------------------------------------
    def evaluate(self, ctx: ChainContext, bias: str = "neutral",
                 mode: str = "sell") -> OptionStrategy:
        if mode == "buy":
            return self.buy_directional(ctx, bias if bias in ("bullish", "bearish") else "bullish")
        return self.sell_strangle(ctx)
