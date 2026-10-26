"""Reuse a recently verified peripheral identity, never a cached toilet state."""

import time
from collections.abc import Awaitable, Callable

from bleak.backends.device import BLEDevice

from .control import operate
from .diagnostics import verified_target


class RecentTargets:
    def __init__(
        self,
        resolver: Callable[[str], Awaitable[BLEDevice]] = verified_target,
        *,
        ttl: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.resolver = resolver
        self.ttl = ttl
        self.clock = clock
        self.targets: dict[str, tuple[float, BLEDevice]] = {}

    async def __call__(self, identifier: str) -> BLEDevice:
        key = identifier.casefold()
        cached = self.targets.get(key)
        if cached is not None and self.clock() - cached[0] < self.ttl:
            return cached[1]
        # Resolver checks the exact ID and the advertised model before caching.
        device = await self.resolver(identifier)
        self.targets[key] = (self.clock(), device)
        return device

    def forget(self, identifier: str) -> None:
        self.targets.pop(identifier.casefold(), None)


class LocalTransport:
    def __init__(self, *, targets: RecentTargets | None = None, operation=operate) -> None:
        self.targets = targets if targets is not None else RecentTargets()
        self.operation = operation

    async def __call__(self, identifier: str, action: str | None = None) -> dict:
        try:
            return await self.operation(identifier, action, target_resolver=self.targets)
        except BaseException:
            # A failed/cancelled session invalidates discovery but never retries an action.
            self.targets.forget(identifier)
            raise
