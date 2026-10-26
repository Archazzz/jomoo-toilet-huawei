"""Original, restricted ZD6450-SA adapter; never defaults missing settings to zero."""

from dataclasses import dataclass

from .protocol import Frame, candidate_fields

# FA byte and mask; these settings share the parameter-update command (0x19).
PARAMETER_FLAGS = {"automatic_lid": (0, 0x08), "automatic_night_light": (2, 0x80)}


class UnsafeState(ValueError):
    """The observed state cannot support a safe, verifiable operation."""


def encode(command: int, payload: bytes) -> bytes:
    body = bytes([len(payload) + 4, 0x92, 0x21, command]) + payload
    return b"\xf3\xf4" + body + bytes([sum(body) & 255, 0xFC])


@dataclass(frozen=True)
class Snapshot:
    extended: bytes
    runtime: bytes
    mechanics: bytes

    @classmethod
    def from_frames(cls, frames: dict[int, Frame]) -> "Snapshot":
        if any(c not in frames or not frames[c].is_known_status for c in (0xFA, 0xE9, 0xEA)):
            raise UnsafeState("没有收齐本次连接的 FA/E9/EA 状态")
        return cls(*(frames[c].payload for c in (0xFA, 0xE9, 0xEA)))

    def fields(self) -> dict:
        result = {}
        for command, payload in (
            (0xFA, self.extended),
            (0xE9, self.runtime),
            (0xEA, self.mechanics),
        ):
            result.update(candidate_fields(Frame(0x21, 0, command, payload, b"")))
        result.update(
            water_temperature_level=(self.extended[0] >> 4) & 7,
            seat_temperature_level=self.extended[1] & 7,
            dryer_temperature_level=self.extended[2] & 7,
        )
        result.update(
            {
                name: bool(self.extended[index] & mask)
                for name, (index, mask) in PARAMETER_FLAGS.items()
            }
        )
        result["foam_running"] = bool(self.extended[6] & 0x04)
        # E9 01/02 are standby only when no automatic cycle or other activity is set.
        # Do not use the requested position or the passage of time as completion evidence.
        result["idle_confirmed"] = (
            self.runtime[3] in (1, 2)
            and not (self.runtime[1] or self.runtime[2])
            and not self.runtime[4] & 0x0C
            and not self.runtime[5] & 0x41
            and not self.extended[0] & 0x80
            and not any(
                result[key]
                for key in (
                    "lid_moving",
                    "ring_moving",
                    "small_flush_running",
                    "large_flush_running",
                    "foam_running",
                )
            )
        )
        return result

    def settings_signature(self) -> bytes:
        # Known settings plus reserved bits; FA p5 bit0 is tracked separately.
        # It changes on this model, is not decoded by the reference app, and is
        # never included in our outgoing payload. No meaning is assigned to it.
        masks = (0x7F, 0xFF, 0xFF, 0xFF, 0xF3, 0xFE, 0xBB, 0x3F)
        return bytes(value & mask for value, mask in zip(self.extended, masks, strict=True))

    def uninterpreted_changes(self, after: "Snapshot") -> list[str]:
        return ["FA.p5.bit0"] if (self.extended[5] ^ after.extended[5]) & 1 else []

    def idle(self) -> None:
        state = self.fields()
        if state["seated"] or self.runtime[3] not in (1, 2):
            raise UnsafeState("设备落座或正在工作")
        if state["lid_moving"] or state["ring_moving"]:
            raise UnsafeState("盖板或座圈正在移动")
        if self.runtime[1] or self.runtime[2] or self.extended[0] & 128:
            raise UnsafeState("设备报告故障或正在疏通")

    def target(self, action: str) -> dict:
        fields = self.fields()
        targets = {
            "foot_sensor_on": {"foot_sensor": True},
            "foot_sensor_off": {"foot_sensor": False},
            "lid_open": {"lid": "open", "ring": fields["ring"]},
            "lid_close": {"lid": "closed", "ring": "closed"},
            "ring_open": {"lid": "open", "ring": "open"},
            "ring_close": {"lid": "open", "ring": "closed"},
        }
        targets.update(
            {
                f"{name}_{value}": {
                    name: value == "on",
                    "lid": fields["lid"],
                    "ring": fields["ring"],
                }
                for name in PARAMETER_FLAGS
                for value in ("on", "off")
            }
        )
        if action not in targets:
            raise UnsafeState("不支持该操作")
        return targets[action]

    def packet(self, action: str) -> bytes:
        self.idle()
        target = self.target(action)
        if action.startswith("foot_sensor_"):
            return encode(0x1F, bytes([0x24, 1 if target["foot_sensor"] else 2]))
        p = self.extended
        # FA does not document gesture/night-light or extended nozzle bits here.
        # This model's observed profile has them clear; reject other profiles.
        if p[0] & 5 or p[1] & 8 or p[2] & 8 or not 1 <= (p[7] & 7) <= 5:
            raise UnsafeState("FA 包含尚未验证的设置位，禁止拼装参数指令")
        if self.fields()["lid"] not in ("open", "closed") or self.fields()["ring"] not in (
            "open",
            "closed",
        ):
            raise UnsafeState("盖板或座圈状态未知")
        payload = bytearray([p[0] & 127, p[1], p[2], (p[7] & 7) << 4, 0, p[4] & 0x70])
        feature = action.rsplit("_", 1)[0]
        if feature in PARAMETER_FLAGS:
            index, mask = PARAMETER_FLAGS[feature]
            payload[index] = (payload[index] & ~mask) | (mask if target[feature] else 0)
            operation = 0x19
        else:
            operation = (
                0x10 if target["lid"] == "closed" else (0x13 if target["ring"] == "open" else 0x11)
            )
        return encode(operation, bytes(payload) + bytes(5))

    def verify(self, after: "Snapshot", action: str) -> bool:
        expected = bytearray(self.settings_signature())
        observed = bytearray(after.settings_signature())
        if action.startswith("foot_sensor_"):
            expected[7] &= ~32
            observed[7] &= ~32
        feature = action.rsplit("_", 1)[0]
        if feature in PARAMETER_FLAGS:
            index, mask = PARAMETER_FLAGS[feature]
            expected[index] &= ~mask
            observed[index] &= ~mask
        if expected != observed:
            changed = [i for i, (a, b) in enumerate(zip(expected, observed, strict=True)) if a != b]
            raise UnsafeState(f"回读发现其他设置改变（FA 字节 {changed}）；未自动重试")
        state = after.fields()
        return all(state[k] == v for k, v in self.target(action).items()) and not (
            state["lid_moving"] or state["ring_moving"]
        )
