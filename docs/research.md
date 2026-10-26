# 调研来源与兼容边界

核查日期：2026-10-26。固定版本记录见 `research/repositories.json`。

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
