"""Tests for thermo.py — tank energy math."""

import math

from thermo import (  # type: ignore[import-not-found]
    energy_budget_until_morning_kwh,
    energy_to_heat_kwh,
    heat_loss_w,
    predicted_temp_drop_c,
)


class TestEnergyToHeat:
    def test_no_heating_needed_when_already_at_target(self):
        assert energy_to_heat_kwh(54.0, 54.0, 270) == 0.0

    def test_below_target_uses_specific_heat(self):
        # 270 L × 1.163e-3 kWh/L·K × (54-44) K = 3.14 kWh
        kwh = energy_to_heat_kwh(44.0, 54.0, 270)
        assert math.isclose(kwh, 270 * 1.163e-3 * 10, abs_tol=1e-3)

    def test_above_target_is_zero(self):
        assert energy_to_heat_kwh(60.0, 54.0, 270) == 0.0


class TestHeatLoss:
    def test_no_loss_when_at_garage_temp(self):
        assert heat_loss_w(18.0, 18.0) == 0.0

    def test_loss_scales_with_delta(self):
        # 1.6 W/K × (50-18) = 51.2 W
        assert math.isclose(heat_loss_w(50.0, 18.0), 51.2)

    def test_floor_at_zero(self):
        # cold garage warmer than tank → 0, not negative
        assert heat_loss_w(10.0, 18.0) == 0.0


class TestPredictedDrop:
    def test_drop_over_8_hours(self):
        # 8 h × 51.2 W = 409.6 Wh = 0.41 kWh
        # in a 270 L tank with 1.163e-3 kWh/(L·K), ΔT = 0.41/(270×1.163e-3) ≈ 1.30 K
        drop = predicted_temp_drop_c(50.0, 18.0, hours=8.0)
        assert 1.0 < drop < 1.5

    def test_zero_hours(self):
        assert predicted_temp_drop_c(50.0, 18.0, hours=0.0) == 0.0


class TestEnergyBudget:
    def test_full_tank_no_morning_drop_returns_zero(self):
        # Top already at target, projected end > floor, no usage.
        kwh = energy_budget_until_morning_kwh(
            current_top_c=54.0,
            target_top_c=54.0,
            floor_c=48.0,
            garage_c=18.0,
            hours_to_morning=8.0,
            expected_usage_kwh=0.0,
        )
        assert kwh == 0.0

    def test_cold_tank_requires_heat(self):
        kwh = energy_budget_until_morning_kwh(
            current_top_c=40.0,
            target_top_c=54.0,
            floor_c=48.0,
            garage_c=18.0,
            hours_to_morning=8.0,
            expected_usage_kwh=2.0,
        )
        # Should be > 4 kWh (the heating delta alone is ~4.4 kWh).
        assert kwh > 4.0

    def test_includes_standing_loss(self):
        kwh_no_loss = energy_budget_until_morning_kwh(
            45.0, 54.0, 48.0, 18.0, hours_to_morning=0.01, expected_usage_kwh=0.0,
        )
        kwh_with_loss = energy_budget_until_morning_kwh(
            45.0, 54.0, 48.0, 18.0, hours_to_morning=8.0, expected_usage_kwh=0.0,
        )
        assert kwh_with_loss > kwh_no_loss
