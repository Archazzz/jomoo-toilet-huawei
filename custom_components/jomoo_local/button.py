"""Explicit refresh without any mechanical write."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import JomooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        Refresh(entry.runtime_data, key, "refresh", "刷新蓝牙状态")
        for key in entry.runtime_data.data
    )


class Refresh(JomooEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    @property
    def available(self):
        return self.coordinator.last_update_success

    async def async_press(self):
        await self.coordinator.request("POST", f"/v1/devices/{self.key}/refresh")
        await self.coordinator.async_request_refresh()
