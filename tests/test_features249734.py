"""Synthetic hardware-free vectors for expanded controls and interlocks."""

from dataclasses import replace

import pytest

from jomoo_gateway.features249734 import ACTIONS, LEVELS, OCCUPIED_STARTS
from jomoo_gateway.model249734 import Snapshot, UnsafeState
from jomoo_gateway.protocol import Frame


def state(*, occupied=False, lid=5, work=1, flags=0, extra=0):
    return Snapshot(
        bytes.fromhex("4a52b34511322933"),
        bytes([128 if occupied else 0, 0, 0, work, flags, extra]),
        bytes([lid]) + bytes(12),
    )


@pytest.mark.parametrize("action", sorted(OCCUPIED_STARTS))
def test_wash_needs_real_occupancy_and_open_lid_with_lowered_ring(action):
    for bad in (state(), state(occupied=True), state(occupied=True, lid=10)):
        with pytest.raises(UnsafeState):
            bad.packet(action)
    before = state(occupied=True, lid=9)
    assert Frame.decode(before.packet(action)).source == 0x92
    with pytest.raises(UnsafeState):
        state(occupied=True, lid=9, work=0x20).packet(action)


def test_stop_is_allowed_during_occupied_faulted_operation_and_needs_idle_readback():
    before = state(occupied=True, lid=9, work=0x20)
    before = replace(before, runtime=bytes([128, 1, 0, 0x20, 0, 0]))
    assert Frame.decode(before.packet("stop")).command == 0
    assert not before.verify(before, "stop")
    assert before.verify(state(occupied=True, lid=9), "stop")


@pytest.mark.parametrize("feature", LEVELS)
def test_setting_boundaries_preserve_other_settings_and_detect_collateral_change(feature):
    before = state()
    index, mask, shift, minimum, maximum = LEVELS[feature]
    for value in range(minimum, maximum + 1):
        action = f"{feature}_{value}"
        packet = Frame.decode(before.packet(action))
        assert packet.command == 0x19
        expected = bytearray.fromhex("4a52b33000100000000000")
        if feature == "wash_width":
            expected[3] = value << 4
        else:
            expected[index] = (expected[index] & ~mask) | (value << shift)
        assert packet.payload == expected
        changed = bytearray(before.extended)
        changed[index] = (changed[index] & ~mask) | (value << shift)
        after = replace(before, extended=bytes(changed))
        assert before.verify(after, action)
        changed[6] ^= 8  # Unrequested automatic-foam setting.
        with pytest.raises(UnsafeState, match="其他设置"):
            before.verify(replace(before, extended=bytes(changed)), action)
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(UnsafeState):
            before.packet(f"{feature}_{value}")


@pytest.mark.parametrize("action", ["flush_large", "flush_small", "foam_start", "self_clean"])
def test_unoccupied_maintenance_requires_closed_lid_and_never_accepts_idle_as_started(action):
    before = state()
    before.packet(action)
    assert not before.verify(before, action)
    with pytest.raises(UnsafeState):
        state(lid=9).packet(action)
    with pytest.raises(UnsafeState):
        state(occupied=True).packet(action)


def test_all_movement_rejects_flush_foam_and_automatic_activity_even_with_standby_byte():
    base = state()
    for index, mask in ((4, 4), (4, 8), (6, 4)):
        changed = bytearray(base.extended)
        changed[index] |= mask
        with pytest.raises(UnsafeState):
            replace(base, extended=bytes(changed)).packet("ring_open")
    with pytest.raises(UnsafeState):
        state(flags=8).packet("lid_open")


def test_bidet_command_preserves_separate_female_pressure_and_position():
    packet = Frame.decode(state(occupied=True, lid=9).packet("wash_bidet"))
    assert packet.command == 11
    assert packet.payload[1] >> 4 & 7 == 5
    assert packet.payload[2] >> 4 & 7 == 4


def test_running_state_and_toggle_targets_come_from_observations():
    before = state(occupied=True, lid=9, work=0x20)
    after = state(occupied=True, lid=9, work=0xA0)
    assert before.verify(after, "move_on")
    assert after.verify(before, "move_off")
    assert not before.verify(before, "move_on")
    assert state(occupied=True, lid=9, work=0x40, flags=8).fields()["wash_mode"] == "bidet"
    assert state(work=131).fields()["wash_mode"] == "nozzle_replace"
    assert state(work=255).fields()["wash_mode"] == "unknown"
    assert not state(extra=1).fields()["work_idle"]


def test_finite_actions_do_not_include_reset_or_update_or_raw_write():
    assert not {"reset", "firmware_update", "write", "water_temperature_99"} & ACTIONS
    for action in ("", "unknown", "water_temperature_05", "seat_temperature_100"):
        with pytest.raises(UnsafeState):
            state().packet(action)
