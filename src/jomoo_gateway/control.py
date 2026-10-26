"""Fresh-state sessions with one actuator write, read-back, and no command replay."""

import asyncio
import time

from bleak import BleakClient

from .diagnostics import DiagnosticError, verified_target
from .model249734 import Snapshot, UnsafeState
from .protocol import STATUS_QUERY, FrameStream

CHARACTERISTIC = "0000ffe1-0000-1000-8000-00805f9b34fb"


class UnconfirmedAction(DiagnosticError):
    """A write may have reached hardware; never retry it automatically."""


async def operate(identifier: str, action: str | None = None) -> dict:
    device = await verified_target(identifier)
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
            if action is None:
                return {"state": before.fields(), "outcome": "read"}
            packet = before.packet(action)
            if all(before.fields()[k] == v for k, v in before.target(action).items()):
                return {"state": before.fields(), "outcome": "already_satisfied"}
            if time.monotonic() - min(received[c] for c in (0xFA, 0xE9, 0xEA)) > 10:
                raise DiagnosticError("状态已过期，未发送动作")
            try:
                await client.write_gatt_char(char, packet, response=False)
            except Exception as exc:
                raise UnconfirmedAction("写入结果未知；未重试") from exc
            try:
                last_mismatch = None
                for _ in range(5):
                    await asyncio.sleep(1)
                    after = await read()
                    try:
                        verified = before.verify(after, action)
                    except UnsafeState as exc:
                        # Wait for transient flags to settle, without another action write.
                        last_mismatch = exc
                        continue
                    if verified:
                        return {
                            "state": after.fields(),
                            "outcome": "verified",
                            "verification": "target_and_known_settings",
                            "uninterpreted_changes": before.uninterpreted_changes(after),
                        }
            except Exception as exc:
                raise UnconfirmedAction("动作已发送，但回读未通过；未重试") from exc
            raise UnconfirmedAction("动作已发送，但未观察到稳定目标状态；未重试") from last_mismatch
