"""Solar-yield forecasting helpers.

Designed to consume Forecast.Solar's `sensor.energy_production_today` and
`sensor.energy_production_tomorrow` (Wh totals over the whole day).
Falls back gracefully when no forecast is available.

Pure functions, no HA imports.
"""

from __future__ import annotations


def expected_pv_kwh_today(forecast_wh: float | None) -> float | None:
    """Convert Wh → kWh; None if the forecast is unavailable."""
    if forecast_wh is None:
        return None
    return max(0.0, forecast_wh / 1000.0)


def expected_pv_kwh_tomorrow(forecast_wh: float | None) -> float | None:
    return expected_pv_kwh_today(forecast_wh)


def is_cloudy_day(
    forecast_kwh: float | None,
    panel_peak_kwp: float,
    cloudy_ratio_threshold: float = 0.30,
) -> bool | None:
    """True if the forecast says today (or tomorrow) will yield less than
    `cloudy_ratio_threshold` of theoretical 24h.

    Theoretical 24h is approximated as `panel_peak_kwp × peak_sun_hours`,
    where peak_sun_hours ~5 in summer, ~2 in winter. We use 4 as a sane
    mid-year default; the threshold is loose enough that the seasonal
    shift doesn't matter much for "is it cloudy?" classification.
    """
    if forecast_kwh is None or panel_peak_kwp <= 0:
        return None
    theoretical = panel_peak_kwp * 4.0
    return forecast_kwh < cloudy_ratio_threshold * theoretical


def will_likely_cover_heating_need(
    forecast_kwh: float | None,
    energy_needed_kwh: float,
    safety_factor: float = 1.5,
) -> bool | None:
    """True if today's predicted PV is comfortably above the heating need.

    `safety_factor` (default 1.5) ensures we don't trigger heating purely on
    a tight forecast; we want at least 50 % more than needed before we wait
    for solar instead of running HC tonight.
    """
    if forecast_kwh is None:
        return None
    if energy_needed_kwh <= 0:
        return True
    return forecast_kwh >= safety_factor * energy_needed_kwh
