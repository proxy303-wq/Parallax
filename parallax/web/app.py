"""PARALLAX dashboard — FastAPI web UI.

Three pages (home / futures / options) plus a JSON API, all reading from the
shared SQLite journal store.  Run with:  uvicorn parallax.web.app:app
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from parallax.web.store import JournalStore

app = FastAPI(title="PARALLAX", docs_url=None, redoc_url=None)
store = JournalStore()

CSS = """
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 16px}.nav{display:flex;gap:8px;margin-bottom:20px}
.nav a{color:#58a6ff;text-decoration:none;padding:8px 14px;border:1px solid #30363d;border-radius:6px}
.nav a.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.grid{display:flex;gap:16px;flex-wrap:wrap}.stat{flex:1;min-width:150px}
.stat .k{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.stat .v{font-size:28px;font-weight:600}.pos{color:#3fb950}.neg{color:#f85149}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:500}.tag{padding:2px 8px;border-radius:10px;font-size:11px}
.fut{background:#1f6feb22;color:#58a6ff}.opt{background:#8957e522;color:#bc8cff}
"""


def _fmt(x, dec=0):
    return f"{x:,.{dec}f}" if isinstance(x, (int, float)) else str(x)


def _page(title, active, body):
    nav = "".join(
        f'<a href="{h}" class="{"active" if a == active else ""}">{t}</a>'
        for t, h, a in (("Home", "/", "home"), ("Futures", "/futures", "futures"),
                        ("Options", "/options", "options"),
                        ("Crypto", "/crypto", "crypto")))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · PARALLAX</title><style>{CSS}</style></head>
<body><h1>⚡ PARALLAX</h1><div class="nav">{nav}</div>{body}</body></html>"""


def _trade_rows(trades):
    if not trades:
        return "<p style='color:#8b949e'>No trades recorded yet.</p>"
    rows = "".join(
        f"<tr><td>{t['ts'][:16].replace('T',' ')}</td>"
        f"<td><span class='tag {'fut' if t['strategy']=='futures' else 'opt'}'>{t['strategy']}</span></td>"
        f"<td>{t['instrument']}</td><td>{t['side']}</td><td>{t['qty']:.0f}L</td>"
        f"<td>{t['entry']:.0f} → {t['exit']:.0f}</td>"
        f"<td class='{'pos' if t['pnl']>0 else 'neg'}'>{t['pnl']:+,.0f}</td>"
        f"<td>{t['outcome']}</td></tr>"
        for t in trades)
    return f"<table><tr><th>Time</th><th>Strategy</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Entry → Exit</th><th>P&amp;L</th><th>Outcome</th></tr>{rows}</table>"


def _stat_cards(cap, summ):
    total = summ["total_pnl"]
    cls = "pos" if total >= 0 else "neg"
    cards = f"""
    <div class="grid">
      <div class="stat"><div class="k">Equity</div><div class="v">₹{_fmt(cap['equity'])}</div></div>
      <div class="stat"><div class="k">Available</div><div class="v">₹{_fmt(cap['available'])}</div></div>
      <div class="stat"><div class="k">Margin Used</div><div class="v">₹{_fmt(cap['margin_used'])}</div></div>
      <div class="stat"><div class="k">Total P&amp;L</div><div class="v {cls}">₹{total:+,.0f}</div></div>
      <div class="stat"><div class="k">Trades</div><div class="v">{summ['n_trades']}</div></div>
    </div>"""
    for s, d in summ["by_strategy"].items():
        wc = "pos" if d["pnl"] >= 0 else "neg"
        cards += f"""<div class="card"><h3>{s}</h3>
        <div class="grid">
          <div class="stat"><div class="k">P&amp;L</div><div class="v {wc}">₹{d['pnl']:+,.0f}</div></div>
          <div class="stat"><div class="k">Win rate</div><div class="v">{d['win_rate']:.0%}</div></div>
          <div class="stat"><div class="k">Trades</div><div class="v">{d['n']}</div></div>
        </div></div>"""
    return cards


@app.get("/", response_class=HTMLResponse)
def home():
    cap = store.capital()
    summ = store.summary()
    trades = store.trades(limit=15)
    body = (_stat_cards(cap, summ)
            + "<div class='card'><h3>Trade Journal (recent)</h3>" + _trade_rows(trades) + "</div>")
    return _page("Home", "home", body)


@app.get("/futures", response_class=HTMLResponse)
def futures():
    trades = store.trades(strategy="futures", limit=100)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    pnl = sum(t["pnl"] for t in trades)
    body = (f"<div class='card'><h3>NIFTY Futures · ICT scalper</h3>"
            f"<div class='grid'>"
            f"<div class='stat'><div class='k'>P&amp;L</div><div class='v {'pos' if pnl>=0 else 'neg'}'>₹{pnl:+,.0f}</div></div>"
            f"<div class='stat'><div class='k'>Win rate</div><div class='v'>{wins/len(trades) if trades else 0:.0%}</div></div>"
            f"<div class='stat'><div class='k'>Trades</div><div class='v'>{len(trades)}</div></div>"
            f"</div></div>"
            + "<div class='card'><h3>Journal</h3>" + _trade_rows(trades) + "</div>")
    return _page("Futures", "futures", body)


@app.get("/options", response_class=HTMLResponse)
def options():
    trades = store.trades(strategy="options", limit=100)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    pnl = sum(t["pnl"] for t in trades)
    body = (f"<div class='card'><h3>NIFTY 0DTE · Hedged short strangle (sell)</h3>"
            f"<div class='grid'>"
            f"<div class='stat'><div class='k'>P&amp;L</div><div class='v {'pos' if pnl>=0 else 'neg'}'>₹{pnl:+,.0f}</div></div>"
            f"<div class='stat'><div class='k'>Win rate</div><div class='v'>{wins/len(trades) if trades else 0:.0%}</div></div>"
            f"<div class='stat'><div class='k'>Trades</div><div class='v'>{len(trades)}</div></div>"
            f"</div></div>"
            + "<div class='card'><h3>Journal</h3>" + _trade_rows(trades) + "</div>")
    return _page("Options", "options", body)


@app.get("/crypto", response_class=HTMLResponse)
def crypto():
    trades = store.trades(strategy="crypto", limit=100)
    cards = ""
    try:
        from parallax.adapters.broker.delta import DeltaBroker
        d = DeltaBroker(dry_run=True)
        for sym, label in (("BTC", "BTC/USD"), ("XAUT", "XAUT/USD")):
            q = d.get_quote(f"DELTA:{sym}")
            cards += (f'<div class="stat"><div class="k">{label}</div>'
                      f'<div class="v">{q.last:,.0f}</div>'
                      f'<div class="k">bid {q.bid:,.0f} / ask {q.ask:,.0f}</div></div>')
    except Exception:
        cards = ('<div class="stat"><div class="k">BTC/USD</div>'
                 '<div class="v">-</div></div>'
                 '<div class="stat"><div class="k">XAUT/USD</div>'
                 '<div class="v">-</div></div>')
    body = (f'<div class="card"><h3>Crypto (Delta) · paper</h3>'
            f'<div class="grid">{cards}</div>'
            f'<p style="color:#8b949e">Strategy engine coming later - page reserved for BTC + XAUTUSD.</p></div>'
            + '<div class="card"><h3>Journal</h3>' + _trade_rows(trades) + '</div>')
    return _page("Crypto", "crypto", body)


@app.get("/api/state")
def api_state():
    return JSONResponse({
        "capital": store.capital(),
        "summary": store.summary(),
        "positions": store.positions(),
        "trades": store.trades(limit=200),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
