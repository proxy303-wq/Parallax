"""PARALLAX Telegram chatbot — chattable + AI (DeepSeek) embedded.

Read-only by design: /status /pnl /journal /positions /help are answered from
the journal store, and any free-text message is routed to DeepSeek with the
live system state as context.  It can never place an order or mutate state.
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

    def _status(self) -> str:
        cap = self.store.capital()
        summ = self.store.summary()
        pos = self.store.positions()
        lines = ["PARALLAX status",
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
        return f"Total P&L Rs{s['total_pnl']:+,.0f} across {s['n_trades']} trades."

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

    def _context(self) -> str:
        s = self.store.summary()
        cap = self.store.capital()
        pos = self.store.positions()
        trades = self.store.trades(limit=8)
        recent = "; ".join(f"{t['strategy']} {t['side']} {t['pnl']:+.0f}" for t in trades) or "none"
        return (f"equity Rs{cap['equity']:,.0f}, P&L Rs{s['total_pnl']:+,.0f}, "
                f"positions {pos or 'none'}, recent trades {recent}")

    def _ai(self, question: str) -> str:
        if not self.ai.enabled:
            return "AI not configured (DEEPSEEK_API_KEY). Use /status /pnl /journal /positions /help."
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

    def handle(self, text: str) -> str:
        t = (text or "").strip().lower()
        if t in ("/start", "/help", "help", "hi", "hello"):
            return NL.join([
                "PARALLAX assistant (read-only)",
                "/status - capital + P&L",
                "/pnl - P&L",
                "/journal - recent trades",
                "/positions - open positions",
                "/help - this list",
                "Or ask me anything about the system.",
            ])
        if t in ("/status", "status"):
            return self._status()
        if t in ("/pnl", "pnl"):
            return self._pnl()
        if t in ("/journal", "journal", "trades"):
            return self._journal()
        if t in ("/positions", "positions", "pos"):
            return self._positions()
        return self._ai(text)

    def run(self, poll_timeout: int = 30) -> None:
        print("PARALLAX Telegram chatbot online (Ctrl+C to stop)")
        while True:
            try:
                updates = self.tg.get_updates(self.offset, poll_timeout)
                for upd in updates:
                    self.offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    chat_id = (msg.get("chat") or {}).get("id")
                    text = msg.get("text") or ""
                    if chat_id and text:
                        self.tg.send_to(chat_id, self.handle(text))
            except Exception as e:
                print("chatbot error:", type(e).__name__, str(e)[:120])
                time.sleep(5)


if __name__ == "__main__":
    TelegramChatbot().run()
