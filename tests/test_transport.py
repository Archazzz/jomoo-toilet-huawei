import asyncio

import pytest

from jomoo_gateway.transport import LocalTransport, RecentTargets


def test_recent_targets_keep_id_isolation_and_expire():
    now = [0]
    scans = []

    async def resolve(identifier):
        scans.append(identifier)
        return object()

    async def run():
        cache = RecentTargets(resolve, ttl=60, clock=lambda: now[0])
        a = await cache("A")
        b = await cache("B")
        assert a is not b
        assert await cache("a") is a
        now[0] = 59
        assert await cache("A") is a
        now[0] = 60
        assert await cache("A") is not a
        cache.forget("b")
        assert await cache("B") is not b
        assert scans == ["A", "B", "A", "B"]

    asyncio.run(run())


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_session_invalidates_target_without_retry(cancelled):
    scans, actions = [], []

    async def resolve(identifier):
        scans.append(identifier)
        return object()

    async def operation(identifier, action, *, target_resolver):
        await target_resolver(identifier)
        actions.append(action)
        if cancelled:
            raise asyncio.CancelledError()
        raise OSError("unconfirmed write")

    async def run():
        cache = RecentTargets(resolve)
        transport = LocalTransport(targets=cache, operation=operation)
        with pytest.raises(asyncio.CancelledError if cancelled else OSError):
            await transport("A", "lid_open")
        assert actions == ["lid_open"]
        assert scans == ["A"]
        assert not cache.targets

    asyncio.run(run())
