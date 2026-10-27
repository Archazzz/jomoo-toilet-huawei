"""Loopback-only, synthetic card host. No Bluetooth imports or device commands."""

import argparse
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

from jomoo_gateway.usage import ZONE, UsageStore

ASSETS = Path(__file__).resolve().parents[1] / "web" / "usage-report"
TODAY = "2026-10-27"
DEVICES = {"main": "主卫九牧马桶", "guest": "客卫九牧马桶"}
IDLE = {
    "seated": False,
    "wash_mode": "idle",
    "large_flush_running": False,
    "small_flush_running": False,
    "foam_running": False,
}


def seed(store):
    first = datetime.fromisoformat(TODAY).replace(tzinfo=ZONE) - timedelta(days=6)
    for device in DEVICES:
        for day, count in enumerate([5, 7, 4, 8, 6, 9, 5]):
            if device == "guest":
                count = max(1, count - 3)
            for event in range(count):
                t = (first + timedelta(days=day, hours=7 + event * 1.5)).timestamp()
                store.observe(device, IDLE, t)
                if event < 2:
                    for minute in range(8 + event * 4):
                        store.observe(
                            device,
                            {**IDLE, "seated": True, "wash_mode": "hip" if minute > 4 else "idle"},
                            t + 10 + minute * 60,
                        )
                    t += 10 + (8 + event * 4) * 60
                    store.observe(device, IDLE, t)
                flag = "large_flush_running" if event % 3 == 0 else "small_flush_running"
                store.observe(device, {**IDLE, flag: True}, t + 10)
                store.observe(device, IDLE, t + 20)


def create_preview(store):
    @web.middleware
    async def local_only(request, handler):
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            raise web.HTTPForbidden()
        origin = request.headers.get("Origin")
        if origin and origin != f"http://{request.host}":
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'self'"
        )
        return response

    app = web.Application(middlewares=[local_only])

    async def context(request):
        device = request.query.get("device", "main")
        if device not in DEVICES:
            raise web.HTTPNotFound()
        return web.json_response(
            {"device": device, "name": DEVICES[device], "today": TODAY, "demo": True}
        )

    async def report(request):
        device = request.query.get("device", "main")
        if device not in DEVICES:
            raise web.HTTPNotFound()
        try:
            data = store.report(
                "empty" if request.query.get("scenario") == "empty" else device,
                request.query["start"],
                int(request.query.get("days", "7")),
                now=datetime(2026, 10, 27, 23, tzinfo=ZONE).timestamp(),
            )
            data["device"] = device
        except (KeyError, ValueError, OverflowError):
            raise web.HTTPBadRequest() from None
        return web.json_response(data)

    async def static(request):
        name = request.match_info.get("name") or "demo.html"
        if name == "favicon.ico":
            return web.Response(status=204)
        if name not in {p.name for p in ASSETS.iterdir() if p.suffix in {".html", ".js", ".css"}}:
            raise web.HTTPNotFound()
        return web.FileResponse(ASSETS / name)

    app.router.add_get("/api/context", context)
    app.router.add_get("/api/report", report)
    app.router.add_get("/", static)
    app.router.add_get("/{name}", static)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jomoo-report-demo-") as directory:
        store = UsageStore(Path(directory) / "synthetic.sqlite3")
        try:
            seed(store)
            web.run_app(create_preview(store), host="127.0.0.1", port=args.port, access_log=None)
        finally:
            store.close()


if __name__ == "__main__":
    main()
