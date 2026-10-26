"""Contract tests independent of Home Assistant and of cloud registration."""

import asyncio
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "toilet_oneconnect", Path(__file__).parents[1] / "custom_components/jomoo_local/oneconnect.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    calls = []
    values = {
        "lid": "open",
        "ring": "closed",
        "lid_moving": False,
        "ring_moving": False,
        "seated": False,
        "idle_confirmed": True,
        "automatic_lid": True,
        "foot_sensor": True,
        "water_temperature_level": 5,
        "seat_temperature_level": 5,
        "dryer_temperature_level": 3,
    }
    attrs = {
        "jomoo_model": "249734",
        "jomoo_state": values,
        "jomoo_actions": ["lid_open", "lid_close"],
        "jomoo_expires_at": time.time() + 120,
    }
    states = {
        "switch.toilet_a": SimpleNamespace(state="on", attributes=attrs),
        "switch.toilet_b": SimpleNamespace(
            state="off",
            attributes={
                **attrs,
                "jomoo_state": {**values, "lid": "closed"},
            },
        ),
    }

    async def call(domain, service, data, *, blocking):
        calls.append((domain, service, data, blocking))

    return SimpleNamespace(states=states, services=SimpleNamespace(async_call=call)), calls, attrs


def adapter(sid):
    return module.ToiletServiceAdapter(sid), SimpleNamespace(
        service_id=sid, service_type=module.SERVICE_TYPES[sid], contract=()
    )


def test_same_entity_supplies_multiple_services_without_fabricated_state():
    hass, calls, attrs = fixture()

    async def run():
        expected = {
            "toiletLidStatus": {"status": 4},
            "toiletStatus": {"status": 4},
            "seatStatus": {"status": 0},
            "toiletSetting": {"autoFoot": True, "autoOpenCover": True},
            "drySetting": {"gear": 3},
            "washSetting": {"tempGear": 5, "massage": 0, "selfClean": 0},
            "seatHeating": {"gear": 100},
            "offSeatAction": {},
        }
        for sid, state in expected.items():
            a, service = adapter(sid)
            assert await a.read_state(hass, service, ("switch.toilet_a",)) == state
        a, service = adapter("toiletLidStatus")
        assert await a.read_state(hass, service, ("switch.toilet_b",)) == {"status": 6}
        attrs["jomoo_state"]["ring_moving"] = True
        assert await a.read_state(hass, service, ("switch.toilet_a",)) == {}
        assert not calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {"action": True},
        {"action": "3"},
        {"action": 1},
        {"action": 5},
        {"action": 7},
        {"action": 200},
        {"action": 3, "extra": 1},
        {},
    ],
)
def test_unsupported_commands_never_reach_home_assistant(payload):
    hass, calls, _ = fixture()
    a, service = adapter("offSeatAction")
    with pytest.raises(ValueError):
        asyncio.run(a.apply_command(hass, service, ("switch.toilet_a",), payload))
    assert not calls


def test_command_preflight_is_read_only_and_preserves_target_identity():
    hass, calls, attrs = fixture()
    a, service = adapter("offSeatAction")

    async def run():
        await a.validate_command(hass, service, ("switch.toilet_b",), {"action": 3})
        assert not calls
        await a.apply_command(hass, service, ("switch.toilet_b",), {"action": 3})
        await a.apply_command(hass, service, ("switch.toilet_a",), {"action": 4})
        assert calls == [
            ("switch", "turn_on", {"entity_id": "switch.toilet_b"}, True),
            ("switch", "turn_off", {"entity_id": "switch.toilet_a"}, True),
        ]
        attrs["jomoo_actions"] = ["lid_open"]
        with pytest.raises(ValueError):
            await a.apply_command(hass, service, ("switch.toilet_a",), {"action": 4})
        assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("jomoo_expires_at", 0),
        ("jomoo_expires_at", float("nan")),
        ("jomoo_expires_at", float("inf")),
        ("jomoo_expires_at", True),
        ("jomoo_model", "another-model"),
        ("jomoo_state", None),
    ],
)
def test_expired_or_untrusted_snapshot_neither_reports_nor_controls(field, value):
    hass, calls, attrs = fixture()
    attrs[field] = value
    a, service = adapter("toiletLidStatus")
    assert asyncio.run(a.read_state(hass, service, ("switch.toilet_a",))) == {}
    a, service = adapter("offSeatAction")
    with pytest.raises(ValueError):
        asyncio.run(a.apply_command(hass, service, ("switch.toilet_a",), {"action": 3}))
    assert not calls


