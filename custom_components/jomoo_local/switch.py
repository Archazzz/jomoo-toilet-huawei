"""Only explicitly enabled actions become switches under the same HA device."""

from homeassistant.components.switch import SwitchEntity

from .entity import JomooEntity

FEATURES = {
    "lid": ("翻盖", "lid_open", "lid_close"),
    "ring": ("座圈", "ring_open", "ring_close"),
    "foot_sensor": ("脚感", "foot_sensor_on", "foot_sensor_off"),
    "automatic_lid": ("自动翻盖", "automatic_lid_on", "automatic_lid_off"),
    "automatic_night_light": ("光感夜灯", "automatic_night_light_on", "automatic_night_light_off"),
}
BOOLEAN_FEATURES = {"foot_sensor", "automatic_lid", "automatic_night_light"}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        JomooSwitch(coordinator, key, feature)
        for key, device in coordinator.data.items()
        for feature, (_, on, off) in FEATURES.items()
        if {on, off} <= set(device["actions"])
    )


class JomooSwitch(JomooEntity, SwitchEntity):
    _attr_icon = "mdi:toilet"

    def __init__(self, coordinator, key, feature):
        self.feature = feature
        name, self.on_action, self.off_action = FEATURES[feature]
        super().__init__(coordinator, key, feature, name)

    @property
    def available(self):
        value = self.state_data.get(self.feature)
        return super().available and (
            isinstance(value, bool)
            if self.feature in BOOLEAN_FEATURES
            else value in ("open", "closed")
        )

    @property
    def is_on(self):
        value = self.state_data.get(self.feature)
        return value is True if self.feature in BOOLEAN_FEATURES else value == "open"

    @property
    def extra_state_attributes(self):
        if self.feature != "lid":
            return None
        # One bound entity carries the same toilet's telemetry for multi-service cards.
        # Never expose its BLE identity, gateway token, or command journal here.
        return {
            "jomoo_model": "249734",
            "jomoo_state": self.state_data,
            "jomoo_actions": self.device_data.get("actions", []),
            "jomoo_expires_at": self.device_data.get("expires_at", 0),
        }

    async def async_turn_on(self, **kwargs):
        await self.coordinator.command(self.key, self.on_action)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.command(self.key, self.off_action)
