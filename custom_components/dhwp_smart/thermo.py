"""Tank thermodynamics: energy-to-heat, standing heat loss, predicted drop.

Pure functions, no HA imports.
"""

from __future__ import annotations

# Specific heat of water: 4.186 kJ / (kg·K) → 1.163e-3 kWh / (L·K)
TANK_SPECIFIC_HEAT_KWH_PER_L_K: float = 1.163e-3

# Default U·A for a 270 L Atlantic DHWP. Atlantic spec sheets put standing
# losses around 1.5–1.8 W/K. We use 1.6 as a sane mid-point.
DEFAULT_U_VALUE_W_PER_K: float = 1.6


def energy_to_heat_kwh(
    current_top_c: float,
    target_top_c: float,
    capacity_l: int = 270,
) -> float:
    """kWh required to bring the tank top from `current_top_c` to `target_top_c`.

    Treats the tank as well-mixed (which it isn't — heat pump heats from the
    bottom up, so the top is always warmer). Good enough for budgeting
    decisions.
    """
    delta = max(0.0, target_top_c - current_top_c)
    return capacity_l * TANK_SPECIFIC_HEAT_KWH_PER_L_K * delta


def heat_loss_w(
    tank_top_c: float,
    garage_c: float,
    u_value: float = DEFAULT_U_VALUE_W_PER_K,
) -> float:
    """Standing heat loss in W. Floored at 0 (cold garage warming the tank is
    a degenerate case we ignore)."""
    return max(0.0, u_value * (tank_top_c - garage_c))


def predicted_temp_drop_c(
    tank_top_c: float,
    garage_c: float,
    hours: float,
    capacity_l: int = 270,
    u_value: float = DEFAULT_U_VALUE_W_PER_K,
) -> float:
    """How much will the tank cool over `hours` with no heating and no draw?"""
    if hours <= 0:
        return 0.0
    loss_w = heat_loss_w(tank_top_c, garage_c, u_value)
    loss_kwh = loss_w * hours / 1000.0
    return loss_kwh / (capacity_l * TANK_SPECIFIC_HEAT_KWH_PER_L_K)


def energy_budget_until_morning_kwh(
    current_top_c: float,
    target_top_c: float,
    floor_c: float,
    garage_c: float,
    hours_to_morning: float,
    expected_usage_kwh: float,
    capacity_l: int = 270,
    u_value: float = DEFAULT_U_VALUE_W_PER_K,
) -> float:
    """Total kWh we need to inject before the morning deadline.

    = (target - current) heating + standing loss over the period
                       + expected user draw
    Returns 0 if we're already above target AND wouldn't drop below floor
    even with the expected draw.
    """
    base_heat = energy_to_heat_kwh(current_top_c, target_top_c, capacity_l)
    standing_loss_kwh = (
        heat_loss_w(max(target_top_c, current_top_c), garage_c, u_value)
        * hours_to_morning
        / 1000.0
    )
    needed = base_heat + standing_loss_kwh + max(0.0, expected_usage_kwh)
    # If we're already above target AND projected end is above floor, no
    # need to heat further.
    projected_end_c = (
        current_top_c
        - predicted_temp_drop_c(current_top_c, garage_c, hours_to_morning, capacity_l, u_value)
        - (expected_usage_kwh / (capacity_l * TANK_SPECIFIC_HEAT_KWH_PER_L_K))
    )
    if current_top_c >= target_top_c and projected_end_c >= floor_c:
        return 0.0
    return max(0.0, needed)