def test_read_only_settings_cannot_be_promoted_to_commands():
    hass, calls, _ = fixture()
    a, service = adapter("toiletSetting")
    with pytest.raises(ValueError):
        asyncio.run(a.apply_command(hass, service, ("switch.toilet_a",), {"autoOpenCover": False}))
    service.contract = (("writable", True),)
    with pytest.raises(ValueError):
        a.validate_service(service)
    assert not calls


def test_work_status_never_invents_idle_for_unknown_activity():
    hass, _, attrs = fixture()
    a, service = adapter("toiletStatus")
    state = attrs["jomoo_state"]
    state["idle_confirmed"] = False
    assert asyncio.run(a.read_state(hass, service, ("switch.toilet_a",))) == {}
    state["large_flush_running"] = True
    assert asyncio.run(a.read_state(hass, service, ("switch.toilet_a",))) == {"status": 107}
    state["foam_running"] = True
    assert asyncio.run(a.read_state(hass, service, ("switch.toilet_a",))) == {}


@pytest.mark.parametrize(
    "sid,payload,action",
    [
        ("offSeatAction", {"action": 5}, "ring_open"),
        ("offSeatAction", {"action": 6}, "ring_close"),
        ("offSeatAction", {"action": 1}, "flush_large"),
        ("onSeatAction", {"action": 101}, "wash_auto_bidet"),
        ("stop", {"action": 0}, "stop"),
        ("seatHeating", {"gear": 100}, "seat_temperature_5"),
        ("drySetting", {"gear": 0}, "dryer_temperature_0"),
        ("washSetting", {"waterGear": 0}, "wash_pressure_1"),
        ("washSetting", {"amplitude": 4}, "nozzle_position_5"),
        ("washSetting", {"anterior": 0}, "wash_width_1"),
        ("foamShield", {"on": True}, "automatic_foam_on"),
        ("foamShield", {"action": 1}, "foam_start"),
        ("toiletSetting", {"autoDeo": 0}, "automatic_deodorization_off"),
    ],
)
def test_extended_commands_require_per_device_allowlist_and_keep_single_card(sid, payload, action):
    hass, calls, attrs = fixture()
    a, service = adapter(sid)
    with pytest.raises(ValueError):
        asyncio.run(a.apply_command(hass, service, ("switch.toilet_a",), payload))
    attrs["jomoo_actions"].append(action)
    asyncio.run(a.apply_command(hass, service, ("switch.toilet_a",), payload))
    assert calls == [
        ("jomoo_local", "execute", {"entity_id": "switch.toilet_a", "action": action}, True)
    ]


@pytest.mark.parametrize(
    "sid,payload",
    [
        ("seatHeating", {"gear": 5}),
        ("seatHeating", {"gear": True}),
        ("drySetting", {"gear": 4}),
        ("washSetting", {"waterGear": -1}),
        ("washSetting", {"tempGear": 3, "waterGear": 1}),
        ("onSeatAction", {"action": 200}),
        ("foamShield", {"action": 0}),
        ("toiletSetting", {"mute": 1}),
        ("toiletSetting", {"autoFlush": 1}),
    ],
)
def test_unknown_semantics_and_batch_updates_are_rejected(sid, payload):
    hass, calls, _ = fixture()
    a, service = adapter(sid)
    with pytest.raises(ValueError):
        asyncio.run(a.apply_command(hass, service, ("switch.toilet_a",), payload))
    assert not calls


def test_status_projection_does_not_invent_normality_for_unknown_faults():
    hass, _, attrs = fixture()
    state = attrs["jomoo_state"]
    state.update(faults_present=True, fault_codes=[])
    a, service = adapter("faultDetection")
    assert asyncio.run(a.read_state(hass, service, ("switch.toilet_a",))) == {"status": 1}
    state.update(idle_confirmed=False, wash_mode="bidet", automatic_cycle=True)
    a, service = adapter("toiletStatus")
    assert asyncio.run(a.read_state(hass, service, ("switch.toilet_a",))) == {"status": 201}
