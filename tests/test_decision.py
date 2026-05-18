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

    def test_during_hold_boost_is_frozen(self, base_inputs, thr):
        """Mid-cycle the boost flag must NOT change, regardless of surplus.
        Toggling boost sends `number.set_value` to the Atlantic and thrashes
        the appliance setpoint (caused premature compressor abort 2026-05-13).
        Once a cycle starts, boost is locked at whatever it currently is."""
        in_hold = dict(
            signal_currently_on=True,
            signal_on_at=base_inputs.now - timedelta(minutes=30),
        )
        # Boost was OFF when cycle started → stays OFF even with huge surplus.
        d_off_to_high = decide(
            replace(base_inputs, grid_smooth_w=-2500.0,
                    boost_currently_on=False, **in_hold),
            thr,
        )
        assert d_off_to_high.boost_mode_on is False
        assert "frozen" in d_off_to_high.reason
        # Boost was ON when cycle started → stays ON even when surplus collapses.
        d_on_to_low = decide(
            replace(base_inputs, grid_smooth_w=0.0,
                    boost_currently_on=True, **in_hold),
            thr,
        )
        assert d_on_to_low.boost_mode_on is True
        assert "frozen" in d_on_to_low.reason

    def test_boost_freshly_decided_at_signal_on_transition(self, base_inputs, thr):
        """Off→on transition: boost is evaluated normally (signal_currently_on
        is still False on the first tick that flips signal_switch_on True)."""
        d = decide(
            replace(base_inputs,
                    signal_currently_on=False,        # last observed state
                    grid_smooth_w=-2500.0,            # huge surplus
                    is_hc=False,                       # HP solar path
                    forecast_today_kwh=15.0, energy_needed_kwh=3.0),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is True
        assert "frozen" not in d.reason

    def test_action_never_says_wait_when_heater_is_on_in_hold(self, base_inputs, thr):
        """B5: during the 2h hold with no surplus and no HC, action used to
        return 'wait' even though signal_switch_on was True — confusing."""
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=30),
                grid_smooth_w=200.0,   # importing, no surplus
                is_hc=False,           # HP window
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.action != "wait"
        assert d.action == "hold_signal"

    # --- post-2h policy (new semantics) ------------------------------------

    def test_past_2h_in_hc_stays_on(self, base_inputs, thr):
        """HC + auto past 2h: legacy YAML owns the off-decision. We must
        not turn off ourselves — the tank may still be charging the middle
        via convection after the appliance's top probe paused."""
        hc_late = base_inputs.now.replace(hour=3, minute=15)
        d = decide(
            replace(
                base_inputs,
                now=hc_late,
                is_hc=True,
                signal_currently_on=True,
                signal_on_at=hc_late - timedelta(minutes=180),  # 3h in
                heater_power_w=0.0,           # appliance currently idle
                tank_middle_c=49.0,
                energy_needed_kwh=0.5,
            ),
            thr,
        )
        assert d.signal_switch_on is True

    def test_past_2h_hp_appliance_active_with_surplus_stays_on(self, base_inputs, thr):
        # Heater still drawing, surplus present → stay on (productive).
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=150),
                heater_power_w=800.0,
                grid_smooth_w=-1500.0,   # exporting strongly
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is True

    def test_past_2h_hp_appliance_idle_no_surplus_releases(self, base_inputs, thr):
        # Heater idle (appliance paused at target), no surplus → release.
        # Decision then falls through to normal HP branch which will say wait.
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=150),
                heater_power_w=9.0,
                grid_smooth_w=200.0,
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is False

    def test_past_2h_hp_appliance_idle_with_surplus_stays_on(self, base_inputs, thr):
        # Heater is idle (paused) but surplus is here — keep contactor on
        # so the appliance can resume when its top probe falls.
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=150),
                heater_power_w=9.0,            # idle
                grid_smooth_w=-1500.0,         # still exporting
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is True

    def test_past_2h_hp_grid_near_zero_continues(self, base_inputs, thr):
        """Reproduction of 2026-05-18 bug: heater drawing 664 W, grid −10 W
        (almost-perfectly solar-balanced). The old strict check thought
        surplus had collapsed and dropped the contactor. New permissive
        continue-threshold keeps the cycle running."""
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=125),
                heater_power_w=664.0,
                grid_smooth_w=-10.0,
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is True

    def test_past_2h_hp_significant_import_releases(self, base_inputs, thr):
        # grid above continue threshold → release.
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=130),
                heater_power_w=700.0,
                grid_smooth_w=300.0,  # > +200 continue threshold
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is False

    def test_start_still_requires_strict_surplus(self, base_inputs, thr):
        # Signal currently off; the start decision must still use the
        # strict (delta-aware) check. grid −10 W is not enough surplus
        # to commit to a new cycle (would import 790 W if heater turns on).
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=False,
                heater_power_w=0.0,
                grid_smooth_w=-10.0,
                is_hc=False,
                forecast_today_kwh=15.0,
                energy_needed_kwh=2.0,
            ),
            thr,
        )
        assert d.signal_switch_on is False

    def test_tank_full_and_appliance_idle_releases(self, base_inputs, thr):
        """Reproduction of 2026-05-18 14:xx state: middle at 54.2 °C
        (within 0.5 °C of 54 target), appliance idle (9 W), strong PV
        surplus. Without the universal "nothing to do" gate, the
        integration would keep the contactor closed indefinitely.
        With the gate, it releases."""
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=30),
                heater_power_w=9.0,
                grid_smooth_w=-1135.0,
                tank_middle_c=54.2,
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is False
        assert "tank middle" in d.reason
        assert "idle" in d.reason

    def test_tank_full_but_appliance_still_drawing_keeps_running(self, base_inputs, thr):
        # Mid-cycle, appliance is still heating: don't interrupt — let
        # the appliance's own logic decide when to stop.
        d = decide(
            replace(
                base_inputs,
                signal_currently_on=True,
                signal_on_at=base_inputs.now - timedelta(minutes=30),
                heater_power_w=700.0,
                grid_smooth_w=-1500.0,
                tank_middle_c=54.2,
                is_hc=False,
            ),
            thr,
        )
        assert d.signal_switch_on is True

    def test_tank_full_manual_boost_still_engages(self, base_inputs, thr):
        # MODE_BOOST is a sovereign override — user wants boost, give it.
        d = decide(
            replace(
                base_inputs,
                mode=MODE_BOOST,
                heater_power_w=9.0,
                tank_middle_c=54.2,
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.boost_mode_on is True

    def test_hc_ending_with_appliance_still_drawing_no_surplus_releases(
        self, base_inputs, thr
    ):
        """HC just ended (is_hc flipped False) but the cycle that started
        at e.g. 03:30 is still running. Appliance still drawing 800 W. No
        solar surplus yet. We MUST release — staying on would burn HP-
        priced grid energy."""
        early_morning = base_inputs.now.replace(hour=6, minute=5)
        d = decide(
            replace(
                base_inputs,
                now=early_morning,
                is_hc=False,                 # HC just ended
                signal_currently_on=True,
                signal_on_at=early_morning - timedelta(minutes=155),
                heater_power_w=800.0,        # appliance still actively heating
                grid_smooth_w=300.0,         # importing (no surplus)
            ),
            thr,
        )
        assert d.signal_switch_on is False


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

    def test_missing_tank_middle_does_not_breach(self, base_inputs, thr):
        """Sensor unavailable (None) must NOT be treated as 0 °C — was bug B2."""
        morning = datetime(2026, 6, 16, 5, 0, 0, tzinfo=timezone.utc)
        d = decide(
            replace(base_inputs, now=morning, tank_middle_c=None),
            thr,
        )
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
        # Rouge HC: same checkpoint gating as Bleu — only fires at a
        # checkpoint with a real reason. Here: 01:00 main + mid below floor.
        d = decide(
            replace(
                base_inputs,
                now=base_inputs.now.replace(hour=1, minute=0),
                tempo_color="Rouge",
                is_hc=True,
                tank_middle_c=46.0,
                energy_needed_kwh=3.0,
            ),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.action == "heat_hc"


# ----------------------------------------------------------------------------
# HC window
# ----------------------------------------------------------------------------


class TestHcWindow:
    """Three time-gated HC checkpoints: 22:30 rescue, 01:00 main, 03:30 last.
    Outside these instants the decision module never starts a HC cycle —
    even if `is_hc` is True and energy is needed."""

    @staticmethod
    def _at(base, hour, minute, **kw):
        return replace(
            base,
            now=base.now.replace(hour=hour, minute=minute),
            is_hc=True,
            **kw,
        )

    # --- outside checkpoints --------------------------------------------------

    def test_22h00_does_not_fire(self, base_inputs, thr):
        # The bug from 2026-05-11: HC starts at 22:00 but we should wait.
        d = decide(
            self._at(base_inputs, 22, 0,
                     tank_middle_c=46.0, energy_needed_kwh=1.5,
                     forecast_tomorrow_kwh=15.0),
            thr,
        )
        assert d.signal_switch_on is False
        assert d.action == "wait"

    def test_02h00_between_checkpoints_does_not_fire(self, base_inputs, thr):
        d = decide(
            self._at(base_inputs, 2, 0,
                     tank_middle_c=46.0, energy_needed_kwh=1.5),
            thr,
        )
        assert d.signal_switch_on is False

    def test_05h00_after_last_chance_does_not_fire(self, base_inputs, thr):
        # Too late — a 2h cycle would push into HP.
        # Mid above hard-floor (48 °C) so the morning-window hard-floor
        # safety net (branch 2) doesn't pre-empt this check.
        d = decide(
            self._at(base_inputs, 5, 0,
                     tank_middle_c=49.0, energy_needed_kwh=2.0),
            thr,
        )
        assert d.signal_switch_on is False

    def test_no_need_off(self, base_inputs, thr):
        d = decide(
            self._at(base_inputs, 1, 0,
                     tank_middle_c=50.0, energy_needed_kwh=0.0),
            thr,
        )
        assert d.signal_switch_on is False

    # --- 22:30 rescue ---------------------------------------------------------

    def test_22h30_depleted_tank_fires(self, base_inputs, thr):
        d = decide(
            self._at(base_inputs, 22, 30,
                     tank_middle_c=40.0, energy_needed_kwh=3.0),
            thr,
        )
        assert d.signal_switch_on is True
        assert d.action == "heat_hc"
        assert "rescue" in d.reason

    def test_22h30_only_mildly_low_does_not_fire(self, base_inputs, thr):
        # Mid is 45 — below the main floor (48) but well above rescue (42).
        # We deliberately wait until 01:00 rather than burning a cycle early.
        d = decide(
            self._at(base_inputs, 22, 30,
                     tank_middle_c=45.0, energy_needed_kwh=2.0),
            thr,
        )
        assert d.signal_switch_on is False

    # --- 01:00 main -----------------------------------------------------------

    def test_01h00_tank_below_floor_fires(self, base_inputs, thr):
        d = decide(
            self._at(base_inputs, 1, 0,
                     tank_middle_c=46.0, energy_needed_kwh=2.0,
                     forecast_tomorrow_kwh=15.0),
            thr,
        )
        assert d.signal_switch_on is True
        assert "main" in d.reason

    def test_01h00_warm_tank_sunny_tomorrow_defers(self, base_inputs, thr):
        # Reproduction of yesterday's complaint in the right time slot:
        # tank above floor + sunny tomorrow → let solar do it for free.
        d = decide(
            self._at(base_inputs, 1, 0,
                     tank_middle_c=49.0, energy_needed_kwh=1.5,
                     forecast_tomorrow_kwh=15.0, outdoor_c=18.0),
            thr,
        )
        assert d.signal_switch_on is False
        assert d.action == "wait"

    def test_01h00_warm_tank_cloudy_tomorrow_fires(self, base_inputs, thr):
        # Mid above floor but tomorrow's PV won't cover need × 1.5.
        d = decide(
            self._at(base_inputs, 1, 0,
                     tank_middle_c=49.0, energy_needed_kwh=2.0,
                     forecast_tomorrow_kwh=2.0, outdoor_c=18.0),
            thr,
        )
        assert d.signal_switch_on is True
        assert "tomorrow" in d.reason.lower() or "forecast" in d.reason.lower() \
            or "won't cover" in d.reason

    def test_01h00_cold_outdoor_fires_even_if_warm_tank(self, base_inputs, thr):
        # Deep winter hedge: outdoor < 5°C → heat regardless.
        d = decide(
            self._at(base_inputs, 1, 0,
                     tank_middle_c=49.0, energy_needed_kwh=2.0,
                     forecast_tomorrow_kwh=15.0, outdoor_c=2.0),
            thr,
        )
        assert d.signal_switch_on is True
        assert "winter" in d.reason.lower() or "outdoor" in d.reason.lower()

    # --- 03:30 last chance ----------------------------------------------------

    def test_03h30_still_below_floor_fires(self, base_inputs, thr):
        # Forecast may have revised cloudier since 01:00, or 01:00 cycle
        # undershot — last chance to fit a 2h cycle before HC ends.
        d = decide(
            self._at(base_inputs, 3, 30,
                     tank_middle_c=46.0, energy_needed_kwh=1.5),
            thr,
        )
        assert d.signal_switch_on is True
        assert "last chance" in d.reason

    def test_03h30_tank_already_ok_does_not_fire(self, base_inputs, thr):
        d = decide(
            self._at(base_inputs, 3, 30,
                     tank_middle_c=49.0, energy_needed_kwh=1.0),
            thr,
        )
        assert d.signal_switch_on is False

    # --- tolerance window -----------------------------------------------------

    def test_checkpoint_tolerance_plus_minus_5_min(self, base_inputs, thr):
        # 01:04 should still match the 01:00 checkpoint (5-min tolerance).
        d = decide(
            self._at(base_inputs, 1, 4,
                     tank_middle_c=46.0, energy_needed_kwh=2.0),
            thr,
        )
        assert d.signal_switch_on is True

    def test_01h06_outside_tolerance_does_not_fire(self, base_inputs, thr):
        d = decide(
            self._at(base_inputs, 1, 6,
                     tank_middle_c=46.0, energy_needed_kwh=2.0),
            thr,
        )
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
