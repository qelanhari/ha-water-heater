"""Constants for the Smart DHWP integration."""

from __future__ import annotations

DOMAIN = "dhwp_smart"
PLATFORMS: list[str] = ["select", "sensor", "binary_sensor", "button"]

# --- Entity bindings (set during config flow) -------------------------------
CONF_SIGNAL_SWITCH_ENTITY = "signal_switch_entity"
CONF_WATER_HEATER_ENTITY = "water_heater_entity"   # the water_heater entity (target temp control)
CONF_TANK_TOP_TEMP_ENTITY = "tank_top_temp_entity"
CONF_TANK_MIDDLE_TEMP_ENTITY = "tank_middle_temp_entity"
CONF_HEATER_POWER_ENTITY = "heater_power_entity"
CONF_HEATER_BOOSTER_POWER_ENTITY = "heater_booster_power_entity"
CONF_HEATER_ENERGY_METER_ENTITY = "heater_energy_meter_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_PV_POWER_ENTITY = "pv_power_entity"
CONF_GARAGE_TEMP_ENTITY = "garage_temp_entity"
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"
CONF_TEMPO_COLOR_ENTITY = "tempo_color_entity"
CONF_TEMPO_NEXT_COLOR_ENTITY = "tempo_next_color_entity"
CONF_TEMPO_IS_HC_ENTITY = "tempo_is_hc_entity"
CONF_FORECAST_TODAY_ENTITY = "forecast_today_entity"
CONF_FORECAST_TOMORROW_ENTITY = "forecast_tomorrow_entity"

# --- Tank & thresholds ------------------------------------------------------
CONF_TANK_CAPACITY_L = "tank_capacity_l"
CONF_TANK_TARGET_TOP_C = "tank_target_top_c"
CONF_TANK_MORNING_FLOOR_C = "tank_morning_floor_c"
CONF_MORNING_DEADLINE_HOUR = "morning_deadline_hour"
CONF_MORNING_DEADLINE_MINUTE = "morning_deadline_minute"
CONF_ECO_TARGET_C = "eco_target_c"
CONF_BOOST_TARGET_C = "boost_target_c"
CONF_SIGNAL_MIN_HOLD_MINUTES = "signal_min_hold_minutes"
CONF_BOOST_EXTRA_SURPLUS_W = "boost_extra_surplus_w"

# --- Cost & anti-flap -------------------------------------------------------
CONF_ROUGE_HP_BLENDED_CAP = "rouge_hp_blended_cap"
CONF_SURPLUS_SAFETY_MARGIN_W = "surplus_safety_margin_w"
CONF_MIN_DWELL_SECONDS = "min_dwell_seconds"
CONF_SOLAR_SMOOTH_ALPHA = "solar_smooth_alpha"

# --- Defaults ---------------------------------------------------------------
DEFAULT_TANK_CAPACITY_L = 270
DEFAULT_TANK_TARGET_TOP_C = 54.0
DEFAULT_TANK_MORNING_FLOOR_C = 48.0
DEFAULT_MORNING_DEADLINE_HOUR = 6
DEFAULT_MORNING_DEADLINE_MINUTE = 30
DEFAULT_ECO_TARGET_C = 54.0
DEFAULT_BOOST_TARGET_C = 55.0
DEFAULT_SIGNAL_MIN_HOLD_MINUTES = 120
DEFAULT_BOOST_EXTRA_SURPLUS_W = 1000.0
DEFAULT_ROUGE_HP_BLENDED_CAP = 0.118125  # 75% of Rouge HC (0.1575)
DEFAULT_SURPLUS_SAFETY_MARGIN_W = 200.0
DEFAULT_MIN_DWELL_SECONDS = 60.0
DEFAULT_SOLAR_SMOOTH_ALPHA = 0.3

# --- Coordinator ------------------------------------------------------------
UPDATE_INTERVAL_SECONDS = 30

# --- Modes (re-exported from decision.py for entity layer) ------------------
from .decision import (  # noqa: E402
    MODE_AUTO,
    MODE_BOOST,
    MODE_HC_ONLY,
    MODE_OFF,
    MODE_SOLAR_ONLY,
    MODES,
    MANUAL_MODES,
)

__all__ = [
    "DOMAIN", "PLATFORMS",
    "CONF_SIGNAL_SWITCH_ENTITY", "CONF_WATER_HEATER_ENTITY",
    "CONF_TANK_TOP_TEMP_ENTITY",
    "CONF_TANK_MIDDLE_TEMP_ENTITY", "CONF_HEATER_POWER_ENTITY",
    "CONF_HEATER_BOOSTER_POWER_ENTITY", "CONF_HEATER_ENERGY_METER_ENTITY",
    "CONF_GRID_POWER_ENTITY", "CONF_PV_POWER_ENTITY",
    "CONF_GARAGE_TEMP_ENTITY", "CONF_OUTDOOR_TEMP_ENTITY",
    "CONF_TEMPO_COLOR_ENTITY", "CONF_TEMPO_NEXT_COLOR_ENTITY",
    "CONF_TEMPO_IS_HC_ENTITY", "CONF_FORECAST_TODAY_ENTITY",
    "CONF_FORECAST_TOMORROW_ENTITY",
    "CONF_TANK_CAPACITY_L", "CONF_TANK_TARGET_TOP_C",
    "CONF_TANK_MORNING_FLOOR_C", "CONF_MORNING_DEADLINE_HOUR",
    "CONF_MORNING_DEADLINE_MINUTE", "CONF_ECO_TARGET_C",
    "CONF_BOOST_TARGET_C", "CONF_SIGNAL_MIN_HOLD_MINUTES",
    "CONF_BOOST_EXTRA_SURPLUS_W",
    "CONF_ROUGE_HP_BLENDED_CAP", "CONF_SURPLUS_SAFETY_MARGIN_W",
    "CONF_MIN_DWELL_SECONDS", "CONF_SOLAR_SMOOTH_ALPHA",
    "DEFAULT_TANK_CAPACITY_L", "DEFAULT_TANK_TARGET_TOP_C",
    "DEFAULT_TANK_MORNING_FLOOR_C", "DEFAULT_MORNING_DEADLINE_HOUR",
    "DEFAULT_MORNING_DEADLINE_MINUTE", "DEFAULT_ECO_TARGET_C",
    "DEFAULT_BOOST_TARGET_C", "DEFAULT_SIGNAL_MIN_HOLD_MINUTES",
    "DEFAULT_BOOST_EXTRA_SURPLUS_W", "DEFAULT_ROUGE_HP_BLENDED_CAP",
    "DEFAULT_SURPLUS_SAFETY_MARGIN_W", "DEFAULT_MIN_DWELL_SECONDS",
    "DEFAULT_SOLAR_SMOOTH_ALPHA",
    "UPDATE_INTERVAL_SECONDS",
    "MODE_AUTO", "MODE_BOOST", "MODE_HC_ONLY", "MODE_OFF",
    "MODE_SOLAR_ONLY", "MODES", "MANUAL_MODES",
]
