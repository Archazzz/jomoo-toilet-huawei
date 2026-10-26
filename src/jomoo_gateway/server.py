"""Authenticated LAN gateway with fixed targets and persistent at-most-once dispatch."""

import argparse
import asyncio
import contextlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import UUID

from aiohttp import web

from .control import operate

ACTIONS = frozenset(
    {
        f"{name}_{value}"
        for name, values in {
            "foot_sensor": ("on", "off"),
            "lid": ("open", "close"),
            "ring": ("open", "close"),
        }.items()
        for value in values
    }
)


class Gateway:
    def __init__(self, config: dict, ledger: Path, operation=operate) -> None:
        self.devices = config["devices"]
        if not self.devices or len({d["identifier"] for d in self.devices.values()}) != len(
            self.devices
        ):
            raise ValueError("设备身份必须唯一")
        for device in self.devices.values():
            UUID(device["identifier"])
            if not set(device["actions"]) <= ACTIONS:
                raise ValueError("配置含不支持的动作")
        self.token = config["token"]
        if len(self.token) < 32:
            raise ValueError("令牌至少 32 字符")
        self.operation = operation
        self.radio = asyncio.Lock()
        self.states = {}
        self.db = sqlite3.connect(ledger)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, request TEXT, result TEXT)"
        )
        self.db.commit()
        os.chmod(ledger, 0o600)

    def snapshot(self) -> dict:
        now = time.time()
        return {
            key: {
                "name": device["name"],
                "actions": device["actions"],
                **self.states.get(key, {}),
                "available": bool(self.states.get(key, {}).get("available"))
                and now - self.states.get(key, {}).get("observed_at", 0) < 240,
            }
            for key, device in self.devices.items()
        }

    async def read(self, key: str) -> None:
        # No queue of stale commands behind a long-running BLE operation.
        if self.radio.locked():
            return
        async with self.radio:
            try:
                result = await self.operation(self.devices[key]["identifier"])
                self.states[key] = {"available": True, "observed_at": time.time(), **result}
            except Exception:
                self.states[key] = {"available": False, "error": "读取失败，等待下次只读刷新"}

    async def dispatch(self, data: dict) -> tuple[int, dict]:
        try:
            command_id = str(UUID(data["id"]))
            key, action, issued = data["device"], data["action"], float(data["issued_at"])
            if key not in self.devices or action not in self.devices[key]["actions"]:
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError):
            return 400, {"error": "非法设备、动作或请求标识"}
        request = json.dumps([key, action, issued])
        previous = self.db.execute(
            "SELECT request,result FROM commands WHERE id=?", (command_id,)
        ).fetchone()
        if previous:
            if previous[0] != request:
                return 409, {"error": "请求标识已被其他动作使用"}
            if previous[1] is None:
                return 409, {"error": "先前动作结果未知，不会重放"}
            return tuple(json.loads(previous[1]))
        if not 0 <= time.time() - issued <= 15:
            return 400, {"error": "动作请求已过期或时钟不一致"}
        if self.radio.locked():
            return 409, {"error": "蓝牙忙，请稍后主动操作；没有排队"}
        async with self.radio:
            self.db.execute("INSERT INTO commands VALUES (?,?,NULL)", (command_id, request))
            self.db.commit()  # Commit before any BLE write; a crash must never replay it.
            self.states[key] = {"available": False, "error": "正在执行并校验"}
            try:
                result = await self.operation(self.devices[key]["identifier"], action)
                self.states[key] = {"available": True, "observed_at": time.time(), **result}
                response = (200, result)
            except Exception:
                response = (502, {"error": "动作未确认；请查看设备，不会自动重试"})
                self.states[key] = {"available": False, "error": response[1]["error"]}
            self.db.execute(
                "UPDATE commands SET result=? WHERE id=?", (json.dumps(response), command_id)
            )
            self.db.commit()
            return response

    async def poll(self) -> None:
        while True:
            for key in self.devices:
                await self.read(key)
            await asyncio.sleep(180)


def create_app(gateway: Gateway, *, polling=True) -> web.Application:
    @web.middleware
    async def authenticate(request, handler):
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {gateway.token}".encode()):
            raise web.HTTPUnauthorized()
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=4096)

    async def states(request):
        return web.json_response({"devices": gateway.snapshot()})

    async def command(request):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        status, result = await gateway.dispatch(data)
        return web.json_response(result, status=status)

    async def refresh(request):
        key = request.match_info["device"]
        if key not in gateway.devices:
            raise web.HTTPNotFound()
        if gateway.radio.locked():
            raise web.HTTPConflict(text="蓝牙忙")
        await gateway.read(key)
        return web.json_response({"devices": gateway.snapshot()})

    async def lifetime(app):
        task = asyncio.create_task(gateway.poll()) if polling else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        gateway.db.close()

    app.cleanup_ctx.append(lifetime)
    app.router.add_get("/v1/states", states)
    app.router.add_post("/v1/commands", command)
    app.router.add_post("/v1/devices/{device}/refresh", refresh)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    if config["bind"] in ("0.0.0.0", "::"):
        raise ValueError("请绑定具体家庭局域网接口")
    gateway = Gateway(config, args.config.parent / "commands.sqlite3")
    web.run_app(
        create_app(gateway),
        host=config["bind"],
        port=config.get("port", 18123),
        access_log=None,
        print=None,
        handler_cancellation=False,
    )


if __name__ == "__main__":
    main()
