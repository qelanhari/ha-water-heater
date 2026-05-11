"""Binary sensor platform for Smart DHWP."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
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
        DhwpHeatingNow(coordinator, entry),
        DhwpHardFloorBreach(coordinator, entry),
        DhwpBoostMode(coordinator, entry),
    ])


class _Base(CoordinatorEntity[SmartDhwpCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Atlantic (via Smart DHWP)",
            model="Heat-pump water heater",
        )


class DhwpHeatingNow(_Base):
    _attr_translation_key = "heating_now"
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_heating_now"

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("heater_on"))


class DhwpBoostMode(_Base):
    """On when the integration has set the water heater target to the boost temp (55°C)."""

    _attr_translation_key = "boost_mode"
    _attr_icon = "mdi:fire-circle"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_boost_mode"

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("boost_mode_on"))


class DhwpHardFloorBreach(_Base):
    _attr_translation_key = "hard_floor_breach"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_hard_floor_breach"

    @property
    def is_on(self) -> bool:
        d = self.coordinator.data
        return bool(d and d.get("decision_action") == "hard_floor")
