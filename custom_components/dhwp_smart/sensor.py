"""Sensor platform for Smart DHWP."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartDhwpCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartDhwpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        DhwpAction(coordinator, entry),
        DhwpReason(coordinator, entry),
        DhwpEnergyNeeded(coordinator, entry),
        DhwpCycleKwhToday(coordinator, entry),
        DhwpBlendedCost(coordinator, entry),
        DhwpGridSmooth(coordinator, entry),
        DhwpExpectedUsage(coordinator, entry),
        DhwpForecastToday(coordinator, entry),
        DhwpPatternSamples(coordinator, entry),
        DhwpSignalHoldRemaining(coordinator, entry),
    ])


class _Base(CoordinatorEntity[SmartDhwpCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Atlantic (via Smart DHWP)",
            model="Heat-pump water heater",
        )


class DhwpAction(_Base):
    _attr_translation_key = "action"
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_action"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("decision_action") if d else None


class DhwpReason(_Base):
    _attr_translation_key = "reason"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reason"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("decision_reason") if d else None


class DhwpEnergyNeeded(_Base):
    _attr_translation_key = "energy_needed"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_energy_needed"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("energy_needed_kwh") if d else None


class DhwpCycleKwhToday(_Base):
    _attr_translation_key = "cycle_kwh_today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cycle_kwh_today"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("cycle_kwh_today") if d else None


class DhwpBlendedCost(_Base):
    _attr_translation_key = "blended_cost"
    _attr_native_unit_of_measurement = "€/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_blended_cost"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("cycle_blended_cost_eur_per_kwh") if d else None


class DhwpGridSmooth(_Base):
    _attr_translation_key = "grid_smooth"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_grid_smooth"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("grid_smooth_w") if d else None


class DhwpExpectedUsage(_Base):
    _attr_translation_key = "expected_usage"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_expected_usage"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("expected_usage_today_kwh") if d else None


class DhwpForecastToday(_Base):
    _attr_translation_key = "forecast_today"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_forecast_today"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("forecast_today_kwh") if d else None


class DhwpSignalHoldRemaining(_Base):
    """Minutes left on the 2h commitment after the signal switch was turned on."""

    _attr_translation_key = "signal_hold_remaining"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_signal_hold_remaining"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("signal_hold_remaining_min") if d else None


class DhwpPatternSamples(_Base):
    _attr_translation_key = "pattern_samples"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_pattern_samples"

    @property
    def native_value(self):
        d = self.coordinator.data
        return d.get("pattern_samples") if d else None

    @property
    def extra_state_attributes(self):
        return {
            "outdoor_coef": (self.coordinator.data or {}).get("outdoor_coef"),
            "daily_kwh_ema": list(self.coordinator.pattern.daily_kwh_ema),
        }
