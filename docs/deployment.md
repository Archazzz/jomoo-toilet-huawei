# Mac 上的 HA 部署与重启验收

## 版本与存储

使用 Apple Silicon Mac、UTM 5.0.8 和 HAOS 18.3 `generic-aarch64` QCOW2 镜像作为本次部署基线。镜像从官方 release 下载，并核对发布资产的 SHA-256。固定版本用于复现，不意味着后续无需维护更新。

创建 ARM64 虚拟化虚拟机，建议 2 个 vCPU、4 GB 内存、持久化磁盘。磁盘和完整 `.utm` 包保存在本机 `work/` 或其他备份目录，不进 Git。使用有线桥接网卡，让 HA、手机和华为主机处于可通信的局域网；NAT 网络能打开网页，并不足以证明多播发现正常。

参考 [HA 官方 macOS 安装文档](https://www.home-assistant.io/installation/macos/) 和 [UTM 文档](https://docs.getutm.app/)。虚拟机的内置蓝牙不可假定透传；当前诊断运行于 Mac 原生 CoreBluetooth。后续可选择 Mac 传输服务，或在覆盖不足时增加 ESPHome 主动蓝牙代理。

## 华为桥接验证

1. HA 首次启动后创建本地管理员，妥善保存登录信息。
2. 将 `deploy/home-assistant/packages/bridge_probe.yaml` 放入 HA 的 `packages/`；在既有 `homeassistant:` 下合并 `packages: !include_dir_named packages`，不要创建重复 YAML 顶级键。
3. 检查 HA 配置并重启。先在 HA 内确认两个虚拟开关独立工作。
4. 安装固定提交的社区 `chaosl1996/ha-hwhomebridge-entity-bridge`。提交见 `research/repositories.json`；不要混装原版同名 `hwhomebridge` 目录。它包含 Linux 原生库，属于第三方代码。
5. 添加集成，按配置流程完成桥接设置，仅选中两个验证实体。在手机智慧生活里完成发现和绑定，记录所用卡片及命令行为。
6. 分别操作 A、B，确认没有串控，再验证 HA 重启后的发现、身份和绑定。

两个 `input_boolean` 不指定 `initial`，使用 HA 的状态恢复机制；这只验证虚拟实体持久化，不能证明真实马桶在断电后的状态。不要给测试开关绑定冲水或翻盖自动化。

## 重启测试记录

每项都记录版本、测试时间、结果及失败原因，保存在本机 `work/acceptance/`。完整 HA 备份需要包含 `.storage/hwhomebridge/` 和桥接身份；不要删除后重新生成网关身份。

| 测试 | 需要核对 |
|---|---|
| HA 重启 3 次 | 实体 ID、桥接身份不变；智慧生活无需重新绑定；命令仍分别作用于 A/B |
| 虚拟机正常关机再启动 | 磁盘和网卡身份不变；HA、桥接重新上线 |
| Mac 重启 | 用户解锁后虚拟机可启动；网络先恢复；原绑定仍可控制 |
| Mac 休眠/唤醒 | 离线期间显示不可用；恢复后先重新读取设备，不重放旧动作 |
| 单个马桶断线 | 另一台独立可用；不会按信号强度替换目标 |

Mac 重启属于会打断其他工作的操作，应由用户安排时间。FileVault 开启时，冷启动需要解锁磁盘；不要为了自动恢复关闭磁盘加密。登录启动与开机前运行不同，需记录实际边界。系统休眠也会中断宿主蓝牙与虚拟机网络。

**预期**：正常重启保留配置，不必重新配置每个设备。**尚待证明**：华为社区原生库能否在当前环境可靠恢复连接；上游存在相关未关闭 issue，因此不能提前承诺。
