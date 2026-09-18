"""Telegram conversational cockpit (§16).

Production messages are generated from structured system state — the text is
phrased deterministically here; a language model may later rephrase it but it
must never invent state.
"""
from __future__ import annotations

import json
import urllib.request

from parallax.contracts import DecisionClass, MarketState, TradeDecision


class TelegramBot:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        from parallax.adapters.env import env
        # dedicated PARALLAX bot first, fall back to the shared Athena bot
        self.token = (token or env("PARALLAX_TELEGRAM_BOT_TOKEN")
                      or env("TELEGRAM_BOT_TOKEN"))
        self.chat_id = (chat_id or env("PARALLAX_TELEGRAM_CHAT_ID")
                        or env("TELEGRAM_CHAT_ID"))

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    # ---- message formatting (structured state only) ----------------------
    def describe_market(self, state: MarketState) -> str:
        regime = state.regime.regime.value.replace("_", " ").title()
        trend = state.structure.trend.lower()
        bias = state.structure.bias.lower()
        rsi = state.indicators.rsi
        price = state.last_price
        lines = [
            f"Market is in a {trend} structure with a {bias} higher-timeframe bias.",
            f"Regime: {regime}. Price {price:.1f} (RSI {rsi:.0f}).",
        ]
        sell = state.liquidity.nearest_sell_side
        buy = state.liquidity.nearest_buy_side
        if sell is not None:
            lines.append(f"Sell-side liquidity resting below near {sell:.1f}.")
        if buy is not None:
            lines.append(f"Buy-side liquidity resting above near {buy:.1f}.")
        if state.data_quality.healthy:
            lines.append("No trade is justified yet." if regime != "Trending Bullish"
                         and regime != "Trending Bearish" else "Watching for a validated entry.")
        else:
            lines.append(f"Data quality {state.data_quality.status.value} — standing aside.")
        return "\n".join(lines)

    def describe_decision(self, decision: TradeDecision) -> str:
        cls = decision.decision_class.value
        if cls == DecisionClass.WAIT.value:
            return f"NO TRADE. {decision.reasons[0] if decision.reasons else 'Edge threshold not met.'}"
        if cls == DecisionClass.ABORT.value:
            return f"ABORT. {decision.reasons[0] if decision.reasons else 'Operational issue.'}"
        if cls == DecisionClass.EXIT.value:
            return f"EXIT. {decision.reasons[0] if decision.reasons else 'Management trigger.'}"
        rp = decision.risk_plan
        levels = ""
        if rp is not None and rp.stop_loss is not None:
            levels = (f" | entry {rp.entry_price:.1f} stop {rp.stop_loss:.1f} "
                      f"target {rp.target:.1f}")
        return (f"{decision.action.value} {decision.thesis} "
                f"(conf {decision.confidence:.2f}, EV {decision.expected_value:.2f})"
                f"{levels}")

    def describe_summary(self, summary: dict) -> str:
        return ("PARALLAX status\n"
                f"mode={summary.get('mode')} state={summary.get('system_state')} "
                f"equity={summary.get('equity')} daily={summary.get('daily_pnl')}")

    def describe_portfolio(self, rows: list[dict]) -> str:
        """Full PARALLAX feed: one line per instrument/strategy + account.
        rows: [{label, mode, equity, daily_pnl, open_positions, note}]"""
        lines = ["PARALLAX - full system"]
        for r in rows:
            line = (f"[{r.get('label', '?')}] equity={r.get('equity')} "
                    f"daily={r.get('daily_pnl')} open={r.get('open_positions')} "
                    f"({r.get('mode', '?')})")
            if r.get("note"):
                line += " " + str(r["note"])
            lines.append(line)
        return "\n".join(lines)

    # ---- transport -------------------------------------------------------
    def send(self, text: str) -> bool:
        if not (self.token and self.chat_id):
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
