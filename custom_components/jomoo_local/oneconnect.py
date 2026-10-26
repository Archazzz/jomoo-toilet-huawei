"""Original, restricted 2QBR service adapters; cloud/UI projection remains unverified.

Contract source: https://smarthome-drcn.dbankcdn.com/device/guide/2QBR/2QBR.json
One physical toilet binds every service to its single jomoo_local lid entity.
"""

import math
import time
from collections.abc import Mapping

SERVICE_TYPES = {
    "offSeatAction": "action",
    "toiletLidStatus": "status",
    "toiletStatus": "status",
    "seatStatus": "status",
    "toiletSetting": "toiletSeat",
    "drySetting": "toiletSeat",
    "washSetting": "toiletSeat",
    "seatHeating": "toiletSeat",
}
LID_COMMANDS = {3: ("lid_open", "turn_on"), 4: ("lid_close", "turn_off")}


def _snapshot(hass, entity_id: str) -> tuple[Mapping, tuple] | None:
    state = hass.states.get(entity_id)
    if state is None or state.state not in {"on", "off"}:
        return None
    attrs = state.attributes
    if attrs.get("jomoo_model") != "249734":
        return None
    expires = attrs.get("jomoo_expires_at")
    if type(expires) not in (int, float) or not math.isfinite(expires) or time.time() >= expires:
        return None
    values, actions = attrs.get("jomoo_state"), attrs.get("jomoo_actions")
    if not isinstance(values, Mapping) or not isinstance(actions, (tuple, list)):
        return None
    return values, tuple(actions)


def _level(state: Mapping, key: str, field: str, maximum: int) -> dict:
    value = state.get(key)
    return {field: value} if type(value) is int and 0 <= value <= maximum else {}


class ToiletServiceAdapter:
    def __init__(self, service_id: str) -> None:
        self.service_id = service_id

    def validate_service(self, service) -> None:
        if (
            service.service_id != self.service_id
            or service.service_type != SERVICE_TYPES[self.service_id]
            or service.contract
        ):
            raise ValueError("2QBR service requires its exact type, ID and unmodified contract")

    def validate_entities(self, entity_ids: tuple[str, ...]) -> None:
        if len(entity_ids) != 1 or not entity_ids[0].startswith("switch."):
            raise ValueError("bind one jomoo_local lid switch per physical toilet")

    def _state(self, hass, service, entity_ids):
        self.validate_service(service)
        self.validate_entities(entity_ids)
        return _snapshot(hass, entity_ids[0])

    async def read_state(self, hass, service, entity_ids) -> dict[str, object]:
        snapshot = self._state(hass, service, entity_ids)
        if snapshot is None:
            return {}
        state, _actions = snapshot
        if self.service_id == "toiletStatus":
            active = [
                code
                for field, code in (
                    ("large_flush_running", 107),
                    ("small_flush_running", 108),
                    ("foam_running", 106),
                )
                if state.get(field) is True
            ]
            if len(active) == 1:
                return {"status": active[0]}
            if not active and state.get("idle_confirmed") is True:
                return {"status": 4}
            return {}
        if self.service_id == "toiletLidStatus":
            if state.get("lid_moving") is not False or state.get("ring_moving") is not False:
                return {}
            value = {("open", "closed"): 4, ("open", "open"): 5, ("closed", "closed"): 6}.get(
                (state.get("lid"), state.get("ring"))
            )
            return {"status": value} if value is not None else {}
        if self.service_id == "seatStatus":
            value = state.get("seated")
            return {"status": int(value)} if type(value) is bool else {}
        if self.service_id == "toiletSetting":
            fields = {
                "autoFoot": "foot_sensor",
                "autoLight": "automatic_night_light",
                "autoOpenCover": "automatic_lid",
                "autoFlush": "automatic_flush",
                "peeDetect": "automatic_small_flush",
            }
            return {
                wire: state[key] for wire, key in fields.items() if type(state.get(key)) is bool
            }
        if self.service_id == "drySetting":
            return _level(state, "dryer_temperature_level", "gear", 3)
        if self.service_id == "washSetting":
            return _level(state, "water_temperature_level", "tempGear", 5)
        if self.service_id == "seatHeating":
            # Profile's highest setting is 100; its BLE translation is not verified.
            return _level(state, "seat_temperature_level", "gear", 4)
        return {}  # offSeatAction is write-only; never invent an action state.

    async def validate_command(self, hass, service, entity_ids, payload: dict) -> None:
        snapshot = self._state(hass, service, entity_ids)
        if snapshot is None:
            raise ValueError("toilet state unavailable or expired; no command dispatched")
        if self.service_id != "offSeatAction":
            raise ValueError("this service is currently read-only in the bridge")
        if not isinstance(payload, dict) or set(payload) != {"action"}:
            raise ValueError("expected exactly one action field")
        value = payload["action"]
        if type(value) is not int or value not in LID_COMMANDS:
            raise ValueError("only hardware-validated lid open/close commands are enabled")
        if LID_COMMANDS[value][0] not in snapshot[1]:
            raise ValueError("command is disabled by the local gateway")

    async def apply_command(self, hass, service, entity_ids, payload: dict) -> None:
        await self.validate_command(hass, service, entity_ids, payload)
        await hass.services.async_call(
            "switch",
            LID_COMMANDS[payload["action"]][1],
            {"entity_id": entity_ids[0]},
            blocking=True,
        )


ADAPTERS = tuple(ToiletServiceAdapter(sid) for sid in SERVICE_TYPES)
