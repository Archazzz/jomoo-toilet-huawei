"""Fresh-state sessions with one actuator write, read-back, and no command replay."""

import asyncio
import logging
import time

from bleak import BleakClient

from .diagnostics import DiagnosticError, verified_target
from .model249734 import Snapshot, UnsafeState
from .protocol import STATUS_QUERY, FrameStream

CHARACTERISTIC = "0000ffe1-0000-1000-8000-00805f9b34fb"
LOGGER = logging.getLogger(__name__)


class UnconfirmedAction(DiagnosticError):
    """A write may have reached hardware; never retry it automatically."""

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


async def operate(identifier: str, action: str | None = None, *, target_resolver=None) -> dict:
    started = time.monotonic()
    device = await (target_resolver or verified_target)(identifier)
    resolved_at = time.monotonic()
    timings = {"discovery": round((resolved_at - started) * 1000)}

    def result(**data):
        timings["session"] = round((time.monotonic() - started) * 1000)
        LOGGER.info("蓝牙会话 %s 阶段耗时 ms：%s", action or "read", timings)
        return {**data, "timing_ms": dict(timings)}

    stream = FrameStream()
    frames = {}
    received = {}

    def notify(_sender, data):
        for frame in stream.feed(bytes(data)):
            if frame.is_known_status:
                frames[frame.command] = frame
                received[frame.command] = time.monotonic()

    async with asyncio.timeout(65):
        async with BleakClient(device, timeout=20) as client:
            connected_at = time.monotonic()
            timings["connection"] = round((connected_at - resolved_at) * 1000)
            char = client.services.get_characteristic(CHARACTERISTIC)
            if char is None or not {"notify", "write-without-response"} <= set(char.properties):
                raise DiagnosticError("GATT 能力不符合 249734 配置")
            await client.start_notify(char, notify)

            async def read() -> Snapshot:
                frames.clear()
                received.clear()
                await client.write_gatt_char(char, STATUS_QUERY, response=False)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if not client.is_connected:
                        raise DiagnosticError("读取状态时蓝牙断开")
                    if all(command in frames for command in (0xFA, 0xE9, 0xEA)):
                        return Snapshot.from_frames(frames)
                    await asyncio.sleep(0.1)
                raise DiagnosticError("状态查询超时；未使用缓存补齐")

            before = await read()
            timings["initial_read"] = round((time.monotonic() - connected_at) * 1000)
            if action is None:
                return result(state=before.fields(), outcome="read")
            packet = before.packet(action)
            if all(before.fields()[k] == v for k, v in before.target(action).items()):
                return result(state=before.fields(), outcome="already_satisfied")
            if time.monotonic() - min(received[c] for c in (0xFA, 0xE9, 0xEA)) > 10:
                raise DiagnosticError("状态已过期，未发送动作")
            try:
                submitted_at = time.monotonic()
                await client.write_gatt_char(char, packet, response=False)
            except Exception as exc:
                raise UnconfirmedAction(
                    "写入结果未知；未重试", details={"phase": "write", "cause": type(exc).__name__}
                ) from exc
            last_state = None
            try:
                last_mismatch = None
                for _ in range(5):
                    await asyncio.sleep(1)
                    after = await read()
                    last_state = after.fields()
                    try:
                        verified = before.verify(after, action)
                    except UnsafeState as exc:
                        # Wait for transient flags to settle, without another action write.
                        last_mismatch = exc
                        continue
                    if verified:
                        timings["confirmation"] = round((time.monotonic() - submitted_at) * 1000)
                        return result(
                            state=after.fields(),
                            outcome="verified",
                            verification="target_and_known_settings",
                            uninterpreted_changes=before.uninterpreted_changes(after),
                        )
            except Exception as exc:
                raise UnconfirmedAction(
                    "动作已发送，但回读未通过；未重试",
                    details={
                        "phase": "readback",
                        "cause": type(exc).__name__,
                        "last_observed": last_state,
                    },
                ) from exc
            raise UnconfirmedAction(
                "动作已发送，但未观察到稳定目标状态；未重试",
                details={
                    "phase": "verification",
                    "target": before.target(action),
                    "last_observed": last_state,
                    "known_settings_mismatch_seen": last_mismatch is not None,
                },
            ) from last_mismatch
