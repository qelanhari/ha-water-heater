"""Button platform: Force heat now / Reset patterns."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartDhwpCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartDhwpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        DhwpForceHeat(coordinator, entry),
        DhwpResetPatterns(coordinator, entry),
    ])


class _Base(CoordinatorEntity[SmartDhwpCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Atlantic (via Smart DHWP)",
            model="Heat-pump water heater",
        )


class DhwpForceHeat(_Base):
    _attr_translation_key = "force_heat"
    _attr_icon = "mdi:fire-alert"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_force_heat"

    async def async_press(self) -> None:
        await self.coordinator.async_force_heat()


class DhwpResetPatterns(_Base):
    _attr_translation_key = "reset_patterns"
    _attr_icon = "mdi:database-refresh"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reset_patterns"

    async def async_press(self) -> None:
        await self.coordinator.async_reset_patterns()
