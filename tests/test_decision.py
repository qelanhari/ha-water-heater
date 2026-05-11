"""Tests for decision.py — the integration's brain.

v0.2.0 introduces:
- Decision.signal_switch_on (the 2h-committed contactor)
- Decision.boost_mode_on (the responsive 54↔55°C target toggle)
- Inputs.signal_on_at + signal_currently_on
- Thresholds.signal_min_hold_minutes (default 120)
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

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
    def test_off(self, base_inputs, thr):
        d = decide(replace(base_inputs, mode=MODE_OFF), thr)
        assert d.signal_switch_on is False
        assert d.boost_mode_on is False

    def test_boost_turns_on_both(self, base_inputs, thr):
        d = decide(replace(base_inputs, mode=MODE_BOOST), thr)
        assert d.signal_switch_on is True
        assert d.boost_mode_on is True

    def test_hc_only_off_in_hp(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, mode=MODE_HC_ONLY, is_hc=False, energy_needed_kwh=2.0),
            thr,
        )
        assert d.signal_switch_on is False

    def test_hc_only_on_in_hc(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, mode=MODE_HC_ONLY, is_hc=True, energy_needed_kwh=2.0),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is False   # HC time, save what we can

    def test_solar_only_with_abundant_surplus_boosts(self, base_inputs, thr):
        # Surplus must exceed heater_W + safety + boost_extra (200+1000=1200).
        # Heater off → delta = 800. Need grid + 800 < -1200 → grid < -2000.
        d = decide(
            replace(base_inputs, mode=MODE_SOLAR_ONLY, grid_smooth_w=-2500.0), thr
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is True

    def test_solar_only_modest_surplus_no_boost(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, mode=MODE_SOLAR_ONLY, grid_smooth_w=-1500.0), thr
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is False


# ----------------------------------------------------------------------------
# 2h signal hold
# ----------------------------------------------------------------------------


class TestSignalHold:
    def test_within_hold_stays_on_even_without_surplus(self, base_inputs, thr):
        # Signal was turned on 30 min ago; current state: no surplus.
        # Commitment forces signal_switch=True regardless.
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=30),
                grid_smooth_w=200.0,  # importing
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert "2h hold" in d.reason

    def test_after_hold_normal_decision(self, base_inputs, thr):
        # Signal was on 121 min ago; hold expired. Without surplus → off.
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=121),
                grid_smooth_w=200.0,
            ),
            thr,
        )
        assert d.signal_switch_on is False

    def test_hold_does_not_prevent_hard_floor(self, base_inputs, thr):
        # Hard floor breach takes priority before the hold check (and the
        # boost flag stays off since hard floor is a safety, not a boost).
        morning = datetime(2026, 6, 16, 5, 0, 0, tzinfo=timezone.utc)
        d = decide(
            replace(
                base_inputs,
                now=morning,
                tank_middle_c=45.0,   # below 48 floor
                signal_currently_on=True,
                signal_on_at=morning - timedelta(minutes=30),
            ),
            thr,
        )
        assert d.action == "hard_floor"

    def test_during_hold_boost_tracks_surplus(self, base_inputs, thr):
        # In hold, with abundant surplus → boost on. Without it → boost off.
        in_hold = dict(
            signal_currently_on=True,
            signal_on_at=base_inputs.now - timedelta(minutes=30),
        )
        d_high = decide(replace(base_inputs, grid_smooth_w=-2500.0, **in_hold), thr)
        d_low = decide(replace(base_inputs, grid_smooth_w=0.0, **in_hold), thr)
        assert d_high.boost_mode_on is True
        assert d_low.boost_mode_on is False


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
        assert d.signal_switch_on is True
        assert d.action == "hard_floor"
        assert d.boost_mode_on is False   # safety only, no boost

    def test_breach_outside_morning_no_override(self, base_inputs, thr):
        d = decide(replace(base_inputs, tank_middle_c=45.0), thr)
        assert d.action != "hard_floor"


# ----------------------------------------------------------------------------
# Tempo Rouge HP guard (now factors in the full 2h worst-case projection)
# ----------------------------------------------------------------------------


class TestRougeHp:
    def test_rouge_hp_empty_cycle_off(self, base_inputs, thr):
        # 2 h × 800 W = 1.6 kWh worst-case at 0.706 €/kWh = 1.13 € → blended
        # 0.706 alone (no solar buffer in the cycle yet) → way over 0.118.
        d = decide(
            replace(
                base_inputs, tempo_color="Rouge", is_hc=False, grid_smooth_w=-2000.0,
                cycle=CycleAccumulator(),
            ),
            thr,
        )
        assert d.signal_switch_on is False

    def test_rouge_hp_big_solar_buffer_ok(self, base_inputs, thr):
        # 30 kWh of solar already in the cycle. Worst-case adding 1.6 kWh HP
        # = 1.13 €, total 31.6 kWh, blended = 0.0358 €/kWh — fine.
        d = decide(
            replace(
                base_inputs,
                tempo_color="Rouge",
                is_hc=False,
                grid_smooth_w=-2000.0,
                cycle=CycleAccumulator(kwh_solar=30.0),
            ),
            thr,
        )
        assert d.signal_switch_on is True

    def test_rouge_hc_window_allowed(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs, tempo_color="Rouge", is_hc=True, energy_needed_kwh=3.0
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.action == "heat_hc"


# ----------------------------------------------------------------------------
# HC window
# ----------------------------------------------------------------------------


class TestHcWindow:
    def test_need_on_eco(self, base_inputs, thr):
        d = decide(replace(base_inputs, is_hc=True, energy_needed_kwh=2.5), thr)
        assert d.signal_switch_on is True
        assert d.boost_mode_on is False   # HC eco

    def test_no_need_off(self, base_inputs, thr):
        d = decide(replace(base_inputs, is_hc=True, energy_needed_kwh=0.0), thr)
        assert d.signal_switch_on is False


# ----------------------------------------------------------------------------
# HP window non-Rouge (solar candidate)
# ----------------------------------------------------------------------------


class TestHpSolar:
    def test_surplus_and_sunny_forecast_on(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs, is_hc=False, grid_smooth_w=-1500.0,
                forecast_today_kwh=15.0, energy_needed_kwh=3.0,
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.action == "heat_solar"

    def test_no_surplus_off(self, base_inputs, thr):
        d = decide(
            replace(base_inputs, is_hc=False, grid_smooth_w=200.0),
            thr,
        )
        assert d.signal_switch_on is False

    def test_surplus_but_cloudy_forecast_short_waits(self, base_inputs, thr):
        d = decide(
            replace(
                base_inputs,
                is_hc=False,
                grid_smooth_w=-1500.0,
                forecast_today_kwh=2.0,
                energy_needed_kwh=4.0,
            ),
            thr,
        )
        assert d.signal_switch_on is False
        assert "forecast" in d.reason.lower()

    def test_abundant_surplus_triggers_boost(self, base_inputs, thr):
        # grid -2500 + 800 = -1700 < -(200+1000) → boost on.
        d = decide(
            replace(
                base_inputs, is_hc=False, grid_smooth_w=-2500.0,
                forecast_today_kwh=15.0, energy_needed_kwh=3.0,
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is True

    def test_modest_surplus_no_boost(self, base_inputs, thr):
        # grid -1500 + 800 = -700, but cap is -(200+1000) = -1200 → no boost.
        d = decide(
            replace(
                base_inputs, is_hc=False, grid_smooth_w=-1500.0,
                forecast_today_kwh=15.0, energy_needed_kwh=3.0,
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is False
