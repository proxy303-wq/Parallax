"""Metacognition — calibration and bias-firewall tests."""
from parallax.core.metacognition import BiasDetector, CalibrationTracker


def test_calibration_tracks_hit_rate():
    ct = CalibrationTracker(min_samples=5)
    for _ in range(10):
        ct.record(0.7, True)
    for _ in range(4):
        ct.record(0.7, False)
    assert abs(ct.hit_rate("0.7-0.8") - 10 / 14) < 1e-9


def test_calibrate_shrinks_toward_hit_rate():
    ct = CalibrationTracker(min_samples=5)
    for _ in range(20):
        ct.record(0.7, True)  # 100% hit rate at 0.7
    cal = ct.calibrate(0.7)
    assert cal is not None and cal > 0.7  # pulled up toward 1.0


def test_calibrate_none_without_samples():
    ct = CalibrationTracker(min_samples=30)
    assert ct.calibrate(0.6) is None


def test_brier_score():
    import pytest
    ct = CalibrationTracker()
    ct.record(0.8, True)
    ct.record(0.8, False)
    # mean squared error = ((0.2)^2 + (0.8)^2) / 2 = 0.34
    assert ct.brier_score() == pytest.approx(0.34, abs=1e-9)


def test_revenge_bias_triggers():
    flags = BiasDetector().check(0.6, ["LOSS", "LOSS"], 0, 50, 25, 0, 0)
    revenge = next(f for f in flags if f.name == "revenge")
    assert revenge.triggered and revenge.blocking


def test_overconfidence_downgrades_not_blocks():
    flags = BiasDetector().check(0.95, [], 0, 50, 25, 0, 0)
    oc = next(f for f in flags if f.name == "overconfidence")
    assert oc.triggered and not oc.blocking


def test_frequency_bias_triggers():
    flags = BiasDetector().check(0.5, [], 5, 50, 25, 0, 0, max_trades_per_day=4)
    freq = next(f for f in flags if f.name == "frequency")
    assert freq.triggered and freq.blocking
