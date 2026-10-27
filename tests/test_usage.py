import asyncio
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_server import command, config

from jomoo_gateway.server import Gateway, create_app
from jomoo_gateway.usage import ZONE, UsageStore

START = datetime(2099, 1, 1, tzinfo=ZONE).timestamp()
IDLE = {
    "seated": False,
    "wash_mode": "idle",
    "large_flush_running": False,
    "small_flush_running": False,
    "foam_running": False,
}


def test_observed_edges_not_repeated_samples_and_rooms_are_isolated(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    for key in ("a", "b"):
        store.observe(key, IDLE, START)
    store.observe("a", {**IDLE, "large_flush_running": True}, START + 10)
    store.observe("a", {**IDLE, "large_flush_running": True}, START + 20)
    store.observe("a", {**IDLE, "large_flush_running": True}, START + 20)
    store.observe("a", IDLE, START + 15)  # Late delivery must not reset the edge.
    store.observe("a", IDLE, START + 30)
    store.observe("a", {**IDLE, "small_flush_running": True}, START + 40)
    report = store.report("a", "2099-01-01")
    assert report["totals"]["flush_large"] == 1
    assert report["totals"]["flush_small"] == 1
    assert store.report("b", "2099-01-01")["totals"]["flush_large"] == 0
    assert report["daily"][1]["flush_large"] is None  # Missing days are not zero.
    store.close()


def test_same_session_hip_once_and_duration_split_at_midnight(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    t = START + 86400 - 60
    store.observe("a", IDLE, t - 10)
    seated = {**IDLE, "seated": True}
    for offset, mode in [(0, "idle"), (30, "hip"), (50, "idle"), (70, "hip")]:
        store.observe("a", {**seated, "wash_mode": mode}, t + offset)
    store.observe("a", IDLE, t + 100)
    r = store.report("a", "2099-01-01", 2)
    assert r["totals"]["seated"] == r["totals"]["seated_hip"] == 1
    assert [d["seated_seconds_estimate"] for d in r["daily"]] == [60, 40]
    assert [d["seated_hip"] for d in r["daily"]] == [1, 0]
    assert r["sessions"][0]["complete"]
    assert len([e for e in r["events"] if e["kind"] == "hip"]) == 2
    store.close()


@pytest.mark.parametrize("failure", ["missing", "long_gap", "restart"])
def test_active_after_gap_has_unknown_boundary_and_does_not_invent_new_events(tmp_path, failure):
    path = tmp_path / "usage.db"
    store = UsageStore(path)
    state = {**IDLE, "seated": True, "large_flush_running": True, "wash_mode": "hip"}
    store.observe("a", IDLE, START)
    store.observe("a", state, START + 10)
    if failure == "missing":
        store.observe("a", None, START + 20)
    if failure == "restart":
        store.close()
        store = UsageStore(path)
    t = START + (300 if failure == "long_gap" else 40)
    store.observe("a", state, t)
    store.observe("a", IDLE, t + 10)
    r = store.report("a", "2099-01-01", 1)
    assert r["totals"]["flush_large"] == r["totals"]["seated"] == 1
    assert r["totals"]["seated_hip"] == 1
    assert len(r["sessions"]) == 2
    assert all(not s["complete"] for s in r["sessions"])
    assert r["daily"][0]["interrupted"]
    assert r["sessions"][0]["duration_seconds_estimate"] == 10
    store.close()


def test_first_sample_active_unknown_occupancy_and_retention(tmp_path):
    store = UsageStore(tmp_path / "usage.db")
    store.observe("a", {**IDLE, "seated": True, "large_flush_running": True}, START)
    store.observe("a", {"wash_mode": "hip"}, START + 5)  # Not an inferred departure.
    r = store.report("a", "2099-01-01", 1, now=START + 1000)
    assert r["totals"]["seated"] == r["totals"]["flush_large"] == 0
    assert not r["sessions"][0]["complete"]
    assert r["collection"]["stale"]
    store.prune(START + 100 * 86400)
    assert store.report("a", "2099-01-01")["totals"]["seated"] is None
    assert store.db.execute("SELECT COUNT(*) FROM cursor").fetchone()[0] == 0
    store.close()


def test_report_auth_validation_and_disabled_collection(tmp_path):
    async def run(enabled):
        cfg = config()
        cfg["usage_report"] = {"enabled": enabled}
        gateway = Gateway(cfg, tmp_path / f"ledger-{enabled}.db")
        async with TestClient(TestServer(create_app(gateway, polling=False))) as client:
            path = "/v1/devices/a/report?start=2099-01-01&days=7"
            assert (await client.get(path)).status == 401
            auth = {"Authorization": "Bearer " + cfg["token"]}
            response = await client.get(path, headers=auth)
            assert response.status == (200 if enabled else 503)
            if enabled:
                assert response.headers["Cache-Control"] == "no-store"
                assert (await response.json())["totals"]["seated"] is None
                for query in ("", "start=broken", "start=2099-01-01&days=32"):
                    assert (
                        await client.get("/v1/devices/a/report?" + query, headers=auth)
                    ).status == 400
                assert (await client.get(path.replace("/a/", "/c/"), headers=auth)).status == 404

    asyncio.run(run(False))
    asyncio.run(run(True))


def test_failed_report_storage_cannot_break_or_duplicate_control(tmp_path):
    calls = []

    async def operation(identifier, action=None):
        calls.append(action)
        return {"state": {**IDLE, "lid": "open"}, "outcome": "verified"}

    async def run():
        cfg = {**config(), "usage_report": {"enabled": True}}
        gateway = Gateway(cfg, tmp_path / "ledger.db", operation)
        gateway.usage.close()  # Simulate recording failure.
        c = command()
        assert (await gateway.dispatch(c))[0] == 200
        assert (await gateway.dispatch(c))[0] == 200
        assert calls == ["lid_open"]
        gateway.db.close()

    asyncio.run(run())


def test_read_token_is_scoped_to_one_device_and_cannot_control(tmp_path):
    async def run():
        cfg = config()
        cfg["usage_report"] = {"enabled": True, "read_tokens": {"a": "a-report-only-" * 4}}
        gateway = Gateway(cfg, tmp_path / "ledger.db")
        headers = {"Authorization": "Bearer " + cfg["usage_report"]["read_tokens"]["a"]}
        async with TestClient(TestServer(create_app(gateway, polling=False))) as client:
            assert (
                await client.get("/v1/devices/a/report?start=2099-01-01", headers=headers)
            ).status == 200
            assert (
                await client.get("/v1/devices/b/report?start=2099-01-01", headers=headers)
            ).status == 401
            assert (await client.get("/v1/states", headers=headers)).status == 401
            assert (
                await client.post("/v1/commands", json=command(), headers=headers)
            ).status == 401
            assert (await client.post("/v1/devices/a/refresh", headers=headers)).status == 401

    asyncio.run(run())


def test_transport_readbacks_feed_real_edges_once_despite_command_deduplication(tmp_path):
    from jomoo_gateway.transport import LocalTransport

    async def operation(identifier, action, *, target_resolver, observer):
        observer(IDLE)
        running = {**IDLE, "large_flush_running": True}
        observer(running)
        return {"state": running, "outcome": "verified"}

    async def run():
        gateway = Gateway(
            {**config(), "usage_report": {"enabled": True}},
            tmp_path / "ledger.db",
            LocalTransport(operation=operation),
        )
        try:
            c = command()
            assert (await gateway.dispatch(c))[0] == 200
            assert (await gateway.dispatch(c))[0] == 200
            rows = gateway.usage.db.execute("SELECT device,kind FROM events").fetchall()
            assert [tuple(r) for r in rows] == [("a", "flush_large")]
        finally:
            gateway.usage.close()
            gateway.db.close()

    asyncio.run(run())
