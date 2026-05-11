"""Tests for decision.py — the integration's brain."""

from dataclasses import replace
from datetime import datetime, timezone

from cost import CycleAccumulator  # type: ignore[import-not-found]
from decision import (  # type: ignore[import-not-found]
    MODE_AUTO,
    MODE_BOOST,
    MODE_HC_ONLY,
    MODE_OFF,
    MODE_SOLAR_ONLY,
    decide,
)


# ----------------------------------------------------------------------------
# Manual modes
# ----------------------------------------------------------------------------


class TestManual:
    def test_off_overrides_everything(self, base_inputs, thr):
        d = decide(replace(base_inputs, mode=MODE_OFF, tank_middle_c=20.0), thr)
        assert d.heater_on is False
        assert "manual=off" in d.reason

    def test_boost(self, base_inputs, thr):
        d = decide(replace(base_inputs, mode=MODE_BOOST), thr)
        assert d.heater_on is True
        assert d.action == "boost"

    def test_hc_only_off_in_hp(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs, mode=MODE_HC_ONLY, is_hc=False, energy_needed_kwh=2.0
            ),
            thr,
        )
        assert d.heater_on is False

    def test_hc_only_on_in_hc_with_need(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs, mode=MODE_HC_ONLY, is_hc=True, energy_needed_kwh=2.0
            ),
            thr,
        )
        assert d.heater_on is True

    def test_solar_only_with_surplus(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs, mode=MODE_SOLAR_ONLY, grid_smooth_w=-1500.0
            ),
            thr,
        )
        assert d.heater_on is True
        assert d.action == "heat_solar"

    def test_solar_only_no_surplus(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, mode=MODE_SOLAR_ONLY, grid_smooth_w=200.0), thr
        )
        assert d.heater_on is False


# ----------------------------------------------------------------------------
# Hard floor breach
# ----------------------------------------------------------------------------


class TestHardFloor:
    def test_breach_in_morning_forces_on(self, base_inputs, thr):
        morning = datetime(2026, 6, 16, 5, 0, 0, tzinfo=timezone.utc)
        d = decide(
            replace(base_inputs, now=morning, tank_middle_c=45.0),
            thr,
        )
        assert d.heater_on is True
        assert d.action == "hard_floor"

    def test_breach_outside_morning_no_override(self, base_inputs, thr):
        # 14:00 with cold tank: NOT the hard-floor window. Brain falls
        # through to normal logic, which is HP window, sunny forecast →
        # solar (no surplus in base_inputs) → wait.
        d = decide(replace(base_inputs, tank_middle_c=45.0), thr)
        assert d.action != "hard_floor"

    def test_above_floor_in_morning_no_force(self, base_inputs, thr):
        morning = datetime(2026, 6, 16, 5, 0, 0, tzinfo=timezone.utc)
        d = decide(
            replace(base_inputs, now=morning, tank_middle_c=51.0),
            thr,
        )
        assert d.action != "hard_floor"


# ----------------------------------------------------------------------------
# Tempo Rouge HP guard
# ----------------------------------------------------------------------------


class TestRougeHp:
    def test_rouge_hp_no_solar_off(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, tempo_color="Rouge", is_hc=False, grid_smooth_w=300.0),
            thr,
        )
        assert d.heater_on is False
        assert "Rouge HP" in d.reason

    def test_rouge_hp_solar_but_empty_cycle_off(self, base_inputs, thr):
        # Empty cycle: any HP contribution breaks the cap. Even with surplus,
        # the worst-case projection (15 min HP) violates the cap.
        d = decide(
            replace(
                base_inputs, tempo_color="Rouge", is_hc=False, grid_smooth_w=-2000.0,
                cycle=CycleAccumulator(),
            ),
            thr,
        )
        assert d.heater_on is False

    def test_rouge_hp_solar_with_solar_buffer_ok(self, base_inputs, thr):
        # 5 kWh already in via solar (free). Adding 0.2 kWh worst-case HP
        # gives blended 0.0272 €/kWh — well under 0.118.
        d = decide(
            replace(
                base_inputs,
                tempo_color="Rouge",
                is_hc=False,
                grid_smooth_w=-2000.0,
                cycle=CycleAccumulator(kwh_solar=5.0),
            ),
            thr,
        )
        assert d.heater_on is True
        assert d.action == "heat_solar"

    def test_rouge_hc_window_allowed(self, base_inputs, thr):
        # Even on a Rouge day, HC is the cheapest grid option at 0.1575.
        d = decide(
            replace(
                base_inputs,
                tempo_color="Rouge",
                is_hc=True,
                energy_needed_kwh=3.0,
            ),
            thr,
        )
        assert d.heater_on is True
        assert d.action == "heat_hc"


# ----------------------------------------------------------------------------
# HC window
# ----------------------------------------------------------------------------


class TestHcWindow:
    def test_need_on(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, is_hc=True, energy_needed_kwh=2.5),
            thr,
        )
        assert d.heater_on is True
        assert d.action == "heat_hc"

    def test_no_need_off(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, is_hc=True, energy_needed_kwh=0.0),
            thr,
        )
        assert d.heater_on is False


# ----------------------------------------------------------------------------
# HP window (non-Rouge)
# ----------------------------------------------------------------------------


class TestHpWindowSolar:
    def test_surplus_and_sunny_forecast_on(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs,
                is_hc=False,
                grid_smooth_w=-1500.0,
                forecast_today_kwh=15.0,
                energy_needed_kwh=3.0,
            ),
            thr,
        )
        assert d.heater_on is True
        assert d.action == "heat_solar"

    def test_no_surplus_off(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, is_hc=False, grid_smooth_w=200.0),
            thr,
        )
        assert d.heater_on is False

    def test_surplus_but_cloudy_forecast_short_waits(self, base_inputs, thr):
        # Surplus exists but Forecast.Solar says we won't cover what we need today.
        # The integration prefers waiting for HC tonight.
        d = decide(
            replace(
                base_inputs,
                is_hc=False,
                grid_smooth_w=-1500.0,
                forecast_today_kwh=2.0,    # tiny forecast
                energy_needed_kwh=4.0,
            ),
            thr,
        )
        assert d.heater_on is False
        assert "forecast" in d.reason.lower()
