from parallax.config.commodities import COMMODITIES, Commodity, MCX_SEGMENT, spec

def test_six_commodities_present():
    assert set(COMMODITIES) == {"CRUDEOIL", "CRUDEOILM", "GOLDM", "SILVERM",
                                "NATURALGAS", "NATGASMINI"}


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
    assert COMMODITIES["CRUDEOILM"].lot == 10
    assert COMMODITIES["GOLDM"].lot == 10
    assert COMMODITIES["SILVERM"].lot == 5
    assert COMMODITIES["NATURALGAS"].lot == 1250
    assert COMMODITIES["NATGASMINI"].lot == 250


def test_book_shape_and_sizing():
    from parallax.config.commodities import BOOK, book_lots
    assert BOOK["short_off"] == 9 and BOOK["wing"] == 2
    assert BOOK["paper_balance"] == 1_000_000.0
    assert book_lots("GOLDM") == 3
    assert book_lots("crudeoilm") == 35
    assert book_lots("NATGASMINI") == 1   # dropped from the book -> default 1
    assert book_lots("UNKNOWN") == 1


def test_spec_lookup_is_case_insensitive():
    assert spec("crudeoil").label == "Crude Oil"
    assert spec("GOLDM").symbol == "GOLDM"
