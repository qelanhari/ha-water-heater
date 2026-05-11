"""Select platform: water heater operating mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODES
from .coordinator import SmartDhwpCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartDhwpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DhwpModeSelect(coordinator, entry)])


class DhwpModeSelect(CoordinatorEntity[SmartDhwpCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "mode"
    _attr_icon = "mdi:auto-mode"
    _attr_options = list(MODES)

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Atlantic (via Smart DHWP)",
            model="Heat-pump water heater",
        )

    @property
    def current_option(self) -> str:
        return self.coordinator.mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option)
        self.async_write_ha_state()
