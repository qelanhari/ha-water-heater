"""Tests for forecast.py — Forecast.Solar plumbing."""

from forecast import (  # type: ignore[import-not-found]
    expected_pv_kwh_today,
    is_cloudy_day,
    will_likely_cover_heating_need,
)


class TestExpectedPvKwh:
    def test_wh_to_kwh(self):
        assert expected_pv_kwh_today(15000) == 15.0

    def test_zero(self):
        assert expected_pv_kwh_today(0) == 0.0

    def test_negative_clamped(self):
        assert expected_pv_kwh_today(-100) == 0.0

    def test_none_passes_through(self):
        assert expected_pv_kwh_today(None) is None


class TestCloudy:
    def test_sunny_day(self):
        # 6 kWp × 4h = 24 kWh theoretical. 70% of that = 16.8 → sunny.
        assert is_cloudy_day(16.8, panel_peak_kwp=6.0) is False

    def test_cloudy_day(self):
        # 6 kWp × 4h × 0.3 = 7.2 kWh threshold. 5 kWh predicted = cloudy.
        assert is_cloudy_day(5.0, panel_peak_kwp=6.0) is True

    def test_no_forecast(self):
        assert is_cloudy_day(None, panel_peak_kwp=6.0) is None


class TestCoverHeatingNeed:
    def test_easy_cover(self):
        # 10 kWh forecast, 2 kWh need × 1.5 = 3 kWh threshold → easy.
        assert will_likely_cover_heating_need(10.0, 2.0) is True

    def test_barely_short(self):
        # 4 kWh forecast, 3 kWh need × 1.5 = 4.5 → short.
        assert will_likely_cover_heating_need(4.0, 3.0) is False

    def test_no_need(self):
        # Tank already full.
        assert will_likely_cover_heating_need(0.5, 0.0) is True

    def test_no_forecast(self):
        assert will_likely_cover_heating_need(None, 2.0) is None
