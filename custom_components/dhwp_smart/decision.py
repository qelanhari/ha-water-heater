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


@dataclass(frozen=True)
class Thresholds:
    hard_floor_c: float = 48.0
    target_top_c: float = 54.0
    morning_window_start: time = time(4, 0)
    morning_window_end: time = time(7, 0)
    surplus_safety_margin_w: float = 200.0
    rouge_hp_blended_cap_eur_per_kwh: float = 0.118125  # 0.75 × Rouge HC
    sunny_forecast_safety_factor: float = 1.5


@dataclass(frozen=True)
class Decision:
    heater_on: bool
    reason: str
    action: str             # "wait" / "heat_solar" / "heat_hc" / "boost" / "hard_floor"


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


def decide(i: Inputs, thr: Thresholds) -> Decision:
    # 1. Manual.
    if i.mode == MODE_OFF:
        return Decision(False, "manual=off", "wait")
    if i.mode == MODE_BOOST:
        return Decision(True, "manual=boost", "boost")
    if i.mode == MODE_HC_ONLY:
        if i.is_hc and i.energy_needed_kwh > 0:
            return Decision(True, "manual=hc_only · HC window + need", "heat_hc")
        return Decision(False, "manual=hc_only · waiting for HC or full", "wait")
    if i.mode == MODE_SOLAR_ONLY:
        if _surplus_covers_heater(i, thr):
            return Decision(True, "manual=solar_only · surplus covers heater", "heat_solar")
        return Decision(False, "manual=solar_only · insufficient surplus", "wait")

    # 2. Hard floor breach — non-negotiable safety net.
    if i.tank_middle_c < thr.hard_floor_c and _in_morning_window(i.now, thr):
        return Decision(
            True,
            f"hard floor breach: tank_middle {i.tank_middle_c:.1f}°C < "
            f"{thr.hard_floor_c:.0f}°C in morning window",
            "hard_floor",
        )

    # 3. Tempo Rouge HP — blended-cost guard.
    if i.tempo_color == "Rouge" and not i.is_hc:
        # 15-minute worst-case projection: if the next 15 min were ALL HP
        # (cloud cover), would the cycle still meet the blended cap?
        worst_case_kwh = HEATER_NOMINAL_W * 0.25 / 1000.0  # 15 min at 800 W
        worst_case_ok = can_still_meet_blended_cap(
            i.cycle,
            worst_case_kwh,
            0.706,
            thr.rouge_hp_blended_cap_eur_per_kwh,
        )
        if _surplus_covers_heater(i, thr) and worst_case_ok:
            return Decision(
                True,
                "Rouge HP · solar covers + blended cap still safe",
                "heat_solar",
            )
        return Decision(False, "Rouge HP · not safe (blended cap or no surplus)", "wait")

    # 4. HC window.
    if i.is_hc:
        if i.energy_needed_kwh > 0:
            return Decision(
                True,
                f"HC window · {i.energy_needed_kwh:.2f} kWh still needed",
                "heat_hc",
            )
        return Decision(False, "HC window · tank already full", "wait")

    # 5. HP window, not Rouge. Solar-only candidate.
    if _surplus_covers_heater(i, thr):
        # Should we even bother? If forecast says PV will easily cover
        # remaining need, sure. If it's cloudy and HC is enough, wait.
        forecast_says_cover = will_likely_cover_heating_need(
            i.forecast_today_kwh,
            i.energy_needed_kwh,
            thr.sunny_forecast_safety_factor,
        )
        if forecast_says_cover is False and i.energy_needed_kwh > 0:
            return Decision(
                False,
                "HP · surplus exists but forecast too low — wait for HC",
                "wait",
            )
        return Decision(True, "HP · solar surplus covers heater", "heat_solar")

    return Decision(False, "HP · no surplus — wait", "wait")
