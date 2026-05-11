"""Tests for pattern.py — weekday EMA + outdoor coefficient."""

import math

from pattern import (  # type: ignore[import-not-found]
    PatternState,
    REFERENCE_OUTDOOR_C,
    confidence,
    expected_kwh_for_day,
    update_pattern,
)


class TestPatternState:
    def test_default_emas_uniform(self):
        s = PatternState()
        assert all(v == 3.0 for v in s.daily_kwh_ema)
        assert s.samples_n == 0

    def test_confidence_grows_with_samples(self):
        s = PatternState()
        assert confidence(s) == 0.0
        s = PatternState(samples_n=7)
        assert 0.4 < confidence(s) < 0.6
        s = PatternState(samples_n=20)
        assert confidence(s) == 1.0


class TestUpdatePattern:
    def test_first_sample_moves_ema(self):
        # Monday observed 6 kWh at reference temp.
        s = update_pattern(PatternState(), weekday=0, observed_kwh=6.0, outdoor_avg_c=REFERENCE_OUTDOOR_C)
        # Default alpha 0.25: new = 0.25 × 6 + 0.75 × 3 = 3.75
        assert math.isclose(s.daily_kwh_ema[0], 3.75)
        # Other weekdays unaffected.
        for w in range(1, 7):
            assert s.daily_kwh_ema[w] == 3.0
        assert s.samples_n == 1

    def test_cold_day_attribution(self):
        # 8 kWh on a 5°C day. Coef starts at 0.1, so cold component = (15-5)*0.1 = 1.0.
        # Normalised = 8 - 1 = 7 kWh. EMA update with alpha 0.25 from 3 → 4.
        s = update_pattern(
            PatternState(), weekday=0, observed_kwh=8.0, outdoor_avg_c=5.0
        )
        assert math.isclose(s.daily_kwh_ema[0], 0.25 * 7 + 0.75 * 3)

    def test_outdoor_coef_drifts_toward_observation(self):
        # Repeated cold-day pattern should nudge outdoor_coef up.
        s = PatternState()
        for _ in range(10):
            s = update_pattern(s, weekday=0, observed_kwh=10.0, outdoor_avg_c=0.0)
        # 10 observations should have moved coef noticeably above 0.10.
        assert s.outdoor_coef > 0.10

    def test_coef_bounded(self):
        s = PatternState(outdoor_coef=0.45)
        for _ in range(50):
            s = update_pattern(s, weekday=0, observed_kwh=20.0, outdoor_avg_c=-10.0)
        assert s.outdoor_coef <= 0.5


class TestExpected:
    def test_reference_day(self):
        s = PatternState(daily_kwh_ema=(2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0))
        assert expected_kwh_for_day(s, 3, REFERENCE_OUTDOOR_C) == 5.0

    def test_cold_day_amplifies(self):
        s = PatternState(daily_kwh_ema=(5.0,) * 7, outdoor_coef=0.2)
        # 5°C: cold component = (15-5) × 0.2 = 2 kWh → expected = 7 kWh.
        assert math.isclose(expected_kwh_for_day(s, 0, 5.0), 7.0)

    def test_warm_day_unchanged(self):
        s = PatternState(daily_kwh_ema=(5.0,) * 7, outdoor_coef=0.2)
        # 25°C ≥ reference → cold component = 0 → expected = 5.
        assert expected_kwh_for_day(s, 0, 25.0) == 5.0
