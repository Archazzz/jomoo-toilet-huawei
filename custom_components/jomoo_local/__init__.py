"""HA adapter for the Mac gateway; BLE identities and writes stay on the Mac."""

import time
from datetime import timedelta
from uuid import uuid4

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

DOMAIN = "jomoo_local"
PLATFORMS = [Platform.SWITCH, Platform.BINARY_SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required("url"): cv.url, vol.Required("token"): cv.string})},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "import"}, data=dict(config[DOMAIN])
            )
        )
    return True


class Coordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        import logging

        super().__init__(
            hass,
            logging.getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=5),
            config_entry=entry,
        )
        self.url = entry.data["url"].rstrip("/")
        self.headers = {"Authorization": f"Bearer {entry.data['token']}"}
        self.session = async_get_clientsession(hass)
        self.pending_commands: dict[str, int] = {}

    async def request(self, method, path, data=None):
        try:
            async with self.session.request(
                method,
                self.url + path,
                json=data,
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=95),
            ) as response:
                if response.status != 200:
                    raise HomeAssistantError(
                        f"网关拒绝或未确认请求（HTTP {response.status}），未重试"
                    )
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise HomeAssistantError("网关连接失败；动作结果可能未知，未重试") from exc

    async def _async_update_data(self):
        try:
            return (await self.request("GET", "/v1/states"))["devices"]
        except HomeAssistantError as exc:
            raise UpdateFailed(str(exc)) from exc

    async def command(self, device, action):
        self.pending_commands[device] = self.pending_commands.get(device, 0) + 1
        self.async_update_listeners()
        try:
            await self.request(
                "POST",
                "/v1/commands",
                {"id": str(uuid4()), "issued_at": time.time(), "device": device, "action": action},
            )
        finally:
            try:
                # Keep old cached standby out of cloud reports until fresh state
                # arrives, including while the HTTP request is still connecting.
                # Fetch the post-action state synchronously before the bridge
                # publishes a cloud snapshot; async_request_refresh only queues
                # a refresh and can race the OneConnect post-ACK callback.
                try:
                    fresh = await self._async_update_data()
                except UpdateFailed:
                    await self.async_request_refresh()
                else:
                    self.async_set_updated_data(fresh)
            finally:
                self.pending_commands[device] -= 1
                if not self.pending_commands[device]:
                    del self.pending_commands[device]
                self.async_update_listeners()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
