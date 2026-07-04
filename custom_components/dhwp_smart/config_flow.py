"""Config flow for Smart DHWP."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_BOOST_EXTRA_SURPLUS_W,
    CONF_BOOST_TARGET_C,
    CONF_ECO_TARGET_C,
    CONF_FORECAST_TODAY_ENTITY,
    CONF_FORECAST_TOMORROW_ENTITY,
    CONF_GARAGE_TEMP_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HEATER_ENERGY_METER_ENTITY,
    CONF_HEATER_NOMINAL_W,
    CONF_HEATER_POWER_ENTITY,
    CONF_MIN_DWELL_SECONDS,
    CONF_MORNING_DEADLINE_HOUR,
    CONF_MORNING_DEADLINE_MINUTE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_ROUGE_HP_BLENDED_CAP,
    CONF_SIGNAL_MIN_HOLD_MINUTES,
    CONF_SIGNAL_SWITCH_ENTITY,
    CONF_SOLAR_SMOOTH_ALPHA,
    CONF_SURPLUS_SAFETY_MARGIN_W,
    CONF_TANK_CAPACITY_L,
    CONF_TANK_MIDDLE_TEMP_ENTITY,
    CONF_TANK_MORNING_FLOOR_C,
    CONF_TANK_TARGET_TOP_C,
    CONF_TANK_TOP_TEMP_ENTITY,
    CONF_TEMPO_COLOR_ENTITY,
    CONF_TEMPO_IS_HC_ENTITY,
    CONF_TEMPO_NEXT_COLOR_ENTITY,
    CONF_WATER_HEATER_ENTITY,
    DEFAULT_BOOST_EXTRA_SURPLUS_W,
    DEFAULT_BOOST_TARGET_C,
    DEFAULT_ECO_TARGET_C,
    DEFAULT_HEATER_NOMINAL_W,
    DEFAULT_MIN_DWELL_SECONDS,
    DEFAULT_MORNING_DEADLINE_HOUR,
    DEFAULT_MORNING_DEADLINE_MINUTE,
    DEFAULT_ROUGE_HP_BLENDED_CAP,
    DEFAULT_SIGNAL_MIN_HOLD_MINUTES,
    DEFAULT_SOLAR_SMOOTH_ALPHA,
    DEFAULT_SURPLUS_SAFETY_MARGIN_W,
    DEFAULT_TANK_CAPACITY_L,
    DEFAULT_TANK_MORNING_FLOOR_C,
    DEFAULT_TANK_TARGET_TOP_C,
    DOMAIN,
)


def _entities_schema(prev: dict[str, Any] | None = None) -> vol.Schema:
    p = prev or {}
    def opt(key: str, sel: Any, required: bool = True):
        if p.get(key):
            return (vol.Required(key, default=p[key]) if required else vol.Optional(key, default=p[key])), sel
        return (vol.Required(key) if required else vol.Optional(key)), sel
    pairs = [
        opt(CONF_SIGNAL_SWITCH_ENTITY, EntitySelector(EntitySelectorConfig(domain="switch"))),
        opt(CONF_WATER_HEATER_ENTITY, EntitySelector(EntitySelectorConfig(domain="water_heater"))),
        opt(CONF_TANK_TOP_TEMP_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))),
        opt(CONF_TANK_MIDDLE_TEMP_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))),
        opt(CONF_HEATER_POWER_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="power"))),
        opt(CONF_HEATER_ENERGY_METER_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="energy"))),
        opt(CONF_GRID_POWER_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="power"))),
        opt(CONF_PV_POWER_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="power")), required=False),
        opt(CONF_GARAGE_TEMP_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))),
        opt(CONF_OUTDOOR_TEMP_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature")), required=False),
        opt(CONF_TEMPO_COLOR_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor"))),
        opt(CONF_TEMPO_NEXT_COLOR_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor")), required=False),
        opt(CONF_TEMPO_IS_HC_ENTITY, EntitySelector(EntitySelectorConfig(domain="binary_sensor"))),
        opt(CONF_FORECAST_TODAY_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor")), required=False),
        opt(CONF_FORECAST_TOMORROW_ENTITY, EntitySelector(EntitySelectorConfig(domain="sensor")), required=False),
    ]
    return vol.Schema(dict(pairs))


def _tank_schema(prev: dict[str, Any] | None = None) -> vol.Schema:
    p = prev or {}
    return vol.Schema({
        vol.Required(CONF_TANK_CAPACITY_L, default=p.get(CONF_TANK_CAPACITY_L, DEFAULT_TANK_CAPACITY_L)):
            NumberSelector(NumberSelectorConfig(min=50, max=500, step=10, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_TANK_TARGET_TOP_C, default=p.get(CONF_TANK_TARGET_TOP_C, DEFAULT_TANK_TARGET_TOP_C)):
            NumberSelector(NumberSelectorConfig(min=40, max=65, step=0.5, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_TANK_MORNING_FLOOR_C, default=p.get(CONF_TANK_MORNING_FLOOR_C, DEFAULT_TANK_MORNING_FLOOR_C)):
            NumberSelector(NumberSelectorConfig(min=35, max=55, step=0.5, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_ECO_TARGET_C, default=p.get(CONF_ECO_TARGET_C, DEFAULT_ECO_TARGET_C)):
            NumberSelector(NumberSelectorConfig(min=40, max=65, step=0.5, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_BOOST_TARGET_C, default=p.get(CONF_BOOST_TARGET_C, DEFAULT_BOOST_TARGET_C)):
            NumberSelector(NumberSelectorConfig(min=40, max=70, step=0.5, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_MORNING_DEADLINE_HOUR, default=p.get(CONF_MORNING_DEADLINE_HOUR, DEFAULT_MORNING_DEADLINE_HOUR)):
            NumberSelector(NumberSelectorConfig(min=0, max=23, step=1, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_MORNING_DEADLINE_MINUTE, default=p.get(CONF_MORNING_DEADLINE_MINUTE, DEFAULT_MORNING_DEADLINE_MINUTE)):
            NumberSelector(NumberSelectorConfig(min=0, max=59, step=5, mode=NumberSelectorMode.BOX)),
    })


def _guardrails_schema(prev: dict[str, Any] | None = None) -> vol.Schema:
    p = prev or {}
    return vol.Schema({
        vol.Required(CONF_ROUGE_HP_BLENDED_CAP, default=p.get(CONF_ROUGE_HP_BLENDED_CAP, DEFAULT_ROUGE_HP_BLENDED_CAP)):
            NumberSelector(NumberSelectorConfig(min=0.05, max=0.3, step=0.001, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_HEATER_NOMINAL_W, default=p.get(CONF_HEATER_NOMINAL_W, DEFAULT_HEATER_NOMINAL_W)):
            NumberSelector(NumberSelectorConfig(min=100, max=3000, step=10, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_SURPLUS_SAFETY_MARGIN_W, default=p.get(CONF_SURPLUS_SAFETY_MARGIN_W, DEFAULT_SURPLUS_SAFETY_MARGIN_W)):
            NumberSelector(NumberSelectorConfig(min=0, max=2000, step=10, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_BOOST_EXTRA_SURPLUS_W, default=p.get(CONF_BOOST_EXTRA_SURPLUS_W, DEFAULT_BOOST_EXTRA_SURPLUS_W)):
            NumberSelector(NumberSelectorConfig(min=0, max=3000, step=100, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_SIGNAL_MIN_HOLD_MINUTES, default=p.get(CONF_SIGNAL_MIN_HOLD_MINUTES, DEFAULT_SIGNAL_MIN_HOLD_MINUTES)):
            NumberSelector(NumberSelectorConfig(min=0, max=240, step=5, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_MIN_DWELL_SECONDS, default=p.get(CONF_MIN_DWELL_SECONDS, DEFAULT_MIN_DWELL_SECONDS)):
            NumberSelector(NumberSelectorConfig(min=0, max=600, step=10, mode=NumberSelectorMode.BOX)),
        vol.Required(CONF_SOLAR_SMOOTH_ALPHA, default=p.get(CONF_SOLAR_SMOOTH_ALPHA, DEFAULT_SOLAR_SMOOTH_ALPHA)):
            NumberSelector(NumberSelectorConfig(min=0.05, max=1.0, step=0.05, mode=NumberSelectorMode.SLIDER)),
    })


class DhwpSmartConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_tank()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default="Smart DHWP"): str}).extend(
                _entities_schema().schema
            ),
        )

    async def async_step_tank(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_guardrails()
        return self.async_show_form(step_id="tank", data_schema=_tank_schema())

    async def async_step_guardrails(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title=self._data.get(CONF_NAME, "Smart DHWP"),
                data={k: v for k, v in self._data.items() if k != CONF_NAME},
            )
        return self.async_show_form(step_id="guardrails", data_schema=_guardrails_schema())

    @staticmethod
    def async_get_options_flow(config_entry):
        return DhwpSmartOptionsFlow(config_entry)


class DhwpSmartOptionsFlow(OptionsFlowWithConfigEntry):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return await self.async_step_tank()

    async def async_step_tank(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._stash = dict(user_input)
            return await self.async_step_guardrails()
        prev = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="tank", data_schema=_tank_schema(prev))

    async def async_step_guardrails(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._stash = {**getattr(self, "_stash", {}), **user_input}
            return await self.async_step_entities()
        prev = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="guardrails", data_schema=_guardrails_schema(prev))

    async def async_step_entities(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            options = {**getattr(self, "_stash", {}), **user_input}
            return self.async_create_entry(title="", data=options)
        prev = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="entities", data_schema=_entities_schema(prev))
