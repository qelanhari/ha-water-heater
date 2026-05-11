"""The decision brain for the Smart DHWP integration.

Pure function over a snapshot of inputs + thresholds; returns a Decision
that the coordinator turns into a `switch.turn_on/off` service call on the
configured contactor entity.

Priority chain (top wins):

1. Manual mode (off / hc_only / solar_only / boost) — sovereign.
2. Hard floor breach in the morning window — force ON regardless of cost.
3. Tempo Rouge HP — solar OK only if the worst-case projected blended
   cost stays under `rouge_hp_blended_cap_eur_per_kwh`. Otherwise OFF.
4. HC window — ON if any energy still needed before the morning deadline.
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
    tank_middle_c: float                      # current "floor" sensor
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
    """True iff the signal switch is currently on AND was turned on less than
    `signal_min_hold_minutes` ago. We mirror this commitment in the decision
    module so we never propose to turn off the contactor before the heater's
    own 2-hour hardware cycle has elapsed."""
    if not i.signal_currently_on or i.signal_on_at is None:
        return False
    elapsed_min = (i.now - i.signal_on_at).total_seconds() / 60.0
    return elapsed_min < thr.signal_min_hold_minutes


def _abundant_surplus_for_boost(i: Inputs, thr: Thresholds) -> bool:
    """Surplus exceeds heater draw by *a lot* — safe to bump the target to 55°C."""
    # If the heater is already on we know its draw exactly; if not, assume
    # nominal. Need grid + delta < -(margin + boost_extra).
    delta = HEATER_NOMINAL_W - i.heater_power_w
    expected = i.grid_smooth_w + delta
    return expected < -(thr.surplus_safety_margin_w + thr.boost_extra_surplus_w)


def decide(i: Inputs, thr: Thresholds) -> Decision:
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
    if i.tank_middle_c < thr.hard_floor_c and _in_morning_window(i.now, thr):
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
        return Decision(
            True, boost,
            f"signal 2h hold (started {elapsed:.0f} min ago)",
            "heat_solar" if boost else "heat_hc" if i.is_hc else "wait",
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

    # 4. HC window. HC is sustained by definition, so the 2h commit is fine.
    if i.is_hc:
        if i.energy_needed_kwh > 0:
            return Decision(
                True, False,    # eco target — HC time, save what we can
                f"HC window · {i.energy_needed_kwh:.2f} kWh still needed",
                "heat_hc",
            )
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
