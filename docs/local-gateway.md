# Mac 本地网关与 HA 集成

当前链路是「智慧生活 → 华为社区桥接 → HA 的 jomoo_local → Mac HTTP 网关 → 固定身份的九牧 BLE」。HAOS 虚拟机不直接使用 Mac 内置蓝牙。两台设备共用一个无线操作锁，各自保留状态和固定身份，防止同名广播导致串控。

## 1. 创建私有配置

在项目虚拟环境安装 `pip install -e '.[dev]'`。将 `deploy/gateway/config.example.json` 复制到忽略目录 `work/gateway/config.local.json`，权限设为 `600`。示例的 IP、UUID 和令牌都不能直接使用。

- `bind` 填 HA 能访问的 Mac 局域网接口地址，不使用 `0.0.0.0` 或公网监听；为该地址配置 DHCP 保留。
- `token` 用 `secrets.token_urlsafe(32)` 生成随机值并直接写入私有配置，不保存到公开文件或日志。
- 扫描后人工核对每台马桶的房间，将 macOS UUID 填入对应 `identifier`，禁止用名称或 RSSI 自动选设备。
- 第一版只配置 `lid_open`、`lid_close`。代码包含的其他动作不代表已完成验收，不要直接全部开放。

通过已获蓝牙授权的运行环境启动：

```sh
.venv/bin/python -m jomoo_gateway.server --config work/gateway/config.local.json
```

先确认两台只读查询成功。未经授权的独立后台进程可能不能连接蓝牙；当前现场仍受此问题影响，交互运行成功不代表 launchd 自启动成功。

API 使用 Bearer 令牌，当前是家庭局域网 HTTP，没有 TLS，不应转发到互联网或不可信网络。状态接口不包含蓝牙设备 UUID；禁止打印请求鉴权头。

## 2. 安装 HA 集成

将仓库的 `custom_components/jomoo_local` 目录部署到 HA 的 `/config/custom_components/jomoo_local`。在已启用 `packages` 的配置中新增私有文件 `packages/jomoo_gateway_local.yaml`：

```yaml
jomoo_local:
  url: http://192.0.2.10:18123
  token: !secret jomoo_gateway_token
```

将实际令牌写入 HA 私有 `secrets.yaml`。文件名使用下划线，不要使用 `jomoo_gateway.local.yaml`，因为 package 名需要满足 HA 标识规则。执行配置检查并重启 Core，随后确认集成加载日志，配置检查本身不能代替实际加载验证。

该集成按 `devices` 的稳定键建立设备和实体：有成对翻盖动作时建立开关，另提供落座传感器和只读刷新按钮。开表示盖打开，关表示盖和圈合上。落座来自协议状态，不作为人身安全保障；机械动作前还会重新查询设备状态并执行限制检查。

只将两台翻盖实体加入华为桥接白名单，保留已绑定网关身份；无须重新生成 PIN 或删除原设备。智慧生活当前呈现通用开关卡片，并非原生马桶控制面板。验证 A/B 仍是无硬件动作的独立测试实体。

## 3. 调度、失败和重启语义

- 后台每轮串行读取两台，然后默认间隔 60 秒；HA 每 15 秒读取网关缓存。手动遥控器引起的变化不会即时推送，需等下次轮询。
- 状态成功读取后默认 150 秒过期；读取失败即标为不可用。不要把旧开关显示当作当前在线证明。
- 控制优先于后续轮询，最多等待正在进行的读取 5 秒；已有控制请求时拒绝第二个请求，不累积机械动作队列。
- 接受请求前及获得无线锁后都检查 15 秒有效期；只在一次会话内获取新鲜状态并发一次机械指令。操作总超时 85 秒，HA 总超时 95 秒。
- 动作后只重读状态，不重发动作。动作结果未知时请先看实际设备；不自动补发。
- `commands.sqlite3` 持久记录请求身份。完成过的相同请求返回原结果；重启时未完成的请求标为未知，永不重放。删除账本会破坏该去重边界。
- 网关启动只读状态，不根据 HA 保存的开关值自动移动盖板；不能把 HA 状态恢复自动化接到机械控制上。

若小程序占用连接、距离太远或设备暂未广播，命令可能失败。先排查连接和只读查询，再由用户发新的明确请求，不做机械动作自动重试。

## 4. 原生 macOS app（实验性）

`tools/build_macos_gateway.py` 用 python.org Framework Python、clang、AppKit 和 CoreBluetooth 创建独立的 `Jomoo Gateway.app`。它声明蓝牙用途，在 Cocoa 主线程请求系统授权，再在工作线程运行网关。使用项目虚拟环境执行构建器：

```sh
.venv/bin/python tools/build_macos_gateway.py
```

产物在忽略目录 `work/macos/`，嵌入本机 Python 和私有配置的绝对路径，不是可分发安装包，不能上传。构建目录存在时工具拒绝覆盖；先退出旧程序，将旧包整体移到备份位置，再构建新包。替换已签名程序时要创建新文件，不覆盖运行过的 executable inode；重新签名也可能使已有授权需要重新确认。

**现场未完成授权验收**：独立 app 的 CoreBluetooth 授权状态仍为未决定，系统未显示用户可确认的提示；独立 LaunchAgent 已停用。已授权的交互运行环境可读写两台设备，但不能据此声称原生 app 或后台服务可用。不要修改 TCC 数据库或借用其他 app 的身份绕过授权。

HA 虚拟机已有登录启动配置，BLE 网关的独立启动仍是待解决项。Mac 关机或网关进程退出时，华为里的新增马桶控制中断；原配遥控器和马桶自身功能继续工作。恢复连接无需重建两台设备配置，但整机重启自动恢复尚未实测。
