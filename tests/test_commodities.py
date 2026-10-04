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


def test_lot_multipliers_are_the_pnl_units():
    assert COMMODITIES["CRUDEOIL"].lot == 100
    assert COMMODITIES["GOLDM"].lot == 10
    assert COMMODITIES["SILVERM"].lot == 5
    assert COMMODITIES["NATURALGAS"].lot == 1250


def test_spec_lookup_is_case_insensitive():
    assert spec("crudeoil").label == "Crude Oil"
    assert spec("GOLDM").symbol == "GOLDM"
