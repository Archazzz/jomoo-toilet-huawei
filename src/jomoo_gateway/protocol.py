"""Independent F3F4 decoder based on publicly documented wire observations.

The field layout is a candidate for model 249734, not a claim of hardware acceptance.
Only status queries are encoded here; there is no arbitrary-write or action API.
"""

from dataclasses import dataclass

HEADER = b"\xf3\xf4"
TOILET_ADDRESS = 0x21
STATUS_QUERY = bytes.fromhex("f3f4089200e2000000007cfc")
EXPECTED_PAYLOAD_LENGTHS = {0xE2: 7, 0xE7: 4, 0xE9: 6, 0xEA: 13, 0xFA: 8}


class InvalidFrame(ValueError):
    """A frame is incomplete, corrupt, or structurally invalid."""


@dataclass(frozen=True, slots=True)
class Frame:
    source: int
    destination: int
    command: int
    payload: bytes
    raw: bytes

    @classmethod
    def decode(cls, raw: bytes) -> "Frame":
        if len(raw) < 8 or raw[:2] != HEADER or raw[-1] != 0xFC:
            raise InvalidFrame("Invalid header, trailer, or minimum length")
        if raw[2] + 4 != len(raw):
            raise InvalidFrame("Declared frame length does not match")
        if sum(raw[2:-2]) & 0xFF != raw[-2]:
            raise InvalidFrame("Checksum mismatch")
        return cls(raw[3], raw[4], raw[5], raw[6:-2], bytes(raw))

    @property
    def is_known_status(self) -> bool:
        return self.source == TOILET_ADDRESS and len(self.payload) == EXPECTED_PAYLOAD_LENGTHS.get(
            self.command
        )


class FrameStream:
    """Decode split or concatenated notifications, discarding malformed packets."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.invalid_frames = 0

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer.extend(chunk)
        frames: list[Frame] = []
        while self._buffer:
            start = self._buffer.find(HEADER)
            if start < 0:
                # Keep a possible split header, without retaining arbitrary noise.
                self._buffer[:] = b"\xf3" if self._buffer[-1] == 0xF3 else b""
                break
            del self._buffer[:start]
            if len(self._buffer) < 3:
                break
            length = self._buffer[2] + 4
            if length < 8:
                self.invalid_frames += 1
                del self._buffer[0]
                continue
            if len(self._buffer) < length:
                break
            try:
                frame = Frame.decode(bytes(self._buffer[:length]))
            except InvalidFrame:
                self.invalid_frames += 1
                del self._buffer[0]
            else:
                frames.append(frame)
                del self._buffer[:length]
        return frames


def candidate_fields(frame: Frame) -> dict[str, bool | int | str]:
    """Decode only documented lengths; preserve unknown frames as raw diagnostics."""
    if not frame.is_known_status:
        return {}
    p = frame.payload
    if frame.command == 0xE2:
        return {
            "automatic_lid": bool(p[0] & 8),
            "automatic_flush": bool(p[0] & 2),
            "water_temperature_level": (p[0] >> 4) & 7,
            "seat_temperature_level": p[1] & 15,
            "dryer_temperature_level": p[2] & 7,
            "automatic_night_light": bool(p[2] & 128),
        }
    if frame.command == 0xEA:
        states = {1: "closed", 2: "open"}
        return {
            "lid": states.get((p[0] >> 2) & 3, "unknown_or_moving"),
            "ring": states.get(p[0] & 3, "unknown_or_moving"),
            "lid_moving": bool(p[0] & 64),
            "ring_moving": bool(p[0] & 32),
        }
    if frame.command == 0xE9:
        return {"seated": bool(p[0] & 128), "work_status_raw": p[3]}
    if frame.command == 0xFA:
        return {
            "foot_sensor": bool(p[7] & 32),
            "automatic_small_flush": bool(p[7] & 16),
            "small_flush_running": bool(p[4] & 4),
            "large_flush_running": bool(p[4] & 8),
        }
    return {}
