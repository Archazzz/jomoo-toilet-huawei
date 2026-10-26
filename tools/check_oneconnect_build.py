"""Run in HA's Python runtime against an isolated build; uses only fake entities and clients."""

import argparse
import asyncio
import json
import pathlib
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a staged OneConnect build without HA services or cloud access"
    )
    parser.add_argument("--build", required=True, type=pathlib.Path)
    root = parser.parse_args().build.resolve()
    sys.path.insert(0, str(root))
    from custom_components.oneconnect_bridge.business import child_manager

    assert pathlib.Path(child_manager.__file__).resolve().is_relative_to(root)
    from custom_components.oneconnect_bridge.business.mapping_config import load_enabled_mapping
    from custom_components.oneconnect_bridge.ha_adapter import HAEntityBridge

    from custom_components.jomoo_local.oneconnect import SERVICE_TYPES

    mapping = {"schema": 1, "enabled": True, "children": {}}
    for key in ["a", "b"]:
        mapping["children"][key] = {
            "product": {
                "prod_id": "2QBR",
                "sub_prod_id": "00",
                "device_type": "123",
                "manufacturer": "cfe",
                "model": "ZD6450-SA-CJM305",
                "protocol_type": 1,
                "setup_type": 0,
                "friendly_name": "test-" + key,
                "services": [{"st": st, "sid": sid} for sid, st in SERVICE_TYPES.items()],
            },
            "bindings": [{"sid": sid, "entities": ["switch.test_" + key]} for sid in SERVICE_TYPES],
        }
    with tempfile.TemporaryDirectory() as temporary:
        p = pathlib.Path(temporary) / "mapping.yaml"
        p.write_text(json.dumps(mapping))
        plan = load_enabled_mapping(p)
    assert len(plan.children) == 2
    assert all(len(c.services) == 7 for c in plan.children)
    assert [c.bindings[0].entity_ids for c in plan.children] == [
        ("switch.test_a",),
        ("switch.test_b",),
    ]
    assert (
        len(
            load_enabled_mapping(
                root / "custom_components/oneconnect_bridge/default_mapping.yaml"
            ).children
        )
        == 0
    )

    calls = []

    async def call(*args, **kwargs):
        calls.append((args, kwargs))

    hass = SimpleNamespace(
        states={
            "switch.test_a": SimpleNamespace(
                state="on",
                attributes={
                    "jomoo_model": "249734",
                    "jomoo_state": {
                        "lid": "open",
                        "ring": "closed",
                        "lid_moving": False,
                        "ring_moving": False,
                        "seated": False,
                    },
                    "jomoo_actions": ["lid_open", "lid_close"],
                    "jomoo_expires_at": time.time() + 150,
                },
            )
        },
        services=SimpleNamespace(async_call=call),
    )
    bridge = HAEntityBridge(hass)

    async def check_bridge():
        child = plan.children[0]
        data = await bridge._async_read_child_state(child)
        assert data["toiletLidStatus"] == {"status": 4}
        for sid, payload in [
            ("offSeatAction", {"action": 1}),
            ("stop", {"action": 0}),
            ("toiletSetting", {"autoOpenCover": False}),
        ]:
            try:
                await bridge._async_validate_child_command(child, sid, payload)
            except ValueError:
                pass
            else:
                raise AssertionError("unsupported command accepted")
        await bridge._async_validate_child_command(child, "offSeatAction", {"action": 4})
        assert calls == []
        await bridge._async_apply_child_command(child, "offSeatAction", {"action": 4})
        assert calls[0][0] == ("switch", "turn_off", {"entity_id": "switch.test_a"})

    asyncio.run(check_bridge())

    manager = child_manager.ChildManager.__new__(child_manager.ChildManager)
    manager._materials = {"fake-device": (plan.children[0], None, None)}
    command = SimpleNamespace(
        dev_id="fake-device",
        service_id="offSeatAction",
        payload={"action": 1},
        request_id=b"a",
        context=b"b",
        message=SimpleNamespace(token=b"abcd"),
    )
    client = SimpleNamespace(
        online_access_token="unit-test-only", next_child_response_sequence=lambda: 1
    )

    class Reject:
        def validate_child_command(self, *args):
            raise ValueError("unsupported")

        def apply_child_command(self, *args):
            raise AssertionError("must not actuate")

    manager._entity_bridge = Reject()
    with patch.object(child_manager, "decode_child_command", return_value=command):
        result = manager.handle_cloud_command(client, None)
    assert result.post_ack is None
    assert json.loads(result.response.split(b"\xff", 1)[1])["errcode"] == 1
    print(
        json.dumps(
            {
                "import": "passed",
                "mapping": "two children, seven services each",
                "empty_default": "passed",
                "service_routing": "passed",
                "unsupported_preflight": "rejected without dispatch",
                "local_error_response": 1,
                "cloud_registration": "not tested",
                "physical_actions": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
