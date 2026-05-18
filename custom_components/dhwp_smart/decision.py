"""The decision brain for the Smart DHWP integration.

Pure function over a snapshot of inputs + thresholds; returns a Decision
that the coordinator turns into a `switch.turn_on/off` service call on the
configured contactor entity.

Priority chain (top wins):

1. Manual mode (off / hc_only / solar_only / boost) — sovereign.
2. Hard floor breach in the morning window — force ON regardless of cost.
3. Tempo Rouge HP — solar OK only if the worst-case projected blended
   cost stays under `rouge_hp_blended_cap_eur_per_kwh`. Otherwise OFF.
4. HC window — only at three checkpoints (22:30 rescue, 01:00 main,
   03:30 last-chance). 22:30 fires only on a depleted tank; 01:00 on
   tank-middle below floor OR poor tomorrow forecast OR very cold outdoor;
   03:30 catches under-heated tanks (e.g. forecast revised cloudier).
   Outside the checkpoints, never start a HC cycle.
5. HP window (sunny day, not Rouge) — ON if:
     - smoothed grid surplus covers heater consumption with margin, AND
     - forecast says today's PV will comfortably cover remaining need
       (so we don't waste cheap HC on something solar will do for free).
6. Otherwise — OFF (wait).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

try:                                              # pragma: no cover — import shim
    # As part of the HA custom component package.
    from .cost import (
        CycleAccumulator,
        blended_cost_eur_per_kwh,
        can_still_meet_blended_cap,
    )
    from .forecast import will_likely_cover_heating_need
except ImportError:                                # pragma: no cover
    # Direct import in the test suite (sys.path points at this dir).
    from cost import (  # type: ignore[no-redef]
        CycleAccumulator,
        blended_cost_eur_per_kwh,
        can_still_meet_blended_cap,
    )
    from forecast import will_likely_cover_heating_need  # type: ignore[no-redef]

MODE_AUTO = "auto"
MODE_OFF = "off"
MODE_BOOST = "boost"
MODE_HC_ONLY = "hc_only"
MODE_SOLAR_ONLY = "solar_only"
MODES: tuple[str, ...] = (MODE_AUTO, MODE_OFF, MODE_BOOST, MODE_HC_ONLY, MODE_SOLAR_ONLY)
MANUAL_MODES: frozenset[str] = frozenset({MODE_OFF, MODE_BOOST, MODE_HC_ONLY, MODE_SOLAR_ONLY})

# Heat-pump nominal consumption in W. Used to size the surplus check.
HEATER_NOMINAL_W: float = 800.0


@dataclass(frozen=True)
class Inputs:
    now: datetime
    mode: str                                 # auto / off / boost / hc_only / solar_only
    tank_top_c: float                         # current top temperature
    tank_middle_c: float | None               # current "floor" sensor (None if unavailable)
    garage_c: float
    outdoor_c: float | None
    grid_smooth_w: float                      # negative = exporting
    pv_power_w: float                         # PV production right now
    heater_power_w: float                     # actual draw (0 if off)
    tempo_color: str                          # "Bleu" / "Blanc" / "Rouge"
    tempo_next_color: str | None              # tomorrow
    is_hc: bool                               # in HC window right now
    forecast_today_kwh: float | None          # Forecast.Solar today total
    forecast_tomorrow_kwh: float | None       # Forecast.Solar tomorrow
    energy_needed_kwh: float                  # from thermo.energy_budget_until_morning
    cycle: CycleAccumulator                   # current cycle so far
    signal_on_at: datetime | None             # when the signal switch was turned on, or None
    signal_currently_on: bool = False         # observed contactor state
    boost_currently_on: bool = False          # observed appliance target == boost target


@dataclass(frozen=True)
class Thresholds:
    hard_floor_c: float = 48.0
    target_top_c: float = 54.0
    morning_window_start: time = time(4, 0)
    morning_window_end: time = time(7, 0)
    surplus_safety_margin_w: float = 200.0
    rouge_hp_blended_cap_eur_per_kwh: float = 0.118125  # 0.75 × Rouge HC
    sunny_forecast_safety_factor: float = 1.5
    # Once signal_switch is turned ON, the Atlantic DHWP runs for at least
    # this many minutes regardless of what we'd prefer. The decision module
    # mirrors that commitment so we don't issue futile turn_off requests.
    signal_min_hold_minutes: int = 120
    # Boost mode (55°C target instead of 54°C) is opportunistic: applied
    # when surplus comfortably exceeds the heater's draw by this much.
    boost_extra_surplus_w: float = 1000.0
    # HC checkpoint times (local time of day). The decision module never
    # starts a new HC cycle outside these instants; it relies on the
    # coordinator running at least once near each. ±5 min tolerance.
    hc_rescue_time: time = time(22, 30)
    hc_main_time: time = time(1, 0)
    hc_last_chance_time: time = time(3, 30)
    hc_checkpoint_tolerance_min: int = 5
    # Tank-middle floors used by the three checkpoints.
    hc_rescue_floor_c: float = 42.0       # 22:30: only if tank is depleted
    hc_main_floor_c: float = 48.0         # 01:00: main nightly top-up
    hc_last_chance_floor_c: float = 47.0  # 03:30: catches forecast revisions
    # Outdoor coldness bonus — below this we lack data, so heat as a hedge.
    hc_cold_outdoor_c: float = 5.0
    # Heater self-idle detection. The Atlantic DHWP draws ~800 W when
    # actively heating and < 20 W in standby; 100 W is a comfortable
    # threshold. Used only past the 2 h hardware floor to decide whether
    # we can safely release the contactor (mirrors the legacy YAML
    # automation `Chauffe-eau : Extinction Fin de Cycle`).
    heater_idle_threshold_w: float = 100.0
    # Asymmetric grid threshold for "continue heating" vs "start heating":
    #
    # - To START: need clear surplus (delta-aware, via _surplus_covers_heater
    #   with `surplus_safety_margin_w`).
    # - To CONTINUE: only stop if we're clearly importing. Once the heater
    #   is already drawing, the question isn't "would adding 800W cause
    #   import" but "are we currently importing significantly?" — a much
    #   more lenient check.
    #
    # +200 W mirrors the legacy `Chauffe-eau : Extinction Solaire` rule
    # (import > 200 W for 15 min → off). The legacy YAML handles the
    # *sustained* part; we just need an instantaneous threshold that
    # doesn't flap on transients.
    surplus_continue_margin_w: float = 200.0


@dataclass(frozen=True)
class Decision:
    signal_switch_on: bool       # the contactor — 2h committed once turned on
    boost_mode_on: bool           # 55°C target (True) vs 54°C eco (False); responsive
    reason: str
    action: str                  # "wait" / "heat_solar" / "heat_hc" / "boost" / "hard_floor"


# Legacy alias for backwards compat in callers that only check on/off:
def heater_on(d: Decision) -> bool:
    return d.signal_switch_on


def _in_morning_window(now: datetime, thr: Thresholds) -> bool:
    t = now.time()
    return thr.morning_window_start <= t <= thr.morning_window_end


def _surplus_covers_heater(i: Inputs, thr: Thresholds) -> bool:
    """Solar surplus large enough to run the heater without pulling from grid?

    The heater is either off (heater_power_w ≈ 0) or running (~800 W).
    Either way, the check is: "if we add HEATER_NOMINAL_W of load, does the
    grid stay in export with the safety margin?"

    expected_grid_after = grid_smooth + (HEATER_NOMINAL_W - heater_power_w)
                       < -safety_margin
    """
    delta = HEATER_NOMINAL_W - i.heater_power_w
    expected = i.grid_smooth_w + delta
    return expected < -thr.surplus_safety_margin_w


def _solar_share_now(i: Inputs) -> float:
    """0..1 — fraction of *current* heater draw covered by solar.

    Used by the coordinator to attribute kWh between solar and grid in the
    cycle accumulator; not part of the decision itself.
    """
    if i.heater_power_w <= 0:
        return 1.0 if i.grid_smooth_w <= 0 else 0.0
    if i.grid_smooth_w <= 0:
        return 1.0
    covered = max(0.0, i.heater_power_w - i.grid_smooth_w)
    return max(0.0, min(1.0, covered / i.heater_power_w))


def _in_signal_hold(i: Inputs, thr: Thresholds) -> bool:
    """True iff the contactor must remain closed right now.

    The 2 h `signal_min_hold_minutes` is a MINIMUM the Atlantic DHWP
    commits to running for; it is NOT a maximum we cap at. Heating a
    fully-charged tank (top + middle) reliably takes longer than 2 h —
    the appliance's top probe hits target first, then must idle while
    convection lets the middle catch up, then resumes briefly, and so on.
    Cutting the contactor at 2 h interrupts that pattern.

    Policy:
      - First 2 h after switch-on: must stay on (mirror the hardware floor).
      - Past 2 h, HC: stay on. The legacy YAML automation
        `Chauffe-eau : Extinction Fin de Cycle` (power < 100 W for 5 min)
        owns the off-decision — that's the correct end-of-cycle signal
        since the middle probe may still be charging via convection.
      - Past 2 h, HP: stay on unless `grid_smooth_w` exceeds the
        permissive *continue* threshold (default +200 W). This is the
        legacy "Extinction Solaire" instantaneous threshold; that YAML
        owns the *sustained* (15-min) cleanup. Asymmetric on purpose:
        starting a cycle requires clear surplus; continuing one only
        needs to not be actively importing. If HC ended with the
        appliance still drawing, this still releases when we're
        actually importing — Rouge HP cap is handled in the calling
        Rouge branch before reaching here.
    """
    if not i.signal_currently_on or i.signal_on_at is None:
        return False
    elapsed_min = (i.now - i.signal_on_at).total_seconds() / 60.0
    # Hardware floor — non-negotiable.
    if elapsed_min < thr.signal_min_hold_minutes:
        return True
    # Past the hardware floor.
    if i.is_hc:
        # During HC the legacy YAML automation (power < 100 W for 5 min)
        # owns the off-decision. Stay on regardless of appliance state —
        # the middle probe may still be charging via convection even
        # while the top probe pauses.
        return True
    # HP, past 2 h. Use the permissive *continue* threshold — once the
    # heater is already running, the question isn't "would adding 800 W
    # cause import?" but "are we currently importing significantly?".
    # Releasing on the strict start-check cuts the cycle off the moment
    # grid balances near zero (2026-05-18: heater 664 W, grid −10 W →
    # strict check thought we lacked surplus, dropped the contactor,
    # even though we were almost-perfectly solar-balanced). The legacy
    # YAML `Chauffe-eau : Extinction Solaire` (import > 200 W for 15 min)
    # handles sustained-import cleanup. If HC ends while the appliance
    # is still drawing AND we're now actually importing, this still
    # releases correctly, preventing HP-priced grid burn.
    return i.grid_smooth_w < thr.surplus_continue_margin_w


def _abundant_surplus_for_boost(i: Inputs, thr: Thresholds) -> bool:
    """Surplus exceeds heater draw by *a lot* — safe to bump the target to 55°C."""
    # If the heater is already on we know its draw exactly; if not, assume
    # nominal. Need grid + delta < -(margin + boost_extra).
    delta = HEATER_NOMINAL_W - i.heater_power_w
    expected = i.grid_smooth_w + delta
    return expected < -(thr.surplus_safety_margin_w + thr.boost_extra_surplus_w)


def _near(now_t: time, target: time, tol_min: int) -> bool:
    """now_t within ±tol_min of target (same-day, no wrap — checkpoints are
    well-separated and never near midnight in either direction by more than
    a few minutes)."""
    n = now_t.hour * 60 + now_t.minute
    t = target.hour * 60 + target.minute
    return abs(n - t) <= tol_min


def _hc_checkpoint_action(i: Inputs, thr: Thresholds) -> Decision | None:
    """Return a heat_hc Decision iff the current time matches one of the
    three HC checkpoints AND its condition is met. Otherwise None."""
    now_t = i.now.time()
    mid = i.tank_middle_c if i.tank_middle_c is not None else 0.0
    outdoor_cold = i.outdoor_c is not None and i.outdoor_c < thr.hc_cold_outdoor_c
    tomorrow_will_cover = will_likely_cover_heating_need(
        i.forecast_tomorrow_kwh,
        i.energy_needed_kwh,
        thr.sunny_forecast_safety_factor,
    )
    # `False` (not None) means forecast known and *not* enough → poor day.
    forecast_poor = tomorrow_will_cover is False

    if _near(now_t, thr.hc_rescue_time, thr.hc_checkpoint_tolerance_min):
        if mid < thr.hc_rescue_floor_c:
            return Decision(
                True, False,
                f"HC 22:30 rescue · tank_middle {mid:.1f}°C < "
                f"{thr.hc_rescue_floor_c:.0f}°C — depleted",
                "heat_hc",
            )
        return None

    if _near(now_t, thr.hc_main_time, thr.hc_checkpoint_tolerance_min):
        if mid < thr.hc_main_floor_c:
            return Decision(
                True, False,
                f"HC 01:00 main · tank_middle {mid:.1f}°C < "
                f"{thr.hc_main_floor_c:.0f}°C",
                "heat_hc",
            )
        if forecast_poor:
            return Decision(
                True, False,
                f"HC 01:00 main · tomorrow's PV "
                f"({i.forecast_tomorrow_kwh:.1f} kWh) won't cover need "
                f"({i.energy_needed_kwh:.2f} kWh × safety) — preheat",
                "heat_hc",
            )
        if outdoor_cold:
            return Decision(
                True, False,
                f"HC 01:00 main · outdoor {i.outdoor_c:.1f}°C "
                f"< {thr.hc_cold_outdoor_c:.0f}°C winter hedge",
                "heat_hc",
            )
        return None

    if _near(now_t, thr.hc_last_chance_time, thr.hc_checkpoint_tolerance_min):
        if mid < thr.hc_last_chance_floor_c:
            return Decision(
                True, False,
                f"HC 03:30 last chance · tank_middle {mid:.1f}°C < "
                f"{thr.hc_last_chance_floor_c:.0f}°C",
                "heat_hc",
            )
        return None

    return None


def decide(i: Inputs, thr: Thresholds) -> Decision:
    """Public entrypoint. Wraps `_decide_impl` with a *boost freeze*:

    The Atlantic DHWP receives target-temperature changes via Cozytouch
    each time the integration calls `water_heater.set_temperature`.
    Toggling boost mid-cycle thrashes the appliance's setpoint (54↔55,
    or 50↔54.5 depending on user config) and observably causes the
    compressor to stop short of the middle-probe target (see
    2026-05-13 incident: 6 setpoint changes in 7 min → compressor
    stopped at middle 50.1 °C, well below the 54.5 °C target).

    Rule: while the signal switch is currently on, boost is whatever
    it currently is — never recomputed. Boost is only freshly decided
    at the transition signal off→on (i.e., when this function is called
    with `signal_currently_on=False` and decides `signal_switch_on=True`).
    """
    d = _decide_impl(i, thr)
    if i.signal_currently_on and d.signal_switch_on and d.boost_mode_on != i.boost_currently_on:
        # Cycle in progress — freeze boost at its current value.
        return Decision(
            signal_switch_on=d.signal_switch_on,
            boost_mode_on=i.boost_currently_on,
            reason=f"{d.reason} · boost frozen for cycle (current={i.boost_currently_on})",
            action=d.action,
        )
    return d


def _decide_impl(i: Inputs, thr: Thresholds) -> Decision:
    # 1. Manual.
    if i.mode == MODE_OFF:
        return Decision(False, False, "manual=off", "wait")
    if i.mode == MODE_BOOST:
        return Decision(True, True, "manual=boost", "boost")
    if i.mode == MODE_HC_ONLY:
        on = i.is_hc and i.energy_needed_kwh > 0
        # Honour 2h hold even in manual HC-only mode.
        if not on and _in_signal_hold(i, thr):
            return Decision(
                True, False, "manual=hc_only · honouring 2h signal hold", "heat_hc"
            )
        return Decision(
            on, False,
            "manual=hc_only · HC window + need" if on
            else "manual=hc_only · waiting for HC or full",
            "heat_hc" if on else "wait",
        )
    if i.mode == MODE_SOLAR_ONLY:
        has_surplus = _surplus_covers_heater(i, thr)
        # If we're holding the contactor due to the 2h commit, stay on but
        # let boost reflect current surplus.
        if _in_signal_hold(i, thr):
            return Decision(
                True,
                _abundant_surplus_for_boost(i, thr),
                "manual=solar_only · 2h signal hold",
                "heat_solar",
            )
        if has_surplus:
            return Decision(
                True,
                _abundant_surplus_for_boost(i, thr),
                "manual=solar_only · surplus covers heater",
                "heat_solar",
            )
        return Decision(False, False, "manual=solar_only · insufficient surplus", "wait")

    # 2. Hard floor breach — non-negotiable safety net.
    # Skip the check entirely if the floor sensor is unavailable, rather
    # than treating a missing reading as 0 °C (which would force heat all
    # morning, every morning).
    if (
        i.tank_middle_c is not None
        and i.tank_middle_c < thr.hard_floor_c
        and _in_morning_window(i.now, thr)
    ):
        return Decision(
            True, False,  # boost off — eco is enough for safety
            f"hard floor breach: tank_middle {i.tank_middle_c:.1f}°C < "
            f"{thr.hard_floor_c:.0f}°C in morning window",
            "hard_floor",
        )

    # If we're inside the 2h signal hold, we cannot turn off — but boost is
    # still freely toggleable. Pick the best surplus-aware action.
    if _in_signal_hold(i, thr):
        boost = _abundant_surplus_for_boost(i, thr)
        elapsed = (i.now - i.signal_on_at).total_seconds() / 60.0  # type: ignore[union-attr]
        # Pick an action label that doesn't lie about the heater being on:
        #   - solar if we have surplus to boost on,
        #   - heat_hc if we're in the HC window,
        #   - otherwise hold_signal (heater is running on grid HP because
        #     we committed earlier; this is rare but explicit).
        if boost:
            action = "heat_solar"
        elif i.is_hc:
            action = "heat_hc"
        else:
            action = "hold_signal"
        return Decision(
            True, boost,
            f"signal 2h hold (started {elapsed:.0f} min ago)",
            action,
        )

    # 3. Tempo Rouge HP — blended-cost guard. Solar-only, with worst-case projection.
    if i.tempo_color == "Rouge" and not i.is_hc:
        # Once we commit signal_switch on Rouge HP, we're stuck for 2h.
        # Worst case: the entire 2h is HP price (cloud cover).
        worst_case_kwh = HEATER_NOMINAL_W * 2.0 / 1000.0  # 2 h at 800 W
        worst_case_ok = can_still_meet_blended_cap(
            i.cycle,
            worst_case_kwh,
            0.706,
            thr.rouge_hp_blended_cap_eur_per_kwh,
        )
        if _surplus_covers_heater(i, thr) and worst_case_ok:
            return Decision(
                True, _abundant_surplus_for_boost(i, thr),
                "Rouge HP · solar covers + 2h worst-case blended cap safe",
                "heat_solar",
            )
        return Decision(False, False, "Rouge HP · not safe (blended cap or no surplus)", "wait")

    # 4. HC window — three time-gated checkpoints, never else.
    #
    # Empirical cooldown rate (10 days, 267 quiet 3 h windows): ≈0.4 °C/h
    # almost flat across outdoor 9–38 °C. So a single middle-tank floor of
    # 48 °C buys ~6 °C of margin over 6 h overnight loss, which is enough
    # for morning comfort. Outdoor temp gets a tiny safety bonus below 5 °C
    # (deep winter, where we have no calibration data and want a hedge).
    #
    # Rationale for time gating: starting a 2 h HC cycle right at 22:00
    # finishes at 00:00 and leaves the tank to passively lose ~2.5 °C
    # before morning use. Pushing the main checkpoint to 01:00 puts the
    # cycle end at 03:00 — minimal cooldown before 06:00.
    if i.is_hc and i.energy_needed_kwh > 0:
        action = _hc_checkpoint_action(i, thr)
        if action is not None:
            return action
        return Decision(
            False, False,
            "HC window · no checkpoint matched — let solar do it tomorrow",
            "wait",
        )
    if i.is_hc:
        return Decision(False, False, "HC window · tank already full", "wait")

    # 5. HP window, not Rouge. Solar-only candidate.
    if _surplus_covers_heater(i, thr):
        forecast_says_cover = will_likely_cover_heating_need(
            i.forecast_today_kwh,
            i.energy_needed_kwh,
            thr.sunny_forecast_safety_factor,
        )
        if forecast_says_cover is False and i.energy_needed_kwh > 0:
            # Surplus is here now but forecast says it won't last. Don't
            # commit the 2h contactor cycle — wait for HC tonight.
            return Decision(
                False, False,
                "HP · surplus exists but forecast too low — wait for HC",
                "wait",
            )
        return Decision(
            True, _abundant_surplus_for_boost(i, thr),
            "HP · solar surplus covers heater",
            "heat_solar",
        )

    return Decision(False, False, "HP · no surplus — wait", "wait")
