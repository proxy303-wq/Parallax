from parallax.apps.ops.commodity_condor import condor_from_chain

def _chain(futures, rows, symbol="GOLDM"):
    return {"symbol": symbol, "underlying": "Gold Mini", "expiry": "2026-10-29",
            "futures": futures, "rows": rows}

def _ladder(atm, step, n, prices):
    rows = []
    for i in range(-n, n + 1):
        k = atm + i * step
        px = prices.get(k, 0.0)
        rows.append({"strike": float(k), "option_type": "CE", "security_id": 1, "ltp": px})
        rows.append({"strike": float(k), "option_type": "PE", "security_id": 2, "ltp": px})
    return rows

def test_condor_credit_and_bounds():
    # ATM 148000, step 500. shorts +-2, wings +-4
    px = {147000.0: 2092.5, 149000.0: 3095.0, 145000.0: 1408.0, 151000.0: 2100.0}
    ch = _chain(148000.0, _ladder(148000.0, 500.0, 8, px))
    c = condor_from_chain(ch, 2, 4)
    assert c["atm"] == 148000.0
    assert (c["short_put"], c["short_call"]) == (147000.0, 149000.0)
    assert (c["long_put"], c["long_call"]) == (145000.0, 151000.0)
    assert c["credit"] == 1679.5
    assert c["max_loss"] == 2000 - 1679.5
    assert c["lot"] == 10
    assert c["max_profit_rs"] == 16795.0


def test_condor_none_when_a_leg_is_missing():
    ch = _chain(147280.0, _ladder(148000.0, 500.0, 2, {}))
    assert condor_from_chain(ch, 2, 4) is None


def test_condor_rounds_atm_to_the_step():
    px = {147500.0: 100.0, 148500.0: 100.0, 145500.0: 10.0, 150500.0: 10.0}
    ch = _chain(147280.0, _ladder(148000.0, 500.0, 8, px))
    c = condor_from_chain(ch, 1, 2)
    assert c["atm"] == 147500.0
