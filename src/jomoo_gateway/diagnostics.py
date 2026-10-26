"""Bounded, explicitly targeted BLE sessions. Never sends actuator commands."""

import asyncio

from bleak import BleakClient, BleakScanner

from .protocol import STATUS_QUERY, FrameStream, candidate_fields

MODEL_NAME = "JOMOO_SMART_249734"


class DiagnosticError(RuntimeError):
    """Device identity, GATT capability or status validation failed."""


async def scan(seconds: float = 15) -> list[dict]:
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    return [
        {
            "id": device.address,
            "name": adv.local_name or device.name,
            "rssi": adv.rssi,
            "services": adv.service_uuids,
            "manufacturer_data": {str(k): v.hex() for k, v in adv.manufacturer_data.items()},
        }
        for device, adv in found.values()
        if (adv.local_name or device.name or "").startswith("JOMOO_SMART")
    ]


async def verified_target(identifier: str, seconds: float = 15):
    observed_name = None

    def matches(device, adv):
        nonlocal observed_name
        if device.address.casefold() != identifier.casefold():
            return False
        if adv.local_name is None:
            return False  # Wait for a scan response carrying the current name.
        observed_name = adv.local_name
        return True

    device = await BleakScanner.find_device_by_filter(matches, timeout=seconds)
    if device is None:
        raise DiagnosticError("指定设备未广播或超出范围；不会替换成其他同名设备")
    if observed_name != MODEL_NAME:
        raise DiagnosticError("当前广播名称不匹配 249734，拒绝连接")
    return device


async def inspect(identifier: str) -> dict:
    device = await verified_target(identifier)
    async with asyncio.timeout(35):
        async with BleakClient(device, timeout=20) as client:
            return {
                "device": identifier,
                "services": [
                    {
                        "uuid": service.uuid,
                        "characteristics": [
                            {
                                "uuid": char.uuid,
                                "handle": char.handle,
                                "properties": char.properties,
                            }
                            for char in service.characteristics
                        ],
                    }
                    for service in client.services
                ],
            }


async def query(identifier: str, characteristic: str, mode: str, seconds: float = 8) -> dict:
    if mode not in {"request", "command"}:
        raise DiagnosticError("必须明确选择 request 或 command 写入方式")
    if not 1 <= seconds <= 30:
        raise DiagnosticError("监听时间必须为 1 至 30 秒")
    device = await verified_target(identifier)
    stream = FrameStream()
    frames = []

    def receive(_sender, data):
        frames.extend(stream.feed(bytes(data)))

    async with asyncio.timeout(40 + seconds):
        async with BleakClient(device, timeout=20) as client:
            char = client.services.get_characteristic(characteristic)
            required = "write" if mode == "request" else "write-without-response"
            if char is None or required not in char.properties:
                raise DiagnosticError("目标特征不支持指定写入方式")
            if not {"notify", "indicate"}.intersection(char.properties):
                raise DiagnosticError("目标特征不支持状态通知")
            await client.start_notify(char, receive)
            # One query per session; no retries and no user-supplied byte payload.
            await client.write_gatt_char(char, STATUS_QUERY, response=(mode == "request"))
            await asyncio.sleep(seconds)
            if not client.is_connected:
                raise DiagnosticError("查询期间连接断开，结果不能作为当前状态")
    return {
        "device": identifier,
        "validated_status_received": any(frame.is_known_status for frame in frames),
        "decoding": "candidate_layout_not_hardware_acceptance",
        "invalid_frames": stream.invalid_frames,
        "frames": [
            {
                "raw": frame.raw.hex(),
                "known_status": frame.is_known_status,
                "candidate_fields": candidate_fields(frame),
            }
            for frame in frames
        ],
    }
