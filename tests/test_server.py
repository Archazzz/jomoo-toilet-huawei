import asyncio
import time
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer

from jomoo_gateway.server import Gateway, create_app


def config():
    return {
        "token": "test-only-" * 5,
        "devices": {
            "a": {
                "identifier": "00000000-0000-0000-0000-000000000001",
                "name": "A",
                "actions": ["lid_open", "lid_close"],
            },
            "b": {
                "identifier": "00000000-0000-0000-0000-000000000002",
                "name": "B",
                "actions": ["lid_open", "lid_close"],
            },
        },
    }


def command(device="a"):
    return {"id": str(uuid4()), "device": device, "action": "lid_open", "issued_at": time.time()}


def test_identity_dispatch_deduplication_and_restart(tmp_path):
    writes = []

    async def operation(identifier, action=None):
        writes.append((identifier, action))
        return {"state": {"lid": "open"}, "outcome": "verified"}

    async def run():
        path = tmp_path / "ledger.db"
        gateway = Gateway(config(), path, operation)
        a, b = command(), command("b")
        assert (await gateway.dispatch(a))[0] == 200
        assert (await gateway.dispatch(b))[0] == 200
        assert [x[0] for x in writes] == [d["identifier"] for d in config()["devices"].values()]
        gateway.db.close()
        restarted = Gateway(config(), path, operation)
        assert not restarted.snapshot()["a"]["available"]
        assert (await restarted.dispatch(a))[0] == 200
        assert len(writes) == 2
        assert (await restarted.dispatch({**a, "device": "b"}))[0] == 409
        restarted.db.close()

    asyncio.run(run())


def test_busy_expired_unknown_and_unfinished_commands_never_execute(tmp_path):
    async def forbidden(*args):
        raise AssertionError("must not execute")

    async def run():
        gateway = Gateway(config(), tmp_path / "ledger.db", forbidden)
        assert (await gateway.dispatch({**command(), "issued_at": time.time() - 30}))[0] == 400
        assert (await gateway.dispatch({**command(), "action": "flush"}))[0] == 400
        async with gateway.radio:
            assert (await gateway.dispatch(command()))[0] == 409
        # Simulate the crash window after journal commit, before any known result.
        import json

        c = command()
        gateway.db.execute(
            "INSERT INTO commands VALUES (?,?,NULL)",
            (c["id"], json.dumps([c["device"], c["action"], c["issued_at"]])),
        )
        gateway.db.commit()
        assert (await gateway.dispatch(c))[0] == 409
        gateway.db.close()

    asyncio.run(run())


def test_auth_and_no_actuation_on_boot_or_read(tmp_path):
    calls = []

    async def operation(identifier, action=None):
        calls.append(action)
        return {"state": {"lid": "closed"}}

    async def run():
        gateway = Gateway(config(), tmp_path / "ledger.db", operation)
        async with TestClient(TestServer(create_app(gateway, polling=False))) as client:
            assert (await client.get("/v1/states")).status == 401
            headers = {"Authorization": f"Bearer {config()['token']}"}
            response = await client.get("/v1/states", headers=headers)
            assert response.status == 200
            assert not (await response.json())["devices"]["a"]["available"]
            await client.post("/v1/devices/a/refresh", headers=headers)
            assert calls == [None]
            gateway.states["a"]["observed_at"] = time.time() - 241
            assert not gateway.snapshot()["a"]["available"]

    asyncio.run(run())
