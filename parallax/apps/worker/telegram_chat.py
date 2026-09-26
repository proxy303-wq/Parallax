"""PARALLAX Telegram chatbot - chattable, AI-embedded, with inline menus.

Read-only chat plus one controlled action: the PAPER/LIVE mode toggle (which
gates whether the Dhan broker places real orders).  Buttons use inline
keyboards; free text routes to DeepSeek with the live system state as context.
"""
from __future__ import annotations

import time

from parallax.web.store import JournalStore
from parallax.adapters.telegram import TelegramBot
from parallax.core.brain.llm import DeepSeekClient

NL = chr(10)


class TelegramChatbot:
    def __init__(self):
        self.tg = TelegramBot()
        self.store = JournalStore()
        self.ai = DeepSeekClient()
        self.offset = 0

    # ---- deterministic state -------------------------------------------
    def _status(self) -> str:
        cap = self.store.capital()
        summ = self.store.summary()
        pos = self.store.positions()
        lines = [f"PARALLAX status [{self.store.mode().upper()}]",
                 f"Equity Rs{cap['equity']:,.0f} | Available Rs{cap['available']:,.0f} | Margin Rs{cap['margin_used']:,.0f}",
                 f"Total P&L Rs{summ['total_pnl']:+,.0f} ({summ['n_trades']} trades)"]
        for s, d in summ["by_strategy"].items():
            lines.append(f"  {s}: Rs{d['pnl']:+,.0f} ({d['win_rate']:.0%} win, {d['n']} trades)")
        if pos:
            lines.append("Open: " + "; ".join(
                f"{p['instrument']} {p['side']} {p['qty']} @ {p['entry']}" for p in pos))
        else:
            lines.append("Open positions: none")
        return NL.join(lines)

    def _pnl(self) -> str:
        s = self.store.summary()
        lines = [f"Total P&L Rs{s['total_pnl']:+,.0f} across {s['n_trades']} trades."]
        for k, d in s["by_strategy"].items():
            lines.append(f"{k}: Rs{d['pnl']:+,.0f} ({d['win_rate']:.0%} win)")
        return NL.join(lines)

    def _journal(self) -> str:
        trades = self.store.trades(limit=10)
        if not trades:
            return "No trades recorded yet."
        lines = ["Recent trades:"]
        for t in trades:
            lines.append(f"  {t['ts'][:16].replace('T', ' ')} {t['strategy']} {t['side']} {t['pnl']:+,.0f} ({t['outcome']})")
        return NL.join(lines)

    def _positions(self) -> str:
        pos = self.store.positions()
        if not pos:
            return "No open positions."
        return NL.join(
            f"{p['instrument']} {p['side']} {p['qty']} @ {p['entry']} (stop {p['stop']}, tgt {p['target']})"
            for p in pos)

    def _strategies(self) -> str:
        s = self.store.summary()
        lines = ["Strategies:"]
        lines.append("  futures - NIFTY ICT scalper, 3 lots, intraday")
        lines.append("  options - index 0DTE condor, hold to expiry, no stop")
        lines.append("  crypto  - BTC/XAUTUSD, coming soon")
        lines.append(f"P&L by strategy: {s['by_strategy']}")
        return NL.join(lines)

    def _mode(self) -> str:
        return f"Current mode: {self.store.mode().upper()}"

    # ---- AI --------------------------------------------------------------
    def _context(self) -> str:
        s = self.store.summary()
        cap = self.store.capital()
        pos = self.store.positions()
        trades = self.store.trades(limit=8)
        recent = "; ".join(f"{t['strategy']} {t['side']} {t['pnl']:+.0f}" for t in trades) or "none"
        return (f"mode {self.store.mode()}, equity Rs{cap['equity']:,.0f}, "
                f"P&L Rs{s['total_pnl']:+,.0f}, positions {pos or 'none'}, "
                f"recent trades {recent}")

    def _ai(self, question: str) -> str:
        if not self.ai.enabled:
            return "AI not configured. Use /menu for options."
        messages = [
            {"role": "system",
             "content": ("You are the PARALLAX trading assistant. Answer concisely "
                         "(under ~150 words) using ONLY the provided system state. "
                         "Never invent numbers. Never give financial advice. "
                         "State: " + self._context())},
            {"role": "user", "content": question},
        ]
        ans = self.ai.complete(messages, temperature=0.3, max_tokens=500)
        return ans or "AI unavailable right now - try again."

    # ---- menus -----------------------------------------------------------
    def _keyboard(self) -> dict:
        mode = self.store.mode()
        nxt = "PAPER" if mode == "live" else "LIVE"
        return {"inline_keyboard": [
            [{"text": "Status", "callback_data": "status"},
             {"text": "PnL", "callback_data": "pnl"}],
            [{"text": "Journal", "callback_data": "journal"},
             {"text": "Positions", "callback_data": "positions"}],
            [{"text": "Strategies", "callback_data": "strategies"},
             {"text": "Help", "callback_data": "help"}],
            [{"text": f"Mode: {mode.upper()} -> switch to {nxt}", "callback_data": "toggle_mode"}],
        ]}

    def _menu_text(self) -> str:
        return (f"PARALLAX menu [{self.store.mode().upper()}]" + NL +
                "Tap a button, or just type a question.")

    def handle(self, text: str) -> tuple:
        t = (text or "").strip().lower()
        if t in ("/start", "/menu", "/help", "help", "hi", "hello", "menu"):
            return self._menu_text(), self._keyboard()
        if t in ("/status", "status"):
            return self._status(), self._keyboard()
        if t in ("/pnl", "pnl"):
            return self._pnl(), self._keyboard()
        if t in ("/journal", "journal", "trades"):
            return self._journal(), self._keyboard()
        if t in ("/positions", "positions", "pos"):
            return self._positions(), self._keyboard()
        if t in ("/strategies", "strategies"):
            return self._strategies(), self._keyboard()
        if t in ("/mode", "mode"):
            return self._mode(), self._keyboard()
        if t in ("/live", "live"):
            self.store.set_mode("live")
            return "Mode set to LIVE - real Dhan orders will be placed.", self._keyboard()
        if t in ("/paper", "paper"):
            self.store.set_mode("paper")
            return "Mode set to PAPER - dry-run only, no real orders.", self._keyboard()
        return self._ai(text), self._keyboard()

    def handle_callback(self, data: str) -> tuple:
        if data == "toggle_mode":
            new = "paper" if self.store.mode() == "live" else "live"
            self.store.set_mode(new)
            note = ("LIVE - real Dhan orders will be placed."
                    if new == "live" else "PAPER - dry-run only, no real orders.")
            return f"Mode switched to {new.upper()}. {note}", self._keyboard()
        if data == "status": return self._status(), self._keyboard()
        if data == "pnl": return self._pnl(), self._keyboard()
        if data == "journal": return self._journal(), self._keyboard()
        if data == "positions": return self._positions(), self._keyboard()
        if data == "strategies": return self._strategies(), self._keyboard()
        if data == "help":
            return self._menu_text(), self._keyboard()
        return "Unknown action.", self._keyboard()

    # ---- poll loop -------------------------------------------------------
    def run(self, poll_timeout: int = 30) -> None:
        print("PARALLAX Telegram chatbot online (menus + AI)")
        while True:
            try:
                for upd in self.tg.get_updates(self.offset, poll_timeout):
                    self.offset = upd["update_id"] + 1
                    if "callback_query" in upd:
                        cq = upd["callback_query"]
                        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
                        self.tg.answer_callback(cq.get("id", ""))
                        if chat_id:
                            txt, kb = self.handle_callback(cq.get("data", ""))
                            self.tg.send_to(chat_id, txt, kb)
                        continue
                    msg = upd.get("message") or {}
                    chat_id = (msg.get("chat") or {}).get("id")
                    text = msg.get("text") or ""
                    if chat_id and text:
                        txt, kb = self.handle(text)
                        self.tg.send_to(chat_id, txt, kb)
            except Exception as e:
                print("chatbot error:", type(e).__name__, str(e)[:140])
                time.sleep(5)


if __name__ == "__main__":
    TelegramChatbot().run()
