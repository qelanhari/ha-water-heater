"""Per-weekday usage EMA + outdoor-temperature coefficient.

Two signals feed the daily-kWh estimator:

1. Daily heat-pump energy consumption (delta of the lifetime accumulator
   between midnight rollovers). Proxy for "how much hot water was drawn
   today + how much standing loss". Persisted per-weekday as an EMA.

2. Outdoor temperature coefficient. Cold days mean colder mains water and
   longer/hotter showers ⇒ more reheating energy. Modelled as a simple
   linear coefficient learnt from history.

All pure — no HA imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

REFERENCE_OUTDOOR_C: float = 15.0  # the temperature at which the EMA values are "baseline"


@dataclass(frozen=True)
class PatternState:
    """Persisted snapshot. Indexed by weekday 0..6 (Monday=0).

    `daily_kwh_ema[w]` = exponential moving average of the heater's daily
    energy consumption on weekday `w`, normalised to `REFERENCE_OUTDOOR_C`.
    `outdoor_coef` = additional kWh of usage per °C below reference (always
    >= 0; cold days ⇒ more usage).
    `samples_n` = total observations used so far. Drives "confidence":
    until ~14 samples we still partly trust a hard-coded fallback.
    """

    daily_kwh_ema: tuple[float, ...] = (3.0,) * 7   # 3 kWh/day = reasonable starting estimate
    outdoor_coef: float = 0.10                       # +0.1 kWh per °C below 15°C, gentle default
    samples_n: int = 0


def _normalise_by_outdoor(kwh: float, outdoor_c: float, coef: float) -> float:
    """Subtract the cold-weather component to get a temperature-neutral value."""
    cold = max(0.0, REFERENCE_OUTDOOR_C - outdoor_c)
    return max(0.0, kwh - cold * coef)


def _denormalise_by_outdoor(kwh_ref: float, outdoor_c: float, coef: float) -> float:
    """Add the cold-weather component back for a forecast at `outdoor_c`."""
    cold = max(0.0, REFERENCE_OUTDOOR_C - outdoor_c)
    return kwh_ref + cold * coef


def update_pattern(
    state: PatternState,
    weekday: int,
    observed_kwh: float,
    outdoor_avg_c: float,
    alpha: float = 0.25,
) -> PatternState:
    """Fold a new observation into the per-weekday EMA.

    `observed_kwh` is the heater's energy consumption for the closed day,
    and `outdoor_avg_c` is the day-average outdoor temperature. We
    normalise away the cold-weather effect before updating the EMA, then
    refit the `outdoor_coef` very gently using the residual.
    """
    weekday = int(weekday) % 7
    ref_kwh = _normalise_by_outdoor(observed_kwh, outdoor_avg_c, state.outdoor_coef)

    new_emas = list(state.daily_kwh_ema)
    new_emas[weekday] = alpha * ref_kwh + (1.0 - alpha) * new_emas[weekday]

    # Refit outdoor_coef *gently*. The cold-weather residual is what
    # `observed_kwh - new_emas[weekday]` would imply, in kWh per °C below
    # reference. We move the coefficient at most 0.02 per sample, capped to
    # [0.0, 0.5] kWh/°C.
    cold = max(0.0, REFERENCE_OUTDOOR_C - outdoor_avg_c)
    new_coef = state.outdoor_coef
    if cold > 0:
        residual_kwh = max(0.0, observed_kwh - new_emas[weekday])
        implied_coef = residual_kwh / cold
        step = max(-0.02, min(0.02, implied_coef - state.outdoor_coef))
        new_coef = max(0.0, min(0.5, state.outdoor_coef + step))

    return replace(
        state,
        daily_kwh_ema=tuple(new_emas),
        outdoor_coef=new_coef,
        samples_n=state.samples_n + 1,
    )


def expected_kwh_for_day(
    state: PatternState,
    weekday: int,
    outdoor_avg_c: float,
) -> float:
    """Predicted kWh consumption for a given weekday + outdoor temperature."""
    weekday = int(weekday) % 7
    ref = state.daily_kwh_ema[weekday]
    return max(0.0, _denormalise_by_outdoor(ref, outdoor_avg_c, state.outdoor_coef))


def confidence(state: PatternState) -> float:
    """0.0..1.0 estimate of how much we trust the EMAs. Returns 1.0 after ~14 samples."""
    return min(1.0, state.samples_n / 14.0)
