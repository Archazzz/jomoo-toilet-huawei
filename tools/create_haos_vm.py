"""Create a new UTM 5 QEMU bundle from an already verified HAOS ARM64 QCOW2 image.

The plist schema was checked against UTM v5.0.8 Configuration sources.
This only creates files; it does not register, start, or replace any VM.
"""

import argparse
import os
import plistlib
import secrets
import shutil
import uuid
from pathlib import Path


def create(image: Path, output: Path, bridge: str) -> None:
    if output.suffix != ".utm":
        raise ValueError("Output must end in .utm")
    with image.open("rb") as source:
        if source.read(4) != b"QFI\xfb":
            raise ValueError("Expected a decompressed QCOW2 disk")
    mac = ":".join(f"{value:02x}" for value in b"\x02" + secrets.token_bytes(5))
    config = {
        "Backend": "QEMU",
        "ConfigurationVersion": 4,
        "Information": {
            "Name": "Home Assistant JOMOO",
            "UUID": str(uuid.uuid4()).upper(),
            "IconCustom": False,
            "Notes": "HAOS ARM64; persistent disk; bridge probe before hardware control",
        },
        "System": {
            "Architecture": "aarch64",
            "Target": "virt",
            "CPU": "host",
            "CPUFlagsAdd": [],
            "CPUFlagsRemove": [],
            "CPUCount": 2,
            "ForceMulticore": False,
            "MemorySize": 4096,
            "JITCacheSize": 0,
        },
        "QEMU": {
            "DebugLog": False,
            "UEFIBoot": True,
            "RNGDevice": True,
            "BalloonDevice": False,
            "TPMDevice": False,
            "Hypervisor": True,
            "RTCLocalTime": False,
            "PS2Controller": False,
            "AdditionalArguments": [],
        },
        "Input": {"UsbBusSupport": "3.0", "UsbSharing": False, "MaximumUsbShare": 0},
        "Sharing": {
            "DirectoryShareMode": "None",
            "DirectoryShareReadOnly": True,
            "ClipboardSharing": False,
        },
        "Display": [
            {
                "Hardware": "virtio-gpu-pci",
                "DynamicResolution": False,
                "UpscalingFilter": "Linear",
                "DownscalingFilter": "Linear",
                "NativeResolution": False,
            }
        ],
        "Drive": [
            {
                "ImageName": "haos.qcow2",
                "ImageType": "Disk",
                "Interface": "VirtIO",
                "InterfaceVersion": 1,
                "Identifier": str(uuid.uuid4()),
                "ReadOnly": False,
            }
        ],
        "Network": [
            {
                "Mode": "Bridged",
                "Hardware": "virtio-net-pci",
                "MacAddress": mac,
                "IsolateFromHost": False,
                "PortForward": [],
                "BridgeInterface": bridge,
            }
        ],
        "Serial": [{"Mode": "Ptty", "Target": "Auto"}],
        "Sound": [],
    }
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    data = output / "Data"
    data.mkdir(mode=0o700)
    shutil.copyfile(image, data / "haos.qcow2")
    os.chmod(data / "haos.qcow2", 0o600)
    with (output / "config.plist").open("xb") as target:
        plistlib.dump(config, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bridge", required=True, help="Physical interface from networksetup")
    args = parser.parse_args()
    create(args.image, args.output, args.bridge)
    print(f"Created {args.output}; open the bundle in UTM and verify network settings.")


if __name__ == "__main__":
    main()
