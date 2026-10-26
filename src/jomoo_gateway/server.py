"""Authenticated LAN gateway with fixed targets and persistent at-most-once dispatch."""

import argparse
import asyncio
import contextlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

from aiohttp import web

from .control import UnconfirmedAction, operate
from .features249734 import ACTIONS
from .transport import LocalTransport

LOGGER = logging.getLogger(__name__)
COMMAND_WAIT_SECONDS = 10
LID_ACTIONS = frozenset({"lid_open", "lid_close"})


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
        self.poll_interval = max(30, float(config.get("poll_interval", 60)))
        self.state_ttl = self.poll_interval + 90
        self.radio = asyncio.Lock()
        self.commands_in_flight = 0
        self.active_device: str | None = None
        self.active_action: str | None = None
        self.poll_wakeup = asyncio.Event()
        self.refresh_first: str | None = None
        self.states = {}
        self.db = sqlite3.connect(ledger)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, request TEXT, result TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cooldowns (device TEXT, kind TEXT, until REAL, "
            "PRIMARY KEY(device,kind))"
        )
        self.db.commit()
        os.chmod(ledger, 0o600)

    @property
    def command_pending(self) -> bool:
        return self.commands_in_flight > 0

    def previous_result(self, command_id: str, request: str) -> tuple[int, dict] | None:
        previous = self.db.execute(
            "SELECT request,result FROM commands WHERE id=?", (command_id,)
        ).fetchone()
        if previous:
            if previous[0] != request:
                return 409, {"error": "请求标识已被其他动作使用"}
            if previous[1] is None:
                return 409, {"error": "先前动作结果未知，不会重放"}
            return tuple(json.loads(previous[1]))
        return None

    def snapshot(self) -> dict:
        now = time.time()
        return {
            key: {
                "name": device["name"],
                "actions": device["actions"],
                **self.states.get(key, {}),
                "expires_at": self.states.get(key, {}).get("observed_at", 0) + self.state_ttl,
                "busy": self.active_device == key,
                "available": bool(self.states.get(key, {}).get("available"))
                and now - self.states.get(key, {}).get("observed_at", 0) < self.state_ttl,
            }
            for key, device in self.devices.items()
        }

    async def read(self, key: str) -> None:
        # Skip polling when control owns the radio or is waiting for it.
        if self.radio.locked() or self.command_pending:
            return
        async with self.radio:
            try:
                async with asyncio.timeout(85):
                    result = await self.operation(self.devices[key]["identifier"])
                self.states[key] = {"available": True, "observed_at": time.time(), **result}
                self._observe_cooldowns(key, result.get("state", {}))
                LOGGER.info("设备 %s 读取成功", key)
            except Exception as exc:
                LOGGER.warning("设备 %s 读取失败：%s", key, type(exc).__name__)
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
        previous = self.previous_result(command_id, request)
        if previous is not None:
            return previous
        if not 0 <= time.time() - issued <= 15:
            return 400, {"error": "动作请求已过期或时钟不一致"}
        # One fresh lid request may follow the same toilet's ongoing lid action.
        # Other actions, another toilet, and a third request never build a queue.
        if self.command_pending and not (
            self.commands_in_flight == 1
            and self.active_device == key
            and ((self.active_action in LID_ACTIONS and action in LID_ACTIONS) or action == "stop")
        ):
            LOGGER.info("设备 %s 拒绝动作 %s：控制等待位已满或目标不符", key, action)
            return 409, {"error": "已有控制请求，等待位已满或目标不同"}
        self.commands_in_flight += 1
        waiting_since = time.monotonic()
        acquired = False
        try:
            try:
                async with asyncio.timeout(COMMAND_WAIT_SECONDS):
                    await self.radio.acquire()
                    acquired = True
            except TimeoutError:
                LOGGER.info("设备 %s 拒绝动作 %s：等待蓝牙超时，未发送", key, action)
                return 409, {"error": "等待蓝牙超时，未发送控制"}
            if not 0 <= time.time() - issued <= 15:
                LOGGER.info("设备 %s 拒绝动作 %s：等待后已过期，未发送", key, action)
                return 400, {"error": "等待期间动作过期，未发送控制"}
            # A concurrent duplicate may have completed while this request waited.
            previous = self.previous_result(command_id, request)
            if previous is not None:
                return previous
            kind = {"flush_large": "flush", "flush_small": "flush", "foam_start": "foam"}.get(
                action
            )
            if kind:
                row = self.db.execute(
                    "SELECT until FROM cooldowns WHERE device=? AND kind=?", (key, kind)
                ).fetchone()
                if row and time.time() < row[0]:
                    return 409, {"error": "冲水或发泡仍在冷却期，未发送动作"}
                self._cooldown(key, kind)
            self.db.execute("INSERT INTO commands VALUES (?,?,NULL)", (command_id, request))
            self.db.commit()
            # Keep the last confirmed state while waiting; executing is not offline.
            # Its original timestamp still expires normally, and no target is guessed.
            self.active_device = key
            self.active_action = action
            operation_started = time.monotonic()
            LOGGER.info(
                "设备 %s 开始动作 %s，等待 %.0f ms",
                key,
                action,
                (operation_started - waiting_since) * 1000,
            )
            try:
                async with asyncio.timeout(85):
                    result = await self.operation(self.devices[key]["identifier"], action)
                self.states[key] = {"available": True, "observed_at": time.time(), **result}
                self._observe_cooldowns(key, result.get("state", {}))
                if result.get("state", {}).get("idle_confirmed") is False:
                    self.refresh_first = key
                    self.poll_wakeup.set()
                response = (200, result)
                LOGGER.info(
                    "设备 %s 动作 %s 完成：%s，耗时 %.0f ms",
                    key,
                    action,
                    result.get("outcome"),
                    (time.monotonic() - operation_started) * 1000,
                )
            except Exception as exc:
                LOGGER.warning("设备 %s 动作未确认：%s", key, type(exc).__name__)
                response = (502, {"error": "动作未确认；请查看设备，不会自动重试"})
                if isinstance(exc, UnconfirmedAction):
                    response[1]["diagnostics"] = exc.details
                    LOGGER.warning("设备 %s 动作诊断：%s", key, exc.details)
                self.states[key] = {"available": False, "error": response[1]["error"]}
                # Wake only the read-only poller, never replay the failed command.
                self.refresh_first = key
                self.poll_wakeup.set()
            self.db.execute(
                "UPDATE commands SET result=? WHERE id=?", (json.dumps(response), command_id)
            )
            self.db.commit()
            return response
        finally:
            if acquired:
                self.active_device = None
                self.active_action = None
                self.radio.release()
            self.commands_in_flight -= 1

    def _cooldown(self, key: str, kind: str) -> None:
        self.db.execute(
            "INSERT INTO cooldowns VALUES (?,?,?) ON CONFLICT(device,kind) "
            "DO UPDATE SET until=max(until,excluded.until)",
            (key, kind, time.time() + 30),
        )
        self.db.commit()

    def _observe_cooldowns(self, key: str, state: dict) -> None:
        if state.get("large_flush_running") or state.get("small_flush_running"):
            self._cooldown(key, "flush")
        if state.get("foam_running"):
            self._cooldown(key, "foam")

    def poll_delay(self) -> float:
        if any(
            s.get("available") and s.get("state", {}).get("idle_confirmed") is False
            for s in self.states.values()
        ):
            return min(3, self.poll_interval)
        return self.poll_interval

    async def poll(self) -> None:
        if sys.platform == "darwin":
            from CoreBluetooth import CBManager
            from Foundation import NSBundle

            LOGGER.info(
                "蓝牙授权状态=%s，应用=%s",
                CBManager.authorization(),
                NSBundle.mainBundle().bundleIdentifier(),
            )
        while True:
            self.poll_wakeup.clear()
            first, self.refresh_first = self.refresh_first, None
            order = list(self.devices)
            if first in order:
                order.remove(first)
                order.insert(0, first)
            for key in order:
                await self.read(key)
            try:
                async with asyncio.timeout(self.poll_delay()):
                    await self.poll_wakeup.wait()
            except TimeoutError:
                pass


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
        if task:

            def completed(finished):
                if not finished.cancelled() and finished.exception():
                    LOGGER.error("只读轮询退出：%s", type(finished.exception()).__name__)

            task.add_done_callback(completed)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    config = json.loads(args.config.read_text())
    if config["bind"] in ("0.0.0.0", "::"):
        raise ValueError("请绑定具体家庭局域网接口")
    gateway = Gateway(config, args.config.parent / "commands.sqlite3", LocalTransport())
    web.run_app(
        create_app(gateway),
        host=config["bind"],
        port=config.get("port", 18123),
        access_log=None,
        print=None,
        handler_cancellation=False,
        handle_signals=threading.current_thread() is threading.main_thread(),
    )


if __name__ == "__main__":
    main()
