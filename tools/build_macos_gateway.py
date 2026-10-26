"""Build a named macOS BLE app using the current python.org framework runtime.

No system Python bundle is changed. The generated app stays in ignored work/.
Run with the project's virtualenv interpreter. The app starts only the gateway.
"""

import json
import plistlib
import subprocess
import sys
import sysconfig
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = root / "work/gateway/config.local.json"
if not config.is_file():
    raise SystemExit("先创建私有网关配置")
frameworks = sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX")
if not frameworks or sysconfig.get_config_var("PYTHONFRAMEWORK") != "Python":
    raise SystemExit("此构建器要求 python.org macOS Framework Python")
app = root / "work/macos/Jomoo Gateway.app"
if app.exists():
    raise SystemExit("构建目录已存在；先退出 app 并将旧包移走，避免覆盖签名程序的文件 inode")
contents = app / "Contents"
exe = contents / "MacOS/jomoo-gateway"
exe.parent.mkdir(parents=True, exist_ok=True)
source = app.parent / "launcher.m"
source.write_text(
    "#define JOMOO_PYTHON "
    + json.dumps(sys.executable)
    + "\n"
    + "#define JOMOO_CONFIG "
    + json.dumps(str(config))
    + "\n"
    + (root / "tools/macos/gateway.m").read_text()
)
(contents / "Info.plist").write_bytes(
    plistlib.dumps(
        {
            "CFBundleIdentifier": "io.github.archazzz.jomoo-ble-app",
            "CFBundleName": "Jomoo Gateway",
            "CFBundleDisplayName": "九牧蓝牙网关",
            "CFBundleExecutable": exe.name,
            "CFBundlePackageType": "APPL",
            "CFBundleInfoDictionaryVersion": "6.0",
            "NSPrincipalClass": "NSApplication",
            "CFBundleSupportedPlatforms": ["MacOSX"],
            "LSMinimumSystemVersion": "12.0",
            "CFBundleVersion": "1",
            "CFBundleShortVersionString": "0.2.0",
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
            "NSBluetoothPeripheralUsageDescription": "发现并连接已配置的九牧马桶。",
            "NSLocalNetworkUsageDescription": "向家庭 Home Assistant 提供本地控制。",
            "NSBluetoothAlwaysUsageDescription": "连接已配置的九牧马桶，提供状态和翻盖控制。",
        }
    )
)
subprocess.run(
    [
        "clang",
        str(source),
        "-I" + sysconfig.get_config_var("INCLUDEPY"),
        "-F" + frameworks,
        "-framework",
        "Python",
        "-framework",
        "AppKit",
        "-framework",
        "CoreBluetooth",
        "-fobjc-arc",
        "-o",
        str(exe),
    ],
    check=True,
)
subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
print(app)
