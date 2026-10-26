# 本机蓝牙诊断

需要 Python 3.12 及以上版本。macOS 使用 CoreBluetooth；Linux 需要正常工作的 BlueZ。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
mkdir -p work
.venv/bin/jomoo --output work/scan.json scan
.venv/bin/jomoo --output work/gatt.json inspect --device '<扫描得到的明确 ID>'
```

输出文件以 `0600` 权限新建，已有文件不覆盖。不要把包含设备 ID、广播数据和状态的文件提交到公开仓库。macOS 上的 UUID 是本机身份，不是 BLE MAC，也不能直接用于 ESPHome。

`inspect` 仅枚举服务和特征。连接前必须同时满足明确的设备 ID 和当前广播名称 `JOMOO_SMART_249734`；广播暂时不含名称时等待扫描响应，不信任操作系统缓存名称。找不到指定设备时不会回退到其他同名设备。

首次使用时允许操作系统的蓝牙权限提示。连接可能产生设备提示音；官方小程序正在连接时，请先退出设备控制页。远端设备连接失败应调整覆盖，不要自动改连近端设备。

## 候选状态查询

先核对枚举结果。公开 F3F4 协议使用 FFE0 服务下的 FFE1 特征；只有现场枚举确认其具有通知及相应写入能力后，才运行一次查询。

```sh
.venv/bin/jomoo --output work/query.json query \
  --device '<明确 ID>' \
  --characteristic 0000ffe1-0000-1000-8000-00805f9b34fb \
  --write-mode command
```

`command` 要求 `write-without-response`；`request` 要求 `write`。工具不自动猜测或切换写入方式，不开放自定义帧接口。查询固定为 `f3f4089200e2000000007cfc`，订阅通知后只发一次，监听 8 秒并断开。该帧在参考机型用于读取状态，本型号仍属于验证过程。

退出码 `0` 表示命令成功；查询还要求至少收到一条通过结构及长度检查的候选状态帧。退出码 `2` 表示查询完成但没有有效状态；`1` 表示诊断失败；`130` 表示用户中断。候选字段不代表功能已通过现场验收。回显查询帧不能作为状态。

## 开发检查

```sh
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/pytest -q
git diff --check
```

测试使用公开帧和模拟传输，覆盖损坏帧、分包、设备误匹配、写入方式、失败释放连接及私有文件处理，不连接真实设备。
