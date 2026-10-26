import asyncio
from types import SimpleNamespace

import pytest

from jomoo_gateway import control
from jomoo_gateway.model249734 import Snapshot, UnsafeState
from jomoo_gateway.protocol import STATUS_QUERY, Frame


def snapshot(*, settings="4a52b34511322933", lid=5, seated=False):
    return Snapshot(
        bytes.fromhex(settings),
        bytes([128 if seated else 0, 0, 0, 1, 0, 0]),
        bytes([lid]) + bytes(12),
    )


def test_published_lid_vector_and_explicit_ring_preservation():
    assert snapshot().packet("lid_open").hex() == "f3f40f9221114a52b3300010000000000062fc"
    assert snapshot(lid=0x1A).packet("lid_open")[5] == 0x13
    assert snapshot().packet("lid_close")[5] == 0x10


@pytest.mark.parametrize("change", [0x01, 0x04])
def test_unverified_fa_bits_fail_closed(change):
    p = bytearray(snapshot().extended)
    p[0] |= change
    with pytest.raises(UnsafeState, match="尚未验证"):
        snapshot(settings=p.hex()).packet("lid_open")


def test_seated_or_moving_device_is_not_actuated():
    for state in (snapshot(seated=True), snapshot(lid=0x45)):
        with pytest.raises(UnsafeState):
            state.packet("lid_close")


def test_option_verification_detects_unrelated_changes():
    before = snapshot()
    assert before.verify(before, "foot_sensor_off") is False  # A delayed change is not corruption.
    p = bytearray(before.extended)
    p[7] &= ~32
    assert before.verify(snapshot(settings=p.hex()), "foot_sensor_off")
    p[1] ^= 1
    with pytest.raises(UnsafeState, match="其他设置"):
        before.verify(snapshot(settings=p.hex()), "foot_sensor_off")


def install_client(monkeypatch, *, fail_action=False):
    writes = []

    async def target(_identifier):
        return "fixed-device"

    async def no_sleep(_seconds):
        pass

    class Client:
        def __init__(self, device, timeout):
            assert device == "fixed-device"
            self.is_connected = True
            self.state = snapshot()
            self.services = SimpleNamespace(
                get_characteristic=lambda _: SimpleNamespace(
                    properties=["notify", "write-without-response"]
                )
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.is_connected = False

        async def start_notify(self, char, callback):
            self.callback = callback

        async def write_gatt_char(self, char, data, response):
            assert response is False
            writes.append(data)
            if data != STATUS_QUERY:
                if fail_action:
                    raise OSError("connection lost after submission")
                p = bytearray(self.state.extended)
                p[7] &= ~32
                self.state = snapshot(settings=p.hex())
                return
            for c, p in (
                (0xFA, self.state.extended),
                (0xE9, self.state.runtime),
                (0xEA, self.state.mechanics),
            ):
                body = bytes([len(p) + 4, 0x21, 0x92, c]) + p
                self.callback(char, b"\xf3\xf4" + body + bytes([sum(body) & 255, 0xFC]))

    monkeypatch.setattr(control, "verified_target", target)
    monkeypatch.setattr(control, "BleakClient", Client)
    monkeypatch.setattr(control.asyncio, "sleep", no_sleep)
    return writes


def test_failed_actuator_write_is_never_retried(monkeypatch):
    writes = install_client(monkeypatch, fail_action=True)
    with pytest.raises(control.UnconfirmedAction):
        asyncio.run(control.operate("device", "foot_sensor_off"))
    assert len([p for p in writes if p != STATUS_QUERY]) == 1


def test_setting_uses_new_connection_state_and_confirms_readback(monkeypatch):
    writes = install_client(monkeypatch)
    result = asyncio.run(control.operate("device", "foot_sensor_off"))
    assert result["state"]["foot_sensor"] is False
    assert result["outcome"] == "verified"
    assert [Frame.decode(p).command for p in writes] == [0xE2, 0x1F, 0xE2]


def test_satisfied_action_does_not_write(monkeypatch):
    writes = install_client(monkeypatch)
    assert (
        asyncio.run(control.operate("device", "foot_sensor_on"))["outcome"] == "already_satisfied"
    )
    assert writes == [STATUS_QUERY]


def test_unknown_observation_is_reported_without_assigning_a_meaning():
    before = snapshot()
    p = bytearray(before.extended)
    p[5] ^= 1
    after = snapshot(settings=p.hex(), lid=9)
    assert before.verify(after, "lid_open")
    assert before.uninterpreted_changes(after) == ["FA.p5.bit0"]
    p[5] ^= 0x10  # An actual known setting must still fail verification.
    with pytest.raises(UnsafeState):
        before.verify(snapshot(settings=p.hex(), lid=9), "lid_open")


@pytest.mark.parametrize(
    "feature,index,mask", [("automatic_lid", 0, 0x08), ("automatic_night_light", 2, 0x80)]
)
@pytest.mark.parametrize("enabled", [False, True])
def test_parameter_update_preserves_other_fields_and_requires_readback(
    feature, index, mask, enabled
):
    before = snapshot()
    action = f"{feature}_{'on' if enabled else 'off'}"
    frame = Frame.decode(before.packet(action))
    assert frame.command == 0x19
    expected_payload = bytearray.fromhex("4a52b33000100000000000")
    expected_payload[index] = (expected_payload[index] & ~mask) | (mask if enabled else 0)
    assert frame.payload == bytes(expected_payload)
    changed = bytearray(before.extended)
    changed[index] = (changed[index] & ~mask) | (mask if enabled else 0)
    after = snapshot(settings=changed.hex())
    assert before.verify(after, action)
    assert after.fields()[feature] is enabled
    assert not before.verify(snapshot(settings=changed.hex(), lid=9), action)
    changed[index] ^= mask
    assert not before.verify(snapshot(settings=changed.hex()), action)
    changed[index] ^= mask
    # Every other bit covered by the settings signature must remain unchanged.
    signature = before.settings_signature()
    for byte_index in range(8):
        for bit in range(8):
            delta = 1 << bit
            if (byte_index, delta) == (index, mask):
                continue
            candidate = bytearray(changed)
            candidate[byte_index] ^= delta
            altered = snapshot(settings=candidate.hex())
            probe = bytearray(before.extended)
            probe[byte_index] ^= delta
            if snapshot(settings=probe.hex()).settings_signature() == signature:
                continue
            with pytest.raises(UnsafeState, match="其他设置"):
                before.verify(altered, action)


@pytest.mark.parametrize("action", ["automatic_lid_off", "automatic_night_light_off"])
def test_new_settings_reject_occupied_or_unknown_profile(action):
    with pytest.raises(UnsafeState):
        snapshot(seated=True).packet(action)
    with pytest.raises(UnsafeState):
        snapshot(settings="4b52b34511322933").packet(action)
