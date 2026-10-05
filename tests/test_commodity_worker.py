from parallax.apps.worker.commodity_worker import commodity_plan, condor_payoff

def test_condor_payoff_bounds():
    sp, lp, sc, lc = 100.0, 90.0, 110.0, 120.0
    assert condor_payoff(sp, lp, sc, lc, 105.0) == 0.0
    assert condor_payoff(sp, lp, sc, lc, 80.0) == -10.0
    assert condor_payoff(sp, lp, sc, lc, 130.0) == -10.0
    assert condor_payoff(sp, lp, sc, lc, 95.0) == -5.0
    assert condor_payoff(sp, lp, sc, lc, 115.0) == -5.0


def test_condor_payoff_is_linear_between_shorts():
    sp, lp, sc, lc = 100.0, 90.0, 110.0, 120.0
    assert condor_payoff(sp, lp, sc, lc, 97.0) == -3.0
    assert condor_payoff(sp, lp, sc, lc, 108.0) == 0.0


def test_plan_returns_none_when_no_expiry(monkeypatch):
    import parallax.apps.worker.commodity_worker as w
    monkeypatch.setattr(w, "list_expiries", lambda s: ["2026-12-29"])
    assert w.commodity_plan(today="2026-12-15") is None


def test_plan_returns_the_symbol_on_expiry(monkeypatch):
    import parallax.apps.worker.commodity_worker as w
    def exps(s):
        return {"GOLDM": ["2026-10-29"], "CRUDEOILM": ["2026-10-15"]}[s]
    monkeypatch.setattr(w, "list_expiries", exps)
    assert w.commodity_plan(today="2026-10-29") == "GOLDM"
    assert w.commodity_plan(today="2026-10-15") == "CRUDEOILM"
    assert w.commodity_plan(today="2026-10-16") is None
