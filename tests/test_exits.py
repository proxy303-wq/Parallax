"""Deterministic exit-management tests (lock-profit + trailing)."""
from parallax.contracts import Side
from parallax.core.execution import ExitConfig, ExitManager


def _long(entry=100.0, stop=95.0, cfg=None):
    return ExitManager(Side.BUY, entry, stop, cfg or ExitConfig(lock_r=0.5, trail_r=0.5, target_r=2.0))


def _short(entry=100.0, stop=105.0, cfg=None):
    return ExitManager(Side.SELL, entry, stop, cfg or ExitConfig(lock_r=0.5, trail_r=0.5, target_r=2.0))


def test_full_stop_loss():
    em = _long()  # 1R = 5, stop 95
    px, reason = em.update(high=99.0, low=94.5)
    assert px == 95.0 and reason == "stop"


def test_target_reached():
    em = _long()
    px, reason = em.update(high=111.0, low=100.0)
    assert px == 110.0 and reason == "target"


def test_stop_first_conservative():
    em = _long()
    # bar spans both target and stop -> stop assumed first
    px, reason = em.update(high=112.0, low=94.0)
    assert px == 95.0 and reason == "stop"


def test_lock_to_breakeven():
    em = _long()
    em.update(high=103.0, low=99.0)  # +0.6R -> armed, stop -> breakeven 100
    assert em.armed
    assert em.stop == 100.0
    px, reason = em.update(high=100.5, low=99.0)  # falls back to breakeven
    assert px == 100.0 and reason == "stop"


def test_trailing_stop_moves_up():
    em = _long()
    em.update(high=103.0, low=99.0)   # arm -> stop 100
    em.update(high=105.0, low=102.0)  # peak 105 -> trail stop = 102.5
    assert em.stop == 102.5


def test_short_symmetry():
    em = _short()  # 1R = 5, stop 105
    em.update(high=103.0, low=97.0)   # +0.6R -> armed, stop -> 100
    assert em.armed and em.stop == 100.0
    px, reason = em.update(high=102.0, low=97.5)  # falls back to breakeven
    assert px == 100.0 and reason == "stop"


def test_no_exit_when_inside_range():
    em = _long()
    px, reason = em.update(high=100.5, low=98.0)
    assert px is None and reason == ""


def test_mfe_mae_tracked():
    em = _long()
    em.update(high=104.0, low=96.0)
    assert em.mfe_r == 0.8   # (104-100)/5
    assert em.mae_r == 0.8   # (100-96)/5


def test_floor_guarantees_profit():
    em = _long(cfg=ExitConfig(lock_r=0.5, floor_r=0.2, trail_r=0.0, target_r=2.0))
    em.update(high=103.0, low=99.0)   # arm -> stop = 100 + 0.2*5 = 101
    assert em.stop == 101.0
