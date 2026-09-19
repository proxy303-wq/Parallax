"""FINAL production backtest: 0DTE condor, 3 lots, real premiums, TP/SL, IV-rank filter.

NIFTY weekly (Tuesday) expiry.  Sells ATM-2/+2 wings + ATM-4/+4 hedges at the
9:20 open, 3 lots.  Entry credit = REAL traded premium.  Skips the trade unless
entry IV > realised vol (positive vol-risk-premium filter).  Manages intraday
with TP@50% credit / SL@2x credit via Black-Scholes re-pricing on the real IV
path.  Real spot for the expiry payoff.
"""
import json, urllib.request, time as _t, datetime, math, statistics
from collections import OrderedDict
from parallax.adapters.broker.dhan_auth import resolve_token
from parallax.adapters.env import env
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.core.options import optmath as om

cid = env('DHAN_CLIENT_ID')
tok, _ = resolve_token(cid, env('DHAN_ACCESS_TOKEN'), env('DHAN_PIN'), env('DHAN_TOTP_SECRET'))

def fetch(strike, otype, f, t):
    body = {'exchangeSegment':'NSE_FNO','interval':'5','securityId':13,'instrument':'OPTIDX','expiryFlag':'WEEK','expiryCode':1,'strike':strike,'drvOptionType':otype,'requiredData':['close','spot','strike','iv'],'fromDate':f,'toDate':t}
    for attempt in range(4):
        try:
            req = urllib.request.Request('https://api.dhan.co/v2/charts/rollingoption', data=json.dumps(body).encode(), headers={'Accept':'application/json','Content-Type':'application/json','access-token':tok}, method='POST')
            with urllib.request.urlopen(req, timeout=40) as resp:
                d = json.loads(resp.read().decode())
            data = d.get('data') or {}
            return data.get('ce' if otype == 'CALL' else 'pe') or {}
        except Exception:
            if attempt == 3: raise
            _t.sleep(1.5*(attempt+1))
    return {}

windows = [('2026-06-10','2026-07-10'), ('2026-07-10','2026-08-09'), ('2026-08-09','2026-09-10')]
series = {}
for off in ('ATM-4','ATM-2','ATM+2','ATM+4'):
    for ot in ('CALL','PUT'):
        series[(off,ot)] = {}
        for f,t in windows:
            ce = fetch(off, ot, f, t)
            ts = ce.get('timestamp') or []
            st = ce.get('strike') or []
            cl = ce.get('close') or []
            sp = ce.get('spot') or []
            iv = ce.get('iv') or []
            for i,tsv in enumerate(ts):
                if i>=len(cl) or i>=len(sp): break
                ivv = float(iv[i]) if i<len(iv) and iv[i] else 0.0
                if ivv > 1.0: ivv /= 100.0
                series[(off,ot)][int(tsv)] = (float(st[i]) if i<len(st) else 0.0, float(cl[i]), float(sp[i]), ivv)
            _t.sleep(0.3)

# realised vol from underlying daily closes
bars = load_ohlcv(r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv", "5m")
dmap = OrderedDict()
for b in bars: dmap[b.ts.date()] = b.close
dates = list(dmap)
def realized_vol(d):
    prior = [dd for dd in dates if dd <= d]
    if not prior: return 0.0
    idx = dates.index(prior[-1])
    hist = list(dmap.values())[max(0,idx-20):idx+1]
    if len(hist) < 10: return 0.0
    rets = [math.log(hist[i]/hist[i-1]) for i in range(1,len(hist))]
    m = sum(rets)/len(rets); v = sum((r-m)**2 for r in rets)/(len(rets)-1)
    return math.sqrt(v)*math.sqrt(252)

tss = sorted(series[('ATM+2','CALL')])
days = OrderedDict()
for ts in tss:
    d = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=5,minutes=30))).date()
    days.setdefault(d, []).append(ts)

LOTS = 8; LOT = 65

def run_backtest(tp):
    trades = []; skipped = []
    for d in days:
        if d.weekday() != 1: continue
        dbars = days[d]
        if len(dbars) < 60: continue
        e_ts = dbars[1]; x_ts = dbars[-1]
        def g(off, ot, ts): return series[(off,ot)].get(ts)
        sp_l = g('ATM-2','PUT',e_ts); sc_l = g('ATM+2','CALL',e_ts)
        hp_l = g('ATM-4','PUT',e_ts); hc_l = g('ATM+4','CALL',e_ts)
        if not (sp_l and sc_l and hp_l and hc_l): continue
        sp_k, sp_p = sp_l[0], sp_l[1]; sc_k, sc_p = sc_l[0], sc_l[1]
        hp_k, hp_p = hp_l[0], hp_l[1]; hc_k, hc_p = hc_l[0], hc_l[1]
        credit = sp_p + sc_p - hp_p - hc_p
        entry_iv = sc_l[3] if sc_l[3] > 0 else 0.14
        rv = realized_vol(d)
        if rv > 0 and entry_iv <= rv:
            skipped.append((str(d), entry_iv, rv)); continue
        legs = [(sp_k,'p',-1),(sc_k,'c',-1),(hp_k,'p',1),(hc_k,'c',1)]
        outcome = 'expiry'; pnl_pts = None
        last_ts = dbars[-1]
        for ts in dbars[2:]:
            entry = series[('ATM+2','CALL')].get(ts)
            if entry is None: continue
            spot_t, iv_t = entry[2], entry[3]
            if iv_t <= 0: iv_t = entry_iv
            T_t = max(0.0, (last_ts - ts) / (365*24*3600))
            val = sum(s * om.bs_price(spot_t, k, T_t, iv_t, f) for k, f, s in legs)
            pnl = credit - val
            if pnl >= tp * credit:
                outcome = 'tp'; pnl_pts = tp * credit; break
            if pnl <= -2.0 * credit:
                outcome = 'sl'; pnl_pts = -2.0 * credit; break
        if pnl_pts is None:
            spot_exp = series[('ATM+2','CALL')].get(x_ts, (0,0,0,0))[2]
            pnl_pts = (credit - max(0.0, sp_k-spot_exp) - max(0.0, spot_exp-sc_k)
                       + max(0.0, hp_k-spot_exp) + max(0.0, spot_exp-hc_k))
        trades.append({"date":str(d),"outcome":outcome,"iv0":round(entry_iv,3),"rv":round(rv,3),
                       "credit":round(credit,2),"pnl":round(pnl_pts*LOT*LOTS,0)})
    return trades, skipped

for tp in (0.5, 0.75):
    trades, skipped = run_backtest(tp)
    wins = [t for t in trades if t["pnl"]>0]
    tot = sum(t["pnl"] for t in trades)
    outcomes = {}
    for t in trades: outcomes[t['outcome']] = outcomes.get(t['outcome'],0)+1
    print(f"=== TP@{tp:.0%} | traded={len(trades)} skipped={len(skipped)} win={len(wins)/len(trades) if trades else 0:.3f} TOTAL=Rs{tot:,.0f} outcomes={outcomes} ===")
    for t in trades:
        print(f"  {t['date']} {t['outcome']:6s} iv={t['iv0']:.0%} rv={t['rv']:.0%} credit={t['credit']} pnl=Rs{t['pnl']:,.0f}")
    print()
