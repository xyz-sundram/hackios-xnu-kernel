# HakiOS：Nothing Phone (1)

本分支面向 Nothing Phone (1)（`spacewar`，Snapdragon 778G+ / SM7325）。

已在手机验证：主屏幕和壁纸、单点触控、音量和电源键、七个 XNU 核心、
CPU0 上的 USB 加载器，以及 550 MHz Adreno 642L / Mesa OpenGL ES 图形。
大核和超大核请求约 1900/1862 MHz。安静模式保留调试代码，且没有预设运行时限。

多点触控和长期稳定性尚未验证。音频、Wi-Fi、相机、振动、电量、充电、
蜂窝网络和持久化数据未被证实可用。其他手机的测试结果不代表本机支持。

- [完整构建步骤（英文）](README.md#build-for-nothing-phone-1-spacewar)
- [当前硬件状态](docs/spacewar-status.md)
- [运行和日志](docs/getting-started/phone.md)
- [双系统实现和验证状态](docs/dual-boot.md)

手机固件、DTB 和 iOS 镜像由用户提供，不包含在源代码中。
