"""Original, restricted ZD6450-SA adapter; never defaults missing settings to zero."""

from dataclasses import dataclass

from .features249734 import (
    ACTIONS,
    FLAG_PACKETS,
    FLAGS,
    LEVELS,
    OCCUPIED_STARTS,
    SPECIAL_TARGETS,
    STARTS,
    TOGGLES,
    setting,
)
from .protocol import Frame, candidate_fields

# FA byte and mask; these settings share the parameter-update command (0x19).
PARAMETER_FLAGS = {
    name: FLAGS[name] for name in ("automatic_lid", "automatic_night_light", "automatic_flush")
}


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
    filter_data: bytes | None = None

    @classmethod
    def from_frames(cls, frames: dict[int, Frame]) -> "Snapshot":
        if any(c not in frames or not frames[c].is_known_status for c in (0xFA, 0xE9, 0xEA)):
            raise UnsafeState("没有收齐本次连接的 FA/E9/EA 状态")
        return cls(
            *(frames[c].payload for c in (0xFA, 0xE9, 0xEA)),
            filter_data=frames[0xE7].payload
            if 0xE7 in frames and frames[0xE7].is_known_status
            else None,
        )

    def fields(self) -> dict:
        result = {}
        for command, payload in (
            (0xFA, self.extended),
            (0xE9, self.runtime),
            (0xEA, self.mechanics),
        ):
            result.update(candidate_fields(Frame(0x21, 0, command, payload, b"")))
        for name, (index, mask, shift, _, _) in LEVELS.items():
            result[name + "_level"] = (self.extended[index] & mask) >> shift
        result.update(
            {name: bool(self.extended[index] & mask) for name, (index, mask) in FLAGS.items()}
        )
        p, r = self.extended, self.runtime
        result.update(
            bidet_pressure_level=p[3] >> 4,
            bidet_position_level=p[3] & 15,
            pre_wetting=bool(p[6] & 0x20),
            lid_sensitivity=(self.mechanics[2] >> 4) & 1,
            foam_running=bool(p[6] & 4),
            self_clean_running=bool(r[5] & 1),
            automatic_cycle=bool(r[4] & 8),
            wash_moving=bool(r[3] & 128),
            faults_present=bool(r[1] or r[2]),
        )
        raw = r[3]
        mode = {
            1: "idle",
            2: "idle",
            3: "nozzle_clean",
            131: "nozzle_replace",
            16: "dry",
            144: "dry",
        }.get(raw)
        if mode is None:
            if r[4] & 0x20:
                mode = "assist"
            elif raw & 0x60 in (0x20, 0x40):
                mode = "hip" if raw & 0x20 else "bidet"
        if r[4] & 4:  # Unsupported bath/mist mode, never guess a supported wash.
            mode = None
        result["wash_mode"] = mode or "unknown"
        result["work_idle"] = (
            mode == "idle"
            and not r[4] & 0x2C
            and not r[5] & 0x41
            and not p[0] & 128
            and not any(
                result[k] for k in ("small_flush_running", "large_flush_running", "foam_running")
            )
        )
        result["idle_confirmed"] = (
            result["work_idle"]
            and not result["faults_present"]
            and not result["lid_moving"]
            and not result["ring_moving"]
        )
        if self.filter_data is not None:
            minutes = 60 * (self.filter_data[2] + ((self.filter_data[3] & 0xC0) << 2)) + (
                self.filter_data[3] & 63
            )
            result["filter_remaining_percent"] = max(
                0, min(100, round(100 * (13980 - minutes) / 13980))
            )
        # Only translate independently identified fault bits; unknown bits stay abnormal.
        result["fault_codes"] = [
            code
            for index, mask, code in (
                (1, 1, 5),
                (1, 4, 6),
                (1, 0x48, 105),
                (1, 0x10, 103),
                (1, 0x20, 106),
                (2, 2, 1),
                (2, 0x20, 102),
                (2, 1, 4),
            )
            if r[index] & mask
        ]
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
        if self.fields()["seated"] or not self.fields()["idle_confirmed"]:
            raise UnsafeState("设备落座、工作中、机构运动或报告故障")

    def _setting(self, action: str):
        return setting(action, woman=self.fields()["wash_mode"] == "bidet")

    def target(self, action: str) -> dict:
        if action not in ACTIONS:
            raise UnsafeState("不支持该操作")
        fields = self.fields()
        change = self._setting(action)
        if change:
            field, _, _, _, value = change
            if field == "wash_pressure_level" and fields["wash_mode"] == "bidet":
                field = "bidet_pressure_level"
            if field == "nozzle_position_level" and fields["wash_mode"] == "bidet":
                field = "bidet_position_level"
            return {
                field: bool(value) if field in FLAGS else value,
                "lid": fields["lid"],
                "ring": fields["ring"],
            }
        targets = {
            "lid_open": {"lid": "open", "ring": fields["ring"]},
            "lid_close": {"lid": "closed", "ring": "closed"},
            "ring_open": {"lid": "open", "ring": "open"},
            "ring_close": {"lid": "open", "ring": "closed"},
            **{name: target for name, (_, target) in STARTS.items()},
            **SPECIAL_TARGETS,
        }
        return targets[action]

    def _guard(self, action: str) -> None:
        state = self.fields()
        if action == "stop":
            return  # Stop is permitted while occupied, working or reporting a fault.
        if state["faults_present"] or self.extended[0] & 128:
            raise UnsafeState("设备报告故障或正在疏通")
        if state["lid_moving"] or state["ring_moving"]:
            raise UnsafeState("盖板或座圈正在移动")
        if action in OCCUPIED_STARTS or action in {"move_on", "move_off"}:
            if not state["seated"] or state["lid"] != "open" or state["ring"] != "closed":
                raise UnsafeState("清洗和烘干要求真实落座、盖开圈关")
            if action in OCCUPIED_STARTS and not state["idle_confirmed"]:
                raise UnsafeState("已有工作流程，请先停止")
            if action.startswith("move_") and state["wash_mode"] not in {"hip", "bidet", "assist"}:
                raise UnsafeState("移动只在已确认的清洗模式中可用")
        elif self._setting(action):
            feature = action.rsplit("_", 1)[0]
            washing = state["wash_mode"] in {"hip", "bidet", "assist"}
            if feature in {"wash_pressure", "nozzle_position"}:
                if not state["seated"] or not washing:
                    raise UnsafeState("水压和喷嘴位置需在真实落座清洗时调整")
                if feature == "nozzle_position" and state["wash_moving"]:
                    raise UnsafeState("此型号移动清洗时不能调整喷嘴位置，请先关闭移动")
            elif feature in LEVELS and state["seated"]:
                if state["wash_mode"] not in {"idle", "hip", "bidet", "assist", "dry"}:
                    raise UnsafeState("当前工作模式不支持调整档位")
            else:
                self.idle()
        elif action in {"flush_large", "flush_small", "foam_start"}:
            # Occupancy alone must not block bowl flushing or foam. Active wash,
            # drying, maintenance and motion remain incompatible operations.
            if not state["idle_confirmed"]:
                raise UnsafeState("已有工作流程或机构运动，不能冲水或发泡")
        else:
            self.idle()
            if action == "self_clean" and state["lid"] != "closed":
                raise UnsafeState("此操作要求先合盖")
            if action in {"nozzle_clean", "nozzle_replace"} and (state["lid"], state["ring"]) != (
                "open",
                "closed",
            ):
                raise UnsafeState("喷嘴维护要求盖开圈关且无人落座")

    def _parameters(self) -> bytearray:
        p = self.extended
        if p[0] & 5 or p[1] & 8 or p[2] & 8 or not 1 <= (p[7] & 7) <= 5:
            raise UnsafeState("FA 包含尚未验证的设置位，禁止拼装参数指令")
        if self.fields()["lid"] not in ("open", "closed") or self.fields()["ring"] not in (
            "open",
            "closed",
        ):
            raise UnsafeState("盖板或座圈状态未知")
        return bytearray([p[0] & 127, p[1], p[2], (p[7] & 7) << 4, 0, p[4] & 0x70]) + bytes(5)

    def packet(self, action: str) -> bytes:
        target = self.target(action)
        self._guard(action)
        feature = action.rsplit("_", 1)[0]
        if feature in FLAG_PACKETS:
            sub, on, off = FLAG_PACKETS[feature]
            return encode(0x1F, bytes([sub, on if target[feature] else off]))
        if action == "foam_start":
            return encode(0x1F, bytes([0x20, 1]))
        if action == "self_clean":
            return encode(0x1F, bytes([0x28, 128]))
        payload = self._parameters()
        woman = action in {"wash_bidet", "wash_auto_bidet"} or (
            self.fields()["wash_mode"] == "bidet" and action != "stop"
        )
        if woman:
            p = self.extended
            if not 1 <= (p[3] & 15) <= 5 or not 1 <= p[3] >> 4 <= 5:
                raise UnsafeState("妇洗参数超出已知范围")
            payload[1] = (payload[1] & 0x8F) | ((p[3] & 15) << 4)
            payload[2] = (payload[2] & 0x8F) | (p[3] & 0xF0)
        change = self._setting(action)
        if change:
            field, index, mask, shift, value = change
            operation = TOGGLES.get(feature, 0x19)
            if feature in PARAMETER_FLAGS:
                payload[index] = (payload[index] & ~mask) | (mask if value else 0)
            elif feature in LEVELS:
                if feature == "wash_width":
                    payload[3] = value << 4
                else:
                    # FA stores female values in byte 3; the outgoing packet uses
                    # bytes 1/2 for the active wash context, just like male values.
                    out_index, out_mask, out_shift, _, _ = LEVELS[feature]
                    payload[out_index] = (payload[out_index] & ~out_mask) | (value << out_shift)
        elif action in STARTS:
            operation = STARTS[action][0]
        elif action == "stop":
            operation = 0
        elif action in {"move_on", "move_off"}:
            operation = 5  # Toggle; target is checked on fresh state before any write.
        else:
            operation = (
                0x10 if target["lid"] == "closed" else (0x13 if target["ring"] == "open" else 0x11)
            )
        return encode(operation, bytes(payload))

    def verify(self, after: "Snapshot", action: str) -> bool:
        expected = bytearray(self.settings_signature())
        observed = bytearray(after.settings_signature())
        change = self._setting(action)
        if change:
            _, index, mask, _, _ = change
            expected[index] &= ~mask
            observed[index] &= ~mask
        if expected != observed:
            changed = [i for i, (a, b) in enumerate(zip(expected, observed, strict=True)) if a != b]
            raise UnsafeState(f"回读发现其他设置改变（FA 字节 {changed}）；未自动重试")
        state = after.fields()
        return all(state[k] == v for k, v in self.target(action).items()) and not (
            state["lid_moving"] or state["ring_moving"]
        )
