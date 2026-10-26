"""Assemble a pinned experimental integration without touching a running HA instance."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = Path("custom_components/oneconnect_bridge")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"unexpected upstream patch context: {path.name}")
    path.write_text(text.replace(old, new, 1))


def verify_source(source: Path) -> dict:
    lock = json.loads((ROOT / "research/oneconnect-source-lock.json").read_text())
    expected = lock["sha256"]
    actual_paths = {
        str(p.relative_to(source)) for p in (source / COMPONENT).rglob("*") if p.is_file()
    } | {"LICENSE"}
    if actual_paths != set(expected):
        raise ValueError("upstream file inventory differs from the pinned source")
    for name, digest in expected.items():
        path = source / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"upstream source does not match the pinned checksum: {name}")
    return lock


def build(source: Path, output: Path) -> None:
    lock = verify_source(source)
    # Refuse existing directories, including earlier builds and live HA config folders.
    output.mkdir(parents=True, exist_ok=False)
    target = output / COMPONENT
    shutil.copytree(source / COMPONENT, target)
    shutil.copytree(
        ROOT / "custom_components/jomoo_local",
        output / "custom_components/jomoo_local",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copyfile(source / "LICENSE", target / "LICENSE.upstream")
    (target / "NOTICE.jomoo").write_text(
        "Experimental local overlay for jomoo-toilet-huawei.\n"
        f"Upstream: {lock['repository']} at {lock['commit']} (Apache-2.0).\n"
        "Changes: restricted toilet service registry, preflight rejection responses,\n"
        "bounded HA command timeout, dependency and empty initial child mapping.\n"
        "Cloud registration and native UI behavior are not verified by unit tests.\n"
    )
    registry = target / "business/adapters/registry.py"
    registry.write_text(
        registry.read_text()
        + """

# Local extension; original implementations remain in jomoo_local.
from custom_components.jomoo_local.oneconnect import ADAPTERS as TOILET_ADAPTERS

for _adapter in TOILET_ADAPTERS:
    if _adapter.service_id in _SERVICE_ADAPTERS:
        raise ValueError("duplicate toilet service registration")
    _SERVICE_ADAPTERS[_adapter.service_id] = _adapter
"""
    )
    bridge = target / "ha_adapter.py"
    replace_once(
        bridge,
        "self._run(self._async_apply_child_command(child, service_id, payload))",
        "self._run(self._async_apply_child_command(child, service_id, payload), timeout=110)",
    )
    replace_once(bridge, "def _run(self, coroutine):", "def _run(self, coroutine, *, timeout=15):")
    replace_once(
        bridge,
        "return future.result(timeout=15)",
        """try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise""",
    )
    replace_once(
        bridge,
        "    def setup_state_listener(self, plan, callback):",
        """    def validate_child_command(self, child, service_id, payload):
        self._run(self._async_validate_child_command(child, service_id, payload))

    async def _async_validate_child_command(self, child, service_id, payload):
        binding = next((b for b in child.bindings if b.service_id == service_id), None)
        if binding is None:
            raise ValueError("command service is not bound")
        service = next(s for s in child.services if s.service_id == service_id)
        adapter = get_service_adapter(service_id)
        validator = getattr(adapter, "validate_command", None)
        if validator is None:
            raise ValueError("this experimental gateway only accepts validated commands")
        await validator(self._hass, service, binding.entity_ids, payload)

    def setup_state_listener(self, plan, callback):""",
    )
    protocol = target / "business/child_protocol.py"
    replace_once(
        protocol,
        "*, command: ChildCommand, access_token: str, sequence: int",
        "*, command: ChildCommand, access_token: str, sequence: int, error_code: int = 0",
    )
    replace_once(
        protocol,
        '{"errcode": 0, "devicedelay": 0, "devicectltype": 0}',
        '{"errcode": error_code, "devicedelay": 0, "devicectltype": 0}',
    )
    manager = target / "business/child_manager.py"
    replace_once(
        manager,
        """        child, _identity, _material = self._materials[command.dev_id]
        if self._entity_bridge is not None:""",
        """        child, _identity, _material = self._materials[command.dev_id]
        try:
            if self._entity_bridge is None:
                raise ValueError("HA entity bridge unavailable")
            self._entity_bridge.validate_child_command(
                child, command.service_id, command.payload
            )
        except Exception as error:
            _LOGGER.warning("Child command rejected before dispatch: %s", type(error).__name__)
            return OnlineChildCommand(
                response=build_child_command_response(
                    command=command,
                    access_token=client.online_access_token,
                    sequence=client.next_child_response_sequence(),
                    error_code=1,
                ),
                post_ack=None,
            )
        if self._entity_bridge is not None:""",
    )
    # No test car or physical child is registered automatically on pairing.
    (target / "default_mapping.yaml").write_text(
        'schema: 1\nenabled: true\ngateway_serial: ""\nchildren: {}\n'
    )
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["dependencies"] = ["jomoo_local"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    for path in output.rglob("*.py"):
        compile(path.read_text(), str(path), "exec")
    (output / "build-info.json").write_text(
        json.dumps(
            {
                "upstream": {k: lock[k] for k in ("repository", "commit")},
                "files": {
                    str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(output.rglob("*"))
                    if p.is_file()
                },
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.upstream.resolve(), args.output.resolve())
    print(f"Prepared experimental integrations: {args.output}")


if __name__ == "__main__":
    main()
