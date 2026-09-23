"""Does rolling the long legs inward actually pay?  Measured, not asserted.

The article proposes: as premium decays 40-50%, roll the BOUGHT legs in toward
the sold legs, keeping the sold legs fixed.  Max loss = (spread - credit), so
shrinking the spread while holding the credit turns the tail into a locked
profit.

What it does not say is what the roll COSTS.  Rolling a long put from K1 up to
K2 is a debit of P(K2) - P(K1), and P(K2) - P(K1) = (K2 - K1) - time_value of
that spread.  So the benefit equals the time value still in the wing, and the
roll is only worth doing while time value remains.

This measures, directly from the ladder:
    debit      = what the roll costs, in points
    risk_cut   = how much max loss it removes, in points
    edge       = risk_cut - debit  (in points per unit)
"""
import pickle, statistics, datetime, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "_ladder_NIFTY_2025-01-05_2026-09-01.pkl")
STEP = 50.0


def load():
    with open(CACHE, "rb") as fh:
        return pickle.load(fh)


def main():
    byts = load()
    days = {}
    for ts in sorted(byts):
        d = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(
            datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
        days.setdefault(d, []).append(ts)
    exp = [d for d in days if d.weekday() == 1]

    # For every expiry, at every bar: what would it cost to pull each long leg
    # in by one strike, and how much max loss does that remove?
    rows = []
    for d in exp:
        tss = days.get(d) or []
        if len(tss) < 60:
            continue
        ed = byts[tss[1]]
        atm0 = round(ed["spot"] / STEP) * STEP
        for k, ts in enumerate(tss[2:], start=2):
            row = byts[ts]
            at = round(row["spot"] / STEP) * STEP
            # fixed entry strikes, expressed as offsets from THIS bar ATM
            o_short_put = (atm0 - 2 * STEP) - at
            o_short_call = (atm0 + 2 * STEP) - at
            o_long_put = (atm0 - 4 * STEP) - at
            o_long_call = (atm0 + 4 * STEP) - at
            if abs(o_long_put - STEP) > 8 * STEP or abs(o_long_call + STEP) > 8 * STEP:
                continue
            lp = row["PUT"].get(o_long_put); lp_in = row["PUT"].get(o_long_put + STEP)
            lc = row["CALL"].get(o_long_call); lc_in = row["CALL"].get(o_long_call - STEP)
            sp = row["PUT"].get(o_short_put); sc = row["CALL"].get(o_short_call)
            if None in (lp, lp_in, lc, lc_in, sp, sc):
                continue
            debit_put = lp_in[0] - lp[0]        # buy closer long, sell farther long
            debit_call = lc_in[0] - lc[0]
            debit = debit_put + debit_call
            risk_cut = 2 * STEP                 # each side loses one strike of width
            value = sp[0] + sc[0] - lp[0] - lc[0]
            rows.append({"date": str(d), "bar": k, "debit": debit,
                         "risk_cut": risk_cut, "value": value,
                         "fraction": k / max(1, len(tss) - 1)})
    if not rows:
        print("no data"); return

    print("obs:", len(rows))
    print()
    print("Roll the long legs in ONE strike (200pt spread -> 100pt spread)")
    print("  risk removed per roll      : %.0f points" % rows[0]["risk_cut"])
    print("  mean debit                 : %+.2f points" % statistics.mean(r["debit"] for r in rows))
    print("  median debit               : %+.2f points" % statistics.median(r["debit"] for r in rows))
    print("  mean edge (cut - debit)    : %+.2f points" % statistics.mean(
        r["risk_cut"] - r["debit"] for r in rows))
    neg = [r for r in rows if r["debit"] > r["risk_cut"]]
    print("  rolls where debit > cut    : %d / %d (%.1f%%)" % (
        len(neg), len(rows), 100.0 * len(neg) / len(rows)))

    print()
    print("does the edge depend on WHEN in the session you roll?")
    buckets = {}
    for r in rows:
        b = min(9, int(r["fraction"] * 10))
        buckets.setdefault(b, []).append(r)
    print("  %-10s %6s %12s %12s" % ("session", "n", "mean debit", "mean edge"))
    for b in sorted(buckets):
        v = buckets[b]
        print("  %2d0-%2d0%%   %6d %+12.2f %+12.2f" % (
            b, b + 1, len(v), statistics.mean(x["debit"] for x in v),
            statistics.mean(x["risk_cut"] - x["debit"] for x in v)))

    print()
    print("and does it depend on how far the position has decayed?")
    rr = sorted(rows, key=lambda r: r["value"])
    q = len(rr) // 4
    for label, chunk in (("cheapest 25% of marks", rr[:q]),
                         ("25-50%", rr[q:2*q]),
                         ("50-75%", rr[2*q:3*q]),
                         ("dearest 25%", rr[3*q:])):
        print("  %-24s n=%5d  mean debit %+7.2f  mean edge %+7.2f" % (
            label, len(chunk), statistics.mean(x["debit"] for x in chunk),
            statistics.mean(x["risk_cut"] - x["debit"] for x in chunk)))


if __name__ == "__main__":
    main()
