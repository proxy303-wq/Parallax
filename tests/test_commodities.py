from parallax.config.commodities import COMMODITIES, Commodity, MCX_SEGMENT, spec

def test_four_targets_present():
    assert set(COMMODITIES) == {"CRUDEOIL", "GOLDM", "SILVERM", "NATURALGAS"}


def test_every_commodity_rides_the_mcx_segment():
    for c in COMMODITIES.values():
        assert isinstance(c, Commodity)
        assert c.futures == c.symbol          # futures symbol matches the option base
        assert c.step > 0


def test_steps_are_sensible():
    assert COMMODITIES["CRUDEOIL"].step == 50.0
    assert COMMODITIES["NATURALGAS"].step == 5.0
    assert COMMODITIES["GOLDM"].step >= COMMODITIES["CRUDEOIL"].step


def test_lot_is_unresolved_until_margin_api():
    for c in COMMODITIES.values():
        assert c.lot is None, "lot must come from the margin calculator"


def test_spec_lookup_is_case_insensitive():
    assert spec("crudeoil").label == "Crude Oil"
    assert spec("GOLDM").symbol == "GOLDM"
