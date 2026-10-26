# 调研来源与兼容边界

核查日期：2026-10-26 至 2026-10-27。固定版本记录见 `research/repositories.json`。

| 来源 | 可复用之处 | 不能据此推断的能力 |
|---|---|---|
| [ha-jomoo-ble](https://github.com/xiaoyaner0201/ha-jomoo-ble) | ZS800J / 237369 的 F3F4 帧、状态解析、HA 蓝牙接入 | 未支持 249734；写入方式与字段仍需实测 |
| [JomooToilet](https://github.com/wilton999/JomooToilet) | ZS781J / 279331 的 Python BLE 控制与帧说明 | 默认最强信号选择不适合多设备；不能照搬自动重试 |
| [JMGatt_client](https://github.com/caokang1997/JMGatt_client) | 公开设备表提供 249734 → ZD6450-SA / bleProtocol 0 的线索 | SQ9650 使用另一帧族；仓库未明确许可，不搬入其程序资源 |
| [JOMOO_HA](https://github.com/sorrymakeeeer/JOMOO_HA) | SQ9650 实验集成的连接生命周期 | README 明示未做真机测试；不是本型号驱动 |
| [ha_hwhomebridge](https://github.com/Wangxiaokang666-666/ha_hwhomebridge) | HA → 华为，Linux 预编译桥接库 | 现有品类没有马桶；未找到完整底层 C 源码 |
| [实体桥接分支](https://github.com/chaosl1996/ha-hwhomebridge-entity-bridge) | 选择 button、script、switch 等实体导出 | 通常映射为开关面板；不保证完整温度滑块或原生卡片 |
| [ha-huawei-smarthome](https://github.com/xiasi0/ha-huawei-smarthome) | 导入已有华为设备到 HA | 方向相反，不能用它将新九牧设备导入华为 |

华为桥接上游 [#39](https://github.com/Wangxiaokang666-666/ha_hwhomebridge/issues/39) 报告重启掉线，[#40](https://github.com/Wangxiaokang666-666/ha_hwhomebridge/issues/40) 报告网关发现失败，核查时仍开放。它们是上游环境报告，不代表所有环境必现；因此先做虚拟实体和重启验收。

## 官方文档

- [HA macOS 部署](https://www.home-assistant.io/installation/macos/)：Apple Silicon HAOS 虚拟机、桥接网络。
- [ESPHome BLE Proxy](https://esphome.io/components/bluetooth_proxy/)：主动 GATT 连接与主动扫描是不同配置。
- [华为产品开发全流程](https://devicepartner.huawei.com/cn/enablement/kit)：产品定义、设备/应用开发、自测和认证。
- [华为创建产品](https://device.harmonyos.com/cn/docs/devicepartner/DevicePartner-Guides/product-definition-0000001134540425)：接入方案和品类；云云对接仅对受邀伙伴开放。
- [华为 BLE 开发](https://device.harmonyos.com/cn/docs/devicepartner/DevicePartner-Guides/device-development-ble-0000001421691393)：产品定义、注册、广播规范和固件开发。
- [华为北向生态](https://devicepartner.huawei.com/cn/certifications-openlab/)：本地 SDK、云云及中控屏插件能力，详细手册面向签约伙伴。

品牌合作新闻、共享 GATT UUID、搜索引擎 AI 概览均不作为型号兼容性证据。公开协议观察用于互操作研究；本仓库不包含九牧小程序、账号令牌或固件镜像。

## 官方 ZD6450 产品记录：优先核对直接接入

2026-10-27 从华为[产品目录](https://smarthome-drcn.dbankcdn.com/device/guide/ver_202609_hmos/mainHelp.json) 查到 `2QBR`，名称“九牧 i60 智能马桶 ZD6450”，型号 `ZD6450-SA-CJM305`，品类 `123`。同目录还有其他九牧型号，不能把它们的支持范围混同。

- [官方 Profile](https://smarthome-drcn.dbankcdn.com/device/guide/2QBR/2QBR.json)：`protocolType=WiFi`、`uiType=H5`，页面为 [2QBR 控制页面](https://smarthome-drcn.dbankcdn.com/device/guide/2QBR/h5_001/index.html)。这里只核对产品声明，未验证手机端页面实际展示或注册。
- 目录配网类型 `hiLinkBleConfig|ignoreBond`，广播规则 `Hi-JOMOOTOLIE-12QBR.*`。它表示 BLE 辅助配网，不能据此认定马桶是 Lite 网关 BLE 子设备。
- [官方配网说明](https://smarthome-drcn.dbankcdn.com/device/guide/2QBR/help.html)：接通电源并盖上盖板；长按侧面按键 2–5 秒，一声后松手，随后连续三声进入联网模式。

现场当前广播是 `JOMOO_SMART_249734`，通过私有 FFE1 控制。广播不同可能与模式或联网模块有关，尚不能断定是同一联网版本或排除直连。应先在智慧生活手动搜索精确型号，核对完整铭牌和配网表现；不要为研究自动恢复出厂或删除现有绑定。若有对应联网模块，官方直连更符合单卡片和多功能要求，也可减少对 Mac BLE 转发的依赖。

### 单张原生马桶卡片的已知契约

下表来自 Profile，代表官方产品接口，不代表本项目已支持。

| 服务 | 官方字段/含义 | 当前适配状态 |
|---|---|---|
| `offSeatAction` | `action` 3 开盖、4 关盖、5 开圈、6 关圈；仅写 | BLE 翻盖已验证，华为该服务尚未注册测试 |
| `toiletLidStatus` | `status` 4 开盖关圈、5 全开、6 全关；只读 | BLE 盖圈可读，尚无该华为服务适配 |
| `seatStatus` | `status` 0 未着座、1 已着座；只读 | HA 落座可读，未导出为另一张卡片 |
| `toiletSetting` | `autoFoot`、`autoLight`、`autoOpenCover` 等只读；`mute`、`autoDeo` 可读写 | 不能把只读字段伪装为该页面的可控功能 |
| `drySetting` | `gear` 0–3 | BLE 读档位，未开放写入 |
| `seatHeating` | `gear` 0–4、100；`mode` 只读 | 100 的映射不能直接当作 BLE 档位，待核对 |
| `washSetting` | 水温 0–5、水压 0–4 等 | 不假设水压、位置与 BLE 编号相同 |
| `foamShield` | 单次发泡 `action`、落座发泡 `on` | 尚未实现或发送 |

官方 Profile 中还包含清洗、停止、滤芯、故障、升级等服务。未验证功能不能返回虚构成功；原生页面可能显示这些控件，桥接需要明确处理不支持项，并验证页面的实际依赖。

## 华为 Lite、S2 和 Mini 的使用边界

本次核对型号为蓝牙网关 Lite `WS3300-11`、Mini `LXB-L40` 和 S2 `HCP23-S10`。型号可记录为兼容性线索；家庭 IP、MAC、SN、房间布局与截图保留在私有资料中。

- [Lite 协议说明](https://consumer.huawei.com/cn/support/content/zh-cn16099670/)声明 Bluetooth 4.2、定制 Bluetooth Mesh 1.0，支持 BLE 与 BLE Mesh 双模。
- [Lite 功能说明](https://consumer.huawei.com/cn/support/content/zh-cn16099671/)说明子设备列表可查看蓝牙信号，建议三格及以上。网关自身 Wi-Fi RSSI 与子设备 BLE 信号是不同指标。
- [添加子设备](https://consumer.huawei.com/cn/support/content/zh-cn15909625/)路径为网关卡片 → 设备列表 → 加号 → 扫描。该页面的通用重置提示不作为对现场马桶执行重置的授权或型号兼容证据。
- [Mini 产品介绍](https://consumer.huawei.com/cn/wholehome/control-panel-mini/)描述场景、语音和家庭设备控制。未找到上述 Mini/S2 型号可供用户安装本项目 Python 驱动或作为任意 GATT 代理的公开接口。

因此，现有 Lite 不能直接作为已经可用的 HA 蓝牙代理；需要精确设备兼容性或可调用的代理接口证据。S2/Mini 展示并控制已接入设备的能力，应与其承担 BLE 驱动的能力分别验收。当前均未完成本项目实机验收。

## OneConnect 单子设备、多服务备用路线

已审阅 [xiasi0/ha-oneconnect-bridge](https://github.com/xiasi0/ha-oneconnect-bridge) 的固定版本 README、默认映射、服务注册表和贡献文档。它以一个 child 包含多个 services 和 bindings 的 YAML 建模，结构上符合“一台设备一张卡片”。基于 Python 的实现也有望避开现有 C 库的 gcompat 依赖，但尚未安装或现场测试。

固定版本 `95b63b78ae4963e942920e839c5b802697503690` 的上游服务注册表没有 `offSeatAction`、`toiletLidStatus`、`toiletSetting` 等马桶服务。本项目已编写七项服务的受限适配并完成隔离检查，详见[单卡片实验](oneconnect-experiment.md)。仅填写 `prod_id: 2QBR` 不会自动获得对应驱动；页面、云端注册和双向操作仍需要验收。构建器将上游默认的无关汽车测试模型替换为空映射。

其预置产品 `9MU7`、`9MXO`、`9MY5` 分别是灯泡、空调、窗帘，不是通用马桶页面。上游对这些模型来源的描述是项目作者声明，不能当作华为对本项目的授权或认证。

另审阅 [chaosl1996 分支](https://github.com/chaosl1996/ha-oneconnect-bridge) README：GUI 按选中实体生成子设备，默认行为不满足本项目分组要求；应采用已审查的手动单 child 多服务映射，不能直接全选实体导出。

备用实施顺序：确认官方直连结果 → 实现匹配 Profile 的必要服务适配和拒绝不支持命令 → 备份现有桥接 `.storage` 与映射 → 解决 UDP 5683 配对监听冲突 → 验证单台单卡 → 验证原身份恢复与第二台隔离。此时才安排迁移；当前保留已工作的桥接和两张翻盖卡片，没有启动另一个冲突的配对服务。
