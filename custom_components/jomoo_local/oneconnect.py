"""Original 2QBR service adapters gated by each device's explicit action allowlist.

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
    "onSeatAction": "action",
    "stop": "action",
    "foamShield": "toiletSeat",
    "faultDetection": "fault",
    "filterElement": "filterElement",
}
LID_COMMANDS = {3: ("lid_open", "turn_on"), 4: ("lid_close", "turn_off")}
OFF_SEAT_COMMANDS = {
    1: "flush_large",
    2: "flush_small",
    3: "lid_open",
    4: "lid_close",
    5: "ring_open",
    6: "ring_close",
    7: "self_clean",
}
ON_SEAT_COMMANDS = {
    0: "wash_auto_hip",
    1: "wash_hip",
    2: "wash_bidet",
    3: "dry_start",
    5: "wash_assist",
    101: "wash_auto_bidet",
}
WASH_STATUS = {"nozzle_clean": 6, "dry": 7, "hip": 8, "bidet": 9, "assist": 10}


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
                    ("self_clean_running", 202),
                )
                if state.get(field) is True
            ]
            mode = state.get("wash_mode")
            if mode in WASH_STATUS:
                code = WASH_STATUS[mode]
                if state.get("automatic_cycle") is True and mode in {"hip", "bidet"}:
                    code = 200 if mode == "hip" else 201
                elif state.get("wash_moving") is True and mode in {"hip", "bidet", "assist"}:
                    code = {"hip": 14, "bidet": 15, "assist": 16}[mode]
                active.append(code)
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
                "preWet": "pre_wetting",
                "autoDeo": "automatic_deodorization",
            }
            return {
                **{
                    wire: state[key] for wire, key in fields.items() if type(state.get(key)) is bool
                },
                **_level(state, "lid_sensitivity", "sensitivity", 1),
            }
        if self.service_id == "drySetting":
            return _level(state, "dryer_temperature_level", "gear", 3)
        if self.service_id == "washSetting":
            result = _level(state, "water_temperature_level", "tempGear", 5)
            woman = state.get("wash_mode") == "bidet"
            for key, wire in (
                ("bidet_pressure_level" if woman else "wash_pressure_level", "waterGear"),
                ("bidet_position_level" if woman else "nozzle_position_level", "amplitude"),
                ("wash_width_level", "anterior"),
            ):
                value = state.get(key)
                if type(value) is int and 1 <= value <= 5:
                    result[wire] = value - 1
            if state.get("wash_mode") in {"hip", "bidet", "assist"}:
                if type(state.get("wash_moving")) is bool:
                    result["massage"] = int(state["wash_moving"])
            elif state.get("idle_confirmed") is True:
                result["massage"] = 0
            if state.get("wash_mode") == "nozzle_clean":
                result["selfClean"] = 1
            elif state.get("idle_confirmed") is True:
                result["selfClean"] = 0
            return result
        if self.service_id == "seatHeating":
            result = _level(state, "seat_temperature_level", "gear", 4)
            if (
                type(state.get("seat_temperature_level")) is int
                and state["seat_temperature_level"] == 5
            ):
                result["gear"] = 100
            if type(state.get("season_temperature")) is bool:
                result["mode"] = int(state["season_temperature"])
            return result
        if self.service_id == "onSeatAction":
            mode = state.get("wash_mode")
            if mode in {"hip", "bidet", "assist", "dry"}:
                value = {"hip": 1, "bidet": 2, "assist": 5, "dry": 3}[mode]
                if state.get("automatic_cycle") is True and mode in {"hip", "bidet"}:
                    value = 0 if mode == "hip" else 101
                return {"action": value}
            return {"action": 200} if state.get("idle_confirmed") is True else {}
        if self.service_id == "foamShield":
            return {
                wire: int(state[key])
                for wire, key in (("action", "foam_running"), ("on", "automatic_foam"))
                if type(state.get(key)) is bool
            }
        if self.service_id == "faultDetection":
            if state.get("faults_present") is False:
                return {"status": 0, "code": 0}
            if state.get("faults_present") is True:
                codes = state.get("fault_codes", [])
                return {"status": 1, **({"code": codes[0]} if codes else {})}
            return {}
        if self.service_id == "filterElement":
            # No guessed filter type or reset action. Percentage requires a fresh E7.
            return _level(state, "filter_remaining_percent", "leftPercentage", 100)
        return {}  # offSeatAction is write-only; never invent an action state.

    def _command(self, state: Mapping, payload: dict) -> str:
        if not isinstance(payload, dict) or len(payload) != 1:
            raise ValueError("expected exactly one supported field; batch writes are not atomic")
        field, value = next(iter(payload.items()))
        sid = self.service_id
        if sid in {"offSeatAction", "onSeatAction"} and field == "action" and type(value) is int:
            commands = OFF_SEAT_COMMANDS if sid == "offSeatAction" else ON_SEAT_COMMANDS
            if value in commands:
                return commands[value]
        if sid == "stop" and field == "action" and type(value) is int and value == 0:
            return "stop"
        levels = {
            ("drySetting", "gear"): ("dryer_temperature", 3, 0),
            ("seatHeating", "gear"): ("seat_temperature", 4, 0),
            ("washSetting", "tempGear"): ("water_temperature", 5, 0),
            ("washSetting", "waterGear"): ("wash_pressure", 4, 1),
            ("washSetting", "amplitude"): ("nozzle_position", 4, 1),
            ("washSetting", "anterior"): ("wash_width", 4, 1),
        }
        if (sid, field) in levels and type(value) is int:
            name, maximum, offset = levels[sid, field]
            if sid == "seatHeating" and value == 100:
                return "seat_temperature_5"
            if 0 <= value <= maximum:
                return f"{name}_{value + offset}"
        if sid == "foamShield" and field == "action" and type(value) is int and value == 1:
            return "foam_start"
        if type(value) in (int, bool) and value in (0, 1):
            flags = {
                ("foamShield", "on"): "automatic_foam",
                ("toiletSetting", "autoDeo"): "automatic_deodorization",
                ("washSetting", "massage"): "move",
            }
            if (sid, field) in flags:
                return f"{flags[sid, field]}_{'on' if value else 'off'}"
            if sid == "washSetting" and field == "selfClean":
                if value:
                    return "nozzle_clean"
                if state.get("wash_mode") == "nozzle_clean":
                    return "stop"
        raise ValueError("unsupported field/value; no command dispatched")

    async def validate_command(self, hass, service, entity_ids, payload: dict) -> None:
        snapshot = self._state(hass, service, entity_ids)
        if snapshot is None:
            raise ValueError("toilet state unavailable or expired; no command dispatched")
        action = self._command(snapshot[0], payload)
        if action not in snapshot[1]:
            raise ValueError("command is disabled by the local gateway")

    async def apply_command(self, hass, service, entity_ids, payload: dict) -> None:
        await self.validate_command(hass, service, entity_ids, payload)
        snapshot = self._state(hass, service, entity_ids)
        action = self._command(snapshot[0], payload)
        if action in {"lid_open", "lid_close"}:
            domain, method, data = (
                "switch",
                LID_COMMANDS[payload["action"]][1],
                {"entity_id": entity_ids[0]},
            )
        else:
            domain, method = "jomoo_local", "execute"
            data = {"entity_id": entity_ids[0], "action": action}
        await hass.services.async_call(domain, method, data, blocking=True)


ADAPTERS = tuple(ToiletServiceAdapter(sid) for sid in SERVICE_TYPES)
