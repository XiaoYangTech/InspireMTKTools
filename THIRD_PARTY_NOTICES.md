# 第三方组件与参考资料声明 (Third-Party Notices)

本项目（亿方联发科工具箱 / InspireMTKTools）自有代码以 **MIT** 许可证发布（见 `LICENSE`）。
以下第三方组件/资料按各自许可证使用，**不在本项目 MIT 许可覆盖范围内**。

## 运行时依赖

| 组件 | 许可证 | 用途 |
|---|---|---|
| [ttkbootstrap](https://github.com/israel-dryer/ttkbootstrap) | MIT | GUI 主题/控件（浅色 litera 主题经其 legacy 兼容层注册） |
| [Pillow](https://python-pillow.org/) | MIT-CMU (HPND) | 图像编解码/预览/截图 |
| [capstone](https://www.capstone-engine.org/) | BSD-3-Clause | LK（AArch64）反汇编与补丁点识别（可选：缺失时 ARM32 功能仍可用） |
| Python / tkinter / Tcl-Tk | PSF / Tcl-Tk license | 运行时与 GUI 基础库 |
| [PyInstaller](https://pyinstaller.org/)（仅构建 exe 时） | GPL-2.0-or-later + 特殊例外 | 打包 Windows 单文件 exe（其 bootloader 例外允许打包专有程序） |

## 数据/模板文件

| 文件 | 来源与许可 | 说明 |
|---|---|---|
| `src/logobuilder/assets/update_template.zip` | Android `update-binary` 来自 AOSP / ClockworkMod Recovery（Apache-2.0 / BSD 系）；JAR 签名文件来自原 LogoBuilder 分发包 | 仅用于生成 CWM/TWRP 兼容卡刷包；刷写前请自行确认目标 Recovery 的签名策略 |
| `src/logobuilder/assets/sizes.txt` | 原 LogoBuilder v1.6 分发包中的分辨率表 | 事实性配置数据，用于解析 MTK logo 镜像 |
| `src/logobuilder/assets/lk_patch_rules.json` | 本项目整理；ARM32 特征码为社区公开事实（见下） | LK 警告补丁规则 |

## 逆向研究与参考（仅事实/方法参考，未复制代码）

- [Hovatek — Remove orange / red state warning on MTK](https://www.hovatek.com/forum/thread-31664.html)：ARM32 LK 警告分发函数特征码与补丁方法（社区教程）。
- [programmersd21/modlk](https://github.com/programmersd21/modlk)（MIT）：特征码与"清零警告文本"思路。
- [justistab3-bot/mtk-lk-warning-patch](https://github.com/justistab3-bot/mtk-lk-warning-patch)（MIT）：ARM32 补丁 A/B 方法、校验与 fail-safe 思路。
- [R0rt1z2/lkpatcher](https://github.com/R0rt1z2/lkpatcher)（GPL-3.0）：仅参考其 `patches.json` 中的**特征码事实**与分类命名；本项目为独立实现，**未复制其代码**（GPL 代码未进入本仓库）。
- [tfastdigital/tfast-mtk-bootloader-tool](https://github.com/tfastdigital/tfast-mtk-bootloader-tool)（专有许可）：仅用于获取真实 MTK LK 样本做本地静态分析；**样本不随本仓库分发**，其 sha256 与分析结论记录在 `analysis/lk/` 文档中。
- 原 `LogoBuilder v1.6`（ankadnikov，2011–2015）：本项目通过 REA 对其做格式与行为逆向研究，**未使用其代码**；反编译/IL/提取资源等受版权内容不随仓库分发。

## 其它

- 本项目不包含任何厂商固件、分区镜像或设备密钥；用户需自行从合法持有的设备/固件中获取待处理镜像。
- 修改 bootloader（LK）有变砖风险，详见 README 与 `docs/lk_patch_research.md` 的风险提示。
