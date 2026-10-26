import asyncio
from types import SimpleNamespace

import pytest

from jomoo_gateway import diagnostics as diag
from jomoo_gateway.cli import save_private
from jomoo_gateway.protocol import STATUS_QUERY


def discovery(monkeypatch, name=diag.MODEL_NAME, present=True):
    async def find(callback, timeout):
        wrong = SimpleNamespace(address="other", name=name)
        assert not callback(wrong, SimpleNamespace(local_name=name))
        if not present:
            return None
        target = SimpleNamespace(address="target", name=name)
        assert callback(target, SimpleNamespace(local_name=name))
        return target

    monkeypatch.setattr(diag.BleakScanner, "find_device_by_filter", find)


@pytest.mark.parametrize("name", ["JOMOO_SMART_237369", "JOMOO_SMART_249734_extra"])
def test_wrong_or_missing_advertised_model_never_connects(monkeypatch, name):
    discovery(monkeypatch, name)
    with pytest.raises(diag.DiagnosticError, match="名称"):
        asyncio.run(diag.verified_target("target"))


def test_missing_target_does_not_fallback_to_same_name(monkeypatch):
    discovery(monkeypatch, present=False)
    with pytest.raises(diag.DiagnosticError, match="未广播"):
        asyncio.run(diag.verified_target("target"))


def test_wait_for_current_scan_response_name(monkeypatch):
    async def find(callback, timeout):
        target = SimpleNamespace(address="target", name="cached_name")
        assert not callback(target, SimpleNamespace(local_name=None))
        assert callback(target, SimpleNamespace(local_name=diag.MODEL_NAME))
        return target

    monkeypatch.setattr(diag.BleakScanner, "find_device_by_filter", find)
    assert asyncio.run(diag.verified_target("target")).address == "target"


def fake_client(monkeypatch, properties, fail_write=False):
    events = []
    char = SimpleNamespace(properties=properties)

    class Client:
        def __init__(self, device, timeout):
            assert device.address == "target"
            self.services = SimpleNamespace(get_characteristic=lambda uuid: char)
            self.is_connected = True

        async def __aenter__(self):
            events.append("connect")
            return self

        async def __aexit__(self, *args):
            events.append("disconnect")

        async def start_notify(self, characteristic, callback):
            events.append("notify")
            self.callback = callback

        async def write_gatt_char(self, characteristic, data, response):
            events.append((data, response))
            if fail_write:
                raise OSError("lost connection")
            self.callback(char, bytes.fromhex("f3f40b2192e24a52b3450900003dfc"))

    async def no_sleep(seconds):
        pass

    monkeypatch.setattr(diag, "BleakClient", Client)
    monkeypatch.setattr(diag.asyncio, "sleep", no_sleep)
    return events


def test_one_query_and_disconnect(monkeypatch):
    discovery(monkeypatch)
    events = fake_client(monkeypatch, ["notify", "write"])
    result = asyncio.run(diag.query("target", "characteristic", "request"))
    assert events == ["connect", "notify", (STATUS_QUERY, True), "disconnect"]
    assert result["validated_status_received"]


def test_unsupported_write_mode_does_not_write(monkeypatch):
    discovery(monkeypatch)
    events = fake_client(monkeypatch, ["notify", "write"])
    with pytest.raises(diag.DiagnosticError, match="写入"):
        asyncio.run(diag.query("target", "characteristic", "command"))
    assert events == ["connect", "disconnect"]


def test_failed_write_is_not_retried_and_disconnects(monkeypatch):
    discovery(monkeypatch)
    events = fake_client(monkeypatch, ["notify", "write"], fail_write=True)
    with pytest.raises(OSError):
        asyncio.run(diag.query("target", "characteristic", "request"))
    assert events == ["connect", "notify", (STATUS_QUERY, True), "disconnect"]


def test_private_output_does_not_overwrite_or_follow_symlink(tmp_path):
    path = tmp_path / "result.json"
    save_private(path, "original")
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_private(path, "replacement")
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        save_private(link, "replacement")
    assert path.read_text() == "original\n"
