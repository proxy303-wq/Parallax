"""0DTE NIFTY condor take-profit study on REAL Dhan expired-option data (v2).

v1 fixes:
  * SIGN BUG      - "cost to close" is (short legs) - (hedge legs); v1 had it negated,
                    which inflated loser days into winners (avg_peak 2.0).
  * CLAMP BUG     - interp() clamped out-of-range offsets to the edge strike instead of
                    failing, silently mispricing legs on trend days.
  * NO FALLBACK   - v1 substituted price 0.0 for a missing leg at the close, booking
                    max profit on days whose data was worst.
  * RANGE         - ladder widened ATM+-6 to ATM+-10 (+-500 pts). ATM+-12 returns 0 rows.
  * COVERAGE      - bars used / skipped are reported per trade so the sample is auditable.

Legs: short ATM+-2 (100 pts), long ATM+-4 (200 pts) -> 100-point wings.
Entry 09:20 bar, management from the next bar, exit at session close.
"""
import json, urllib.request, time as _t, datetime, statistics, sys
from collections import OrderedDict
from parallax.adapters.broker.dhan_auth import resolve_token
from parallax.adapters.env import env

cid = env("DHAN_CLIENT_ID")
tok, _ = resolve_token(cid, env("DHAN_ACCESS_TOKEN"), env("DHAN_PIN"), env("DHAN_TOTP_SECRET"))
STEP = 50.0
LOT = 65
LOTS = 8

def fetch(offset, otype, f, t):
    body = {"exchangeSegment":"NSE_FNO","interval":"5","securityId":13,"instrument":"OPTIDX",
            "expiryFlag":"WEEK","expiryCode":1,"strike":offset,"drvOptionType":otype,
            "requiredData":["close","spot","strike"],"fromDate":f,"toDate":t}
    for a in range(4):
        try:
            req = urllib.request.Request("https://api.dhan.co/v2/charts/rollingoption",
                data=json.dumps(body).encode(),
                headers={"Accept":"application/json","Content-Type":"application/json",
                         "access-token":tok}, method="POST")
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode())
            return (d.get("data") or {}).get("ce" if otype == "CALL" else "pe") or {}
        except Exception:
            if a == 3: return {}
            _t.sleep(1.5*(a+1))
    return {}

WINDOWS = [("2026-06-10","2026-07-10"), ("2026-07-10","2026-08-09"), ("2026-08-09","2026-09-10")]
# ATM +-10 (+-500 pts) is the deepest ladder Dhan serves; ATM+-12 returns 0 rows.
OFFS = ["ATM"] + ["ATM%+d" % j for j in range(-10, 11) if j != 0]

ladder = {}
for off in OFFS:
    for ot in ("CALL","PUT"):
        acc = {}
        for f,t in WINDOWS:
            ce = fetch(off, ot, f, t)
            ts = ce.get("timestamp") or []; sp = ce.get("spot") or []; cl = ce.get("close") or []
            for i,tv in enumerate(ts):
                if i >= len(cl) or i >= len(sp): break
                acc[int(tv)] = (float(sp[i]), float(cl[i]))
            _t.sleep(0.2)
        ladder[(off,ot)] = acc
print("fetched %d series, %d bars in ATM series" % (len(ladder), len(ladder[("ATM","CALL")])), flush=True)

byts = {}
for (off,ot), acc in ladder.items():
    n = 0 if off == "ATM" else int(off.replace("ATM","").replace("+",""))
    for ts,(spot,price) in acc.items():
        d = byts.setdefault(ts, {"spot": spot, "CALL": {}, "PUT": {}})
        d[ot][n*STEP] = price

def interp(points, off_pts):
    """Strict: returns None if the offset falls outside the ladder (no clamping)."""
    if not points: return None
    ks = sorted(points)
    if off_pts < ks[0] or off_pts > ks[-1]: return None
    for i in range(len(ks)-1):
        a,b = ks[i], ks[i+1]
        if a <= off_pts <= b:
            if b == a: return points[a]
            w = (off_pts-a)/(b-a)
            return points[a]*(1-w) + points[b]*w
    return points[ks[-1]]

def ist(ts):
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(
        datetime.timezone(datetime.timedelta(hours=5,minutes=30)))

days = OrderedDict()
for ts in sorted(byts):
    days.setdefault(ist(ts).date(), []).append(ts)

