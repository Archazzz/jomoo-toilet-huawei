"""Shared device identities and real-state availability."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN


class JomooEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, feature, name):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}_{feature}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{key}")},
            name=coordinator.data[key]["name"],
            manufacturer="JOMOO",
            model="ZD6450-SA",
        )

    @property
    def device_data(self):
        return (self.coordinator.data or {}).get(self.key, {})

    @property
    def available(self):
        return super().available and self.device_data.get("available", False)

    @property
    def state_data(self):
        return self.device_data.get("state", {})
