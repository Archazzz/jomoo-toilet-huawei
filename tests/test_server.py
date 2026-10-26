import asyncio
import time
from uuid import uuid4

import pytest
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


@pytest.mark.parametrize("fails", [False, True])
def test_busy_preserves_confirmed_state_but_never_masks_expiry_or_failure(tmp_path, fails):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def operation(identifier, action=None):
            if action:
                started.set()
                await release.wait()
                if fails:
                    raise TimeoutError("result unknown")
            return {"state": {"lid": "open" if action else "closed"}}

        gateway = Gateway(config(), tmp_path / "ledger.db", operation)
        await gateway.read("a")
        before = gateway.snapshot()["a"]
        task = asyncio.create_task(gateway.dispatch(command()))
        await started.wait()
        during = gateway.snapshot()["a"]
        assert during["available"] and during["busy"]
        assert during["state"]["lid"] == "closed"
        assert during["observed_at"] == before["observed_at"]
        assert not gateway.snapshot()["b"]["available"]
        assert not gateway.snapshot()["b"]["busy"]

        gateway.states["a"]["observed_at"] -= gateway.state_ttl + 1
        assert not gateway.snapshot()["a"]["available"]
        release.set()
        assert (await task)[0] == (502 if fails else 200)
        after = gateway.snapshot()["a"]
        assert not after["busy"]
        assert after["available"] is not fails
        if not fails:
            assert after["state"]["lid"] == "open"
        gateway.db.close()

    asyncio.run(run())


def test_busy_expired_unknown_and_unfinished_commands_never_execute(tmp_path):
    async def forbidden(*args):
        raise AssertionError("must not execute")

    async def run():
        gateway = Gateway(config(), tmp_path / "ledger.db", forbidden)
        assert (await gateway.dispatch({**command(), "issued_at": time.time() - 30}))[0] == 400
        assert (await gateway.dispatch({**command(), "action": "flush"}))[0] == 400
        async with gateway.radio:
            gateway.commands_in_flight = 1
            assert (await gateway.dispatch(command()))[0] == 409
        gateway.commands_in_flight = 0
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


def test_only_one_control_can_wait_behind_a_read(tmp_path):
    async def run():
        reading = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def operation(identifier, action=None):
            calls.append(action)
            if action is None:
                reading.set()
                await release.wait()
            return {"state": {"lid": "open"}}

        gateway = Gateway(config(), tmp_path / "ledger.db", operation)
        read = asyncio.create_task(gateway.read("b"))
        await reading.wait()
        action = asyncio.create_task(gateway.dispatch(command()))
        await asyncio.sleep(0)
        assert (await gateway.dispatch(command()))[0] == 409
        release.set()
        await read
        assert (await action)[0] == 200
        assert calls == [None, "lid_open"]
        gateway.db.close()

    asyncio.run(run())


def test_one_lid_request_can_follow_same_device_without_replaying(tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def operation(identifier, action=None):
            calls.append(action)
            if len(calls) == 1:
                started.set()
                await release.wait()
            return {"state": {"lid": "open" if action == "lid_open" else "closed"}}

        gateway = Gateway(config(), tmp_path / "ledger.db", operation)
        first = asyncio.create_task(gateway.dispatch(command()))
        await started.wait()
        close = {**command(), "action": "lid_close"}
        second = asyncio.create_task(gateway.dispatch(close))
        await asyncio.sleep(0)
        assert gateway.commands_in_flight == 2
        assert (await gateway.dispatch(command()))[0] == 409
        assert (await gateway.dispatch(command("b")))[0] == 409
        release.set()
        assert (await first)[0] == 200
        assert (await second)[0] == 200
        assert calls == ["lid_open", "lid_close"]
        assert (await gateway.dispatch(close))[0] == 200
        assert calls == ["lid_open", "lid_close"]
        assert not gateway.command_pending
        assert not gateway.snapshot()["a"]["busy"]
        gateway.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("expired", [False, True])
def test_waiting_lid_timeout_or_expiry_never_executes(tmp_path, monkeypatch, expired):
    from jomoo_gateway import server

    monkeypatch.setattr(server, "COMMAND_WAIT_SECONDS", 0.02 if not expired else 1)
    now = [time.time()]
    monkeypatch.setattr(server.time, "time", lambda: now[0])

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def operation(identifier, action=None):
            calls.append(action)
            started.set()
            await release.wait()
            return {"state": {"lid": "open"}}

        gateway = Gateway(config(), tmp_path / "ledger.db", operation)
        first = asyncio.create_task(gateway.dispatch(command()))
        await started.wait()
        close = {**command(), "action": "lid_close", "issued_at": now[0] - 14}
        second = asyncio.create_task(gateway.dispatch(close))
        await asyncio.sleep(0)
        if expired:
            now[0] += 2
            release.set()
            assert (await second)[0] == 400
        else:
            assert (await second)[0] == 409
            assert gateway.active_device == "a"  # Waiting timeout cannot clear active state.
            assert gateway.snapshot()["a"]["busy"]
            release.set()
        assert (await first)[0] == 200
        assert calls == ["lid_open"]
        assert gateway.db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert not gateway.command_pending
        gateway.db.close()

    asyncio.run(run())


def test_failed_command_wakes_read_only_recovery_without_replay(tmp_path, monkeypatch):
    from jomoo_gateway import server

    monkeypatch.setattr(server.sys, "platform", "linux")

    async def run():
        boot_read, recovered = asyncio.Event(), asyncio.Event()
        calls = []
        failed = False

        async def operation(identifier, action=None):
            nonlocal failed
            calls.append((identifier, action))
            if action:
                failed = True
                raise server.UnconfirmedAction("unconfirmed", details={"phase": "verification"})
            if failed and identifier == config()["devices"]["a"]["identifier"]:
                recovered.set()
            if identifier == config()["devices"]["b"]["identifier"]:
                boot_read.set()
            return {"state": {"lid": "closed"}, "outcome": "read"}

        gateway = Gateway(config(), tmp_path / "ledger.db", operation)
        poll = asyncio.create_task(gateway.poll())
        await boot_read.wait()
        status, body = await gateway.dispatch(command())
        assert status == 502 and body["diagnostics"]["phase"] == "verification"
        await asyncio.wait_for(recovered.wait(), timeout=1)
        assert gateway.snapshot()["a"]["available"]
        assert [action for _, action in calls if action] == ["lid_open"]
        assert (await gateway.dispatch(command()))[0] == 502  # Only a new explicit request writes.
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll
        gateway.db.close()

    asyncio.run(run())