def simulate(tp_mode, sl=1.0):
    trades = []
    for d, tss in days.items():
        if d.weekday() != 1 or len(tss) < 60: continue
        ed = byts[tss[1]]; spot0 = ed["spot"]
        atm = round(spot0/STEP)*STEP
        legs = [("sp", atm-2*STEP, "PUT", "short"), ("sc", atm+2*STEP, "CALL", "short"),
                ("hp", atm-4*STEP, "PUT", "long"),  ("hc", atm+4*STEP, "CALL", "long")]
        prices = {}
        ok = True
        for name,k,ot,kind in legs:
            p = interp(ed[ot], k-atm)
            if p is None: ok = False; break
            prices[name] = p
        if not ok:
            continue
        credit = prices["sp"] + prices["sc"] - prices["hp"] - prices["hc"]
        if credit <= 0: continue

        peak = 0.0; outcome = "close"; pnl_pct = None
        used = 0; skipped = 0
        last_prof = None
        for ts in tss[2:]:
            dd = byts[ts]; atm_t = round(dd["spot"]/STEP)*STEP
            val = 0.0; bad = False
            for name,k,ot,kind in legs:
                p = interp(dd[ot], k-atm_t)
                if p is None: bad = True; break
                val += p if kind == "short" else -p
            if bad:
                skipped += 1
                continue
            used += 1
            prof = (credit - val)/credit
            last_prof = prof
            peak = max(peak, prof)
            if prof <= -sl:
                outcome = "sl"; pnl_pct = -sl; break
            if tp_mode == "tp50"  and prof >= 0.50:  outcome = "tp"; pnl_pct = 0.50; break
            if tp_mode == "tp75"  and prof >= 0.75:  outcome = "tp"; pnl_pct = 0.75; break
            if tp_mode == "tp100" and prof >= 0.995: outcome = "tp"; pnl_pct = 1.00; break
            if tp_mode == "ratchet":
                floor = 0.90 if peak >= 0.95 else (0.75 if peak >= 0.80 else (0.50 if peak >= 0.50 else 0.0))
                if floor > 0 and prof <= floor:
                    outcome = "ratchet"; pnl_pct = floor; break
        if pnl_pct is None:
            if last_prof is None:
                outcome = "nodata"; pnl_pct = 0.0
            else:
                pnl_pct = last_prof
        trades.append({"date": str(d), "credit": round(credit,2), "peak": round(peak,3),
                       "outcome": outcome, "pnl_pct": round(pnl_pct,3),
                       "used": used, "skipped": skipped,
                       "pnl_inr": round(pnl_pct*credit*LOT*LOTS, 0)})
    return trades

print()
print("=== NIFTY 0DTE condor | REAL Dhan premiums | 8 lots | Jun 16 - Sep 08 2026 ===")
allt = {}
for sl in (1.0, 2.0):
    print()
    print("--- hard stop = %.0fx credit ---" % sl)
    for mode in ("tp50","tp75","tp100","ratchet"):
        tr = simulate(mode, sl)
        allt[(mode,sl)] = tr
        live = [t for t in tr if t["outcome"] != "nodata"]
        tot = sum(t["pnl_inr"] for t in live)
        wins = [t for t in live if t["pnl_inr"] > 0]
        avg_peak = statistics.mean([t["peak"] for t in live]) if live else 0
        outs = {}
        for t in live: outs[t["outcome"]] = outs.get(t["outcome"],0)+1
        cov = statistics.mean([t["used"]/(t["used"]+t["skipped"]) for t in live]) if live else 0
        print("%-8s n=%2d win=%.3f total=Rs%9s avg_peak=%.2f cov=%.2f %s" % (
            mode, len(live), len(wins)/len(live) if live else 0, format(int(tot), ","),
            avg_peak, cov, outs))

print()
print("=== per-expiry detail (stop 1x credit) ===")
print("%-11s %-7s %s" % ("date", "credit", "  ".join("%9s" % m for m in ("tp50","tp75","tp100","ratchet"))))
base = {t["date"]: t for t in allt[("tp50",1.0)]}
for t in allt[("ratchet",1.0)]:
    row = [allt[(m,1.0)] for m in ("tp50","tp75","tp100","ratchet")]
    cells = []
    for arr in row:
        m = [x for x in arr if x["date"] == t["date"]]
        cells.append("%9s" % format(int(m[0]["pnl_inr"]) if m else 0, ","))
    print("%-11s %-7.1f %s" % (t["date"], t["credit"], "  ".join(cells)))

print()

print()
print("=== per-trade path detail (ratchet, stop 1x credit) ===")
for t in allt[("ratchet",1.0)]:
    print("  %s credit=%5.1f peak=%.2f exit=%-8s bars_used=%-3d pnl=Rs%s" % (
        t["date"], t["credit"], t["peak"], t["outcome"], t["used"],
        format(int(t["pnl_inr"]), ",")))
