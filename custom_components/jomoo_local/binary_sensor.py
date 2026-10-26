"""Expose occupancy as read-only telemetry."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import JomooEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        Occupancy(entry.runtime_data, key, "seated", "落座") for key in entry.runtime_data.data
    )


class Occupancy(JomooEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self):
        return self.state_data.get("seated")
