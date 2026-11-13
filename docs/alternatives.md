# LogoBuilder 开源代替品调研

> 调研日期：2026-11-13 ｜ 调研员：oss-researcher
> 方法：中英文混合检索 + GitHub REST API 读元数据 + 直接抓取一手 README / 源码。
> **星数与日期均为 2026-11-13 通过 GitHub API 读取的值**，会随时间变化，引用时请注明快照日期。
> 标记约定：✅ 已验证（一手 README/源码/API）｜🟡 推断｜❓ 未验证（勿当结论引用）

## TL;DR

- 现存开源工具里**没有** LogoBuilder v1.6 的"现代 GUI 替代品"：全部是 CLI/脚本，且多数已停更 5–8 年（2018–2019）。
- 最新、最值得读的是 **YAMLP**（2026-08 新建，MIT，Python），它逆向并写清了 Android 11+ 的 **`logo.img` 容器格式**（0x200 头 + 块表 + zlib 块）——这正是老 LogoBuilder 没覆盖的部分。
- 老格式（无头、靠字节长度切分、必须猜分辨率与色彩模式）做得最系统的是 **mtklogo**（Rust，MIT/Apache-2.0，36★，2019 停更）：`unpack`/`repack`/`explore`/`guess` + profile 配置。
- 事实上的"格式百科"是 **bgcngm/mtk-tools**（Perl，226★，2018 停更），但**许可证禁止再分发**，只能读思路、不能抄代码。
- 我们的差异化空间很明确：**Windows 中文 GUI + 自动格式探测 + 新旧格式双支持 + 可复现测试语料**——上述开源项目一个都没有。

---

## 一、开源代替品一览（按借鉴价值排序）

| 名称 | 链接 | 语言 | 许可证 | 最后更新 | 能力 | 与本项目对比 |
|---|---|---|---|---|---|---|
| **YetAnotherMediaTekLogoPatcher (YAMLP)** | [wiretoscreen/YetAnotherMediaTekLogoPatcher](https://github.com/wiretoscreen/YetAnotherMediaTekLogoPatcher) | Python | MIT ✅ | 最后推送 2026-08-05（8★，2026-08 新建）✅ | 新式 MTK `logo.img`：`extract`/`pack`，解析 0x200 头 + 块表 + zlib 块，按原文件大小零填充，A/B 双槽刷写说明 ✅ | 格式能力与我们互补（现代容器），但无 GUI、无自动探测，必须手动传 `-r WxH`；仅作者一台 Moto G05 实测 |
| **mtklogo** | [arlept/mtklogo](https://github.com/arlept/mtklogo) | Rust | MIT 或 Apache-2.0 双许可 ✅（API 识别为 MIT） | 最后推送 2019-02-20（36★）✅，作者自述不积极维护 | 旧式无头 MTK logo 镜像：`unpack`/`repack`/`explore`/`guess`；16bpp RGB 为主，兼容 32bpp RGBA/BGRA 大小端；zlib 压缩 slot 以 `.z` 原样保留；YAML profile 描述"分辨率×色彩模式" ✅ | 旧格式覆盖最接近老工具，架构（CLI + 库 + profile）最值得抄；但需用户猜分辨率、无 GUI、2019 年后停更 |
| **mtk-tools** | [bgcngm/mtk-tools](https://github.com/bgcngm/mtk-tools) | Perl | ❌ 无开源许可证（作者保留版权，**明令禁止再分发**）✅ | 最后推送 2018-08-15（226★，历史影响力最大）✅ | `unpack-MTK.pl` / `repack-MTK.pl`：boot/recovery/**logo**.bin 解包重打包；logo 支持 zlib、`-force_logo_res`、`-invert_logo_res`、`--no_compression`；覆盖 MT6516~MT6595 / MT83xx ✅ | 格式历史事实的最佳佐证（老 LogoBuilder 同代工具），但 Perl、停更、**代码不可复用** |
| **logo_dumper** | [affggh/logo_dumper](https://github.com/affggh/logo_dumper) | Python | Apache-2.0 ✅ | 最后推送 2022-10-08（10★）✅ | 小米 `logo.img` 的 unpack/repack 位图（仓库自述仅 K30 5G 测过）✅ | 跨厂商参考（非 MTK 专用），可看其位图切分方式 |
| **Mediatek-Logo-Creator** | [Iscle/Mediatek-Logo-Creator](https://github.com/Iscle/Mediatek-Logo-Creator) | C（GitHub 语言标注 Makefile）✅ | ❌ 无许可证（默认版权保留）✅ | 最后推送 2019-08-24（10★）✅ | 从 MTK 官方 BSP 抽出、改为 standalone 的 logo 生成器（**README 抓取失败，能力依据仓库描述**🟡） | 可参考"官方如何生成 logo 数据"（调色板/压缩策略），但代码不可复用 |
| **mtkclient** | [bkerler/mtkclient](https://github.com/bkerler/mtkclient) | Python | GPL-3.0 ✅ | 最后推送 2026-10-26（1494★）✅ | MTK 刷写/修复/分区读写工具链 ✅；**未找到其 logo 解析模块的一手证据**❓ | 不是 logo 编辑器；但可作为"备份/刷入 logo 分区"的通道（🟡推断）。GPL 有传染性，注意与本项目许可证边界 |

补充说明：

- 上述 6 个是本次检索中**能落到具体仓库并核到元数据**的全部项目。没有发现任何一个提供 GUI 或中文界面。
- 老 LogoBuilder v1.6 本身是 **.NET Windows GUI**（本地反编译产物 `analysis/il/il/LogoBuilder.ImagePreviewControl..ctor.68.il` 表明存在图像预览控件 ✅），闭源、仅网盘/论坛分发（工作区里的 `LogoBuilder_jb51.rar` ✅）。所以"开源代替品"这条赛道上，我们的项目是**新一代重写**，而非又一个脚本。

### 1.1 能力矩阵

| 工具 | 旧式无头 `logo.bin` | 新式 `logo.img` | GUI | 自动探测 | 代码可否复用 |
|---|---|---|---|---|---|
| YAMLP | ❌ | ✅ | ❌ | 部分（bpp/stride/prefix 穷举）✅ | ✅ MIT |
| mtklogo | ✅ | ❌ | ❌ | 部分（`explore`/`guess`）✅ | ✅ MIT/Apache |
| mtk-tools | ✅ | ❌ | ❌ | 部分（强制分辨率）✅ | ❌ 禁止再分发 |
| logo_dumper | ❌（小米格式） | ❌（小米格式） | ❌ | ❓ | ✅ Apache-2.0 |
| Mediatek-Logo-Creator | 🟡 旧世代 | ❌ | ❌ | ❓ | ❌ 无许可证 |
| mtkclient | ❌ | ❌ | ❌ | ❌ | ⚠️ GPL-3.0 传染 |

---

## 二、我们能借鉴什么

### 2.1 格式建模：容器层与像素层分离

- YAMLP 把 `logo.img` 拆成 **`header.bin`（0x200 字节，原样保留）+ `blocks/*.bin`（zlib 压缩块）+ `blocks/*.raw`（解压后原始像素）**，并用 `manifest.json` 记录每个块的 `fmt`（每像素字节数）、`stride`、`prefix`。pack 时按 manifest 逐块重建，**解压失败的块原样写回**（源码里 `except zlib.error` 分支直接保留原 block）✅。
- mtklogo 则用 `color_model`（编码方式）+ 尺寸 profile 描述像素层，容器层只认"每段的字节长度"✅。
- **建议**：我们的核心库也分两层 API——`Container`（header/offset table/unparsed payload）与 `PixelCodec`（RGB565 / RGB888 / RGBA8888 / BGRA，大小端，stride 对齐，块前缀）。GUI 只消费这两层，不直接碰字节。

### 2.2 自动探测：把"猜"变成可解释的候选列表

- YAMLP 的 `detect_layout()` 穷举 `bytes_per_pixel ∈ {2,3,4}` × `stride`（行字节按 4/8/16/32/64/128/256 对齐）× `prefix ∈ {0,4,8,12,16,32,64,128,256,512}`，用 `raw_length == prefix + stride*height` 命中✅。逻辑不到 30 行，MIT 许可，可直接改写为 Python（保留版权声明即可）。
- mtklogo 的 `guess` 用字节数因式分解给出"可能的 宽×高 组合"，`explore` 则把同一段数据按所有编码导出 PNG 让用户肉眼选✅。
- **建议**：GUI 里做"探测结果置信度列表"——命中多个布局时全部列出 + 缩略图预览，让用户点选，而不是静默取第一个。

### 2.3 尺寸安全：重打包后的硬校验

- YAMLP：新文件**小于**原始大小 → 零填充到原大小（保持分区布局）；**大于**原始大小 → 打印红字危险警告，提示可能变砖✅。
- mtklogo README 明确提醒"logo 分区通常 8MB，重打包文件必须小于分区"✅。
- **建议**：这三条都进我们 `pack` 的前置/后置校验，并写入 GUI 的"导出前检查清单"：`输出大小 ≤ 原文件/分区大小`、`header 保留`、`块数一致`、`A/B 双槽提醒`。

### 2.4 CLI + 库 + GUI 三层架构

- 两个主力项目都是"薄 CLI + 可复用库"：mtklogo 自述 "a Command Line Interface with a thin reusable library"✅；YAMLP 的 `main.py` 只做参数解析，核心在 `utils/binary.py`、`utils/images.py`✅。
- **建议**：重建设置 `core/`（纯解析/打包，无 UI 依赖）→ `cli.py` → `gui/`（ttkbootstrap）。这样测试只测 core，CLI 可脚本化，GUI 换皮不影响正确性。命令命名直接沿用 `unpack`/`repack`、`extract`/`pack` 这类已被用户熟悉的动词。

### 2.5 命名约定与清单文件

- mtklogo 的文件名自带语义：`logo_000_bgrabe.png` / `logo_000_bgrabe.z`（`%03d` 槽位 + 编码；`.z` 表示未解码、原样保留）✅。
- YAMLP 用 `manifest.json` 承载同样的信息✅。
- **建议**：GUI 内部数据模型照搬 `(slot_index, encoding, compressed, source_bytes)` 四元组；导出目录同时写 `manifest.json`，方便二次编辑与 diff。

### 2.6 测试与语料（开源项目最缺的一环）

- 6 个项目**都没有公开测试语料**（YAMLP 的 README 只给一台机型实例 ✅，mtk-tools/mtklogo 靠用户报 bug）。这是我们可以建立优势的地方。
- **建议**：收集各代 `logo.bin` / `logo.img` 样本（注意隐私与版权，只存最小必要字节并脱敏），做 `pytest` 参数化 **round-trip 字节级一致**测试；对无法解析的段验证"原样保留"；对探测函数做表驱动单测。

### 2.7 许可证边界（重要）

| 项目 | 能做什么 | 不能做什么 |
|---|---|---|
| mtklogo（MIT/Apache-2.0）✅ | 借鉴代码，保留版权与许可声明 | 去掉声明后闭源分发 |
| YAMLP（MIT）✅ | 借鉴代码/算法 | 去掉声明 |
| logo_dumper（Apache-2.0）✅ | 借鉴代码，保留 NOTICE | 商标暗示背书 |
| mtk-tools（无许可证）✅ | 只读思路、看格式事实 | **复制/分发任何代码或衍生品** |
| Mediatek-Logo-Creator（无许可证）✅ | 只读思路 | 复制代码（默认版权保留） |
| mtkclient（GPL-3.0）✅ | 独立进程调用 | 链接进闭源程序 |

### 2.8 产品定位：他们都没有 GUI

- 全部 6 个项目都是命令行 / 脚本，无中文、无 Windows 桌面体验；老 LogoBuilder 的用户群（刷机爱好者、维修店）恰恰要的是"点几下、拖张图、看到预览"。
- **建议**：把"**中文 + Windows 单文件/免安装 + 图像预览 + 一键备份提醒**"作为核心卖点；文档里明确对标"LogoBuilder v1.6 重制"，并引用本文件里的开源工具作为格式参考来源。

---

## 三、现代 Android logo 格式变化（本项目的 `logo.img` 支持）

### 3.1 分区与刷写流程变了（Android 11+）

- 新机上 logo 是**独立分区**，MTK 机型常见 **A/B 双份**：`logo_a` / `logo_b`；备份用 `dd if=/dev/block/by-name/logo_a`，刷入用 `fastboot flash logo_a` + `fastboot flash logo_b`✅（YAMLP README 的完整操作步骤）。
- 趋势：`fastboot` 取代"root 后直接写 `/dev/block/...`"的老办法；老工具（含 LogoBuilder v1.6）那种"写分区"思路需要改成"生成镜像文件 + 交给 fastboot/SP Flash Tool"。
- 🟡 推断：AOSP 本身不定义统一的 "logo 分区"规范，第一屏由 bootloader/厂商决定；`/system/media/bootanimation.zip`（开机动画）与 logo 分区是**两件事**。本次未找到 AOSP 官方文档佐证，标为推断。

### 3.2 容器格式变了：从"裸数据"到"头 + 块表 + 压缩块"

**旧式（mtklogo / mtk-tools 时代）** ✅：
- 镜像里**没有任何图像元数据**——mtklogo README 原话："The MTK logo binary image does not contain any information about the images themselves: you don't know their dimensions, or their image format. The only information one surely knows is their length in bytes."
- 所以老工具必须让用户提供分辨率/编码，或暴力穷举；各槽位数据可能是 zlib 压缩也可能裸存。

**新式 `logo.img`（YAMLP 逆向，Motorola/MTK 2025–2026 机型）** ✅，结构如下（依据其 `utils/binary.py` 与 `main.py` 源码）：

```
偏移 0x000: header[0x200]            # 512 字节头，工具不解析、原样保留
偏移 0x200: payload:
    u32 block_count                  # 块数
    u32 payload_end                  # payload 总长（写回时更新）
    u32 offset[block_count]          # 每块在 payload 内的偏移（从 0x208 起）
    block[...]                       # 每块 = zlib 压缩的像素数据
解压后单块 raw 布局:
    [prefix(0..512B)] + stride * height 字节
    stride = width * bytes_per_pixel，且按 4/8/16/.../256 对齐
    bytes_per_pixel ∈ {2 (RGB565), 3 (RGB888), 4 (RGBA/BGRA)}
```

- 重建规则✅：header 原样写回、`header[4]` 更新为 payload 长度、块表重算、每个改动块 `zlib.compress(raw, 9)`、最后**零填充到原始文件大小**。
- 这与我们手头的"老 logo.bin"是**两代不同格式**，不能用一个解析器硬吃；建议做成两个 codec（`LegacyLogoBinCodec` / `LogoImgCodec`）+ 探测器自动分流。

### 3.3 像素布局的多样性

- 已证实存在：16bpp RGB565（mtklogo 默认假设）、24bpp、32bpp RGBA/BGRA（大小端各有变体），以及行对齐 stride、块前缀 prefix✅。
- 意味着 GUI 必须暴露"分辨率 + 色彩模式 + 对齐/前缀"高级选项，同时给出自动探测默认值。

### 3.4 关于 `0x58881688` 魔数与 `LOGO`/`LK` 头：❓ 未验证

- 任务里提到的 "LOGO header 0x58881688 / LK header" 是社区常见说法，但**本次检索没有拿到一手源码证据**（grep.app 被反爬拦截、`bgcngm/mtk-tools` 的 `unpack-MTK.pl` 两次抓取失败、中文博客正文被登录墙/反爬挡住）。**请勿直接当事实写进实现或对外文档。**
- 已验证的反证/约束：mtklogo 明确说老镜像"无任何图像元数据"✅——说明**不同 SoC 代次的容器头并不统一**，不能假设所有 `logo.bin` 共用一个 magic。
- **建议的验证路径**（低成本、可执行）：
  1. 收集 3–5 个不同年代的 `logo.bin` / `logo.img` 样本，`xxd | head` 看前 0x200 字节；
  2. 在 vendor LK 源码里搜 `logo` / `mt_logo` / `0x58881688`（MTK LK 未在 GitHub 官方组织公开 ❓；上游开源 LK 为 [littlekernel/lk](https://github.com/littlekernel/lk)，MTK 分支未验证）；
  3. 用"魔数表 + 长度自洽校验"做多候选探测，而不是写死单一 magic；
  4. 把结论以"已验证/未验证"标注回填本文档。

### 3.5 对重建设计的兼容 checklist

- [ ] 格式探测器：先判 0x200 容器头（块表自洽）→ 再判旧式无头（长度切分 + 探测布局）→ 都不中则报"未知格式"并保留原字节。
- [ ] 旧格式保持 LogoBuilder v1.6 的默认行为（向后兼容），`logo.img` 作为**新增实验性格式**，UI 上有清晰标识。
- [ ] 导出前：大小校验（≤ 原文件/分区）、A/B 双槽提示、原始文件自动备份、危险操作二次确认。
- [ ] 未识别的槽位/块**原样保留**（YAMLP 的做法✅），保证 round-trip 不破坏设备可启动性。
- [ ] 中文错误信息 + 可复制的诊断日志（老工具最被吐槽的就是"报错看不懂"）。

---

## 四、Python GUI 借鉴（简短，工程建议而非检索结论）

本次把检索预算集中在格式与仓库证据上，**未专门检索 Python GUI 项目**；以下是基于本项目技术栈的直接建议（🟡 工程推断）：

- `ttkbootstrap` 负责壳，`Pillow` 负责解码/编码与缩略图；原版 LogoBuilder 有 `ImagePreviewControl`✅，说明"预览"是核心体验，必须保留。
- 大图/多槽位解码放后台线程 + 进度条，避免 Tk 主线程卡死；用 `after()` 回传结果。
- 预览按真实设备分辨率缩放显示，并提供"1:1 像素视图"检查颜色是否正确（RGB565 掉色是高频用户困惑点）。
- 拖拽载入（`tkinterdnd2`）+ 最近文件 + 槽位列表（左侧槽位/右侧预览）是老工具最顺手的交互，不必发明新交互。
- 导出前展示"原图 vs 重打包图"对比与文件大小差值，把 2.3 的安全校验做成可视化清单。

---

## 五、待核实线索（不要当结论引用）

| 线索 | 状态 | 说明 |
|---|---|---|
| `0x58881688` / `LOGO` / `LK` 头 | ❓ 未验证 | 本次未取得一手源码；见 3.4 验证路径 |
| MIO-KITCHEN-SOURCE（ColdWindScholar） | ❓ 未验证 | 第三方评测页称其为 Python 通用 ROM 工具、AGPL；是否含 logo 模块未验证，仅列线索 [评测页](https://hysenlabs.com/projects/coldwindscholar-mio-kitchen-source) |
| `MTKLogoBuilder` / `AK_MTK_LOGO` / `MTK_Logo_Changer` / `mtk-logo-extractor` | ❓ 未找到可靠公开仓库 | 可能为闭源/网盘分发，**不要**在文档里写成"开源项目" |
| MTK 官方 LK 源码 | ❓ 未验证 | GitHub 上未找到 MTK 官方组织仓库；上游 [littlekernel/lk](https://github.com/littlekernel/lk) 存在 |
| 中文博客格式分析（掘金/阿里云） | ❓ 正文未获取 | 见参考链接，仅作线索 |
| SSTIC 2024 论文（When Samsung meets MediaTek） | ❓ 未读 | 涉及 MTK bootloader 安全研究，可能含参考资料 |

---

## 参考链接

**一手仓库 / 源码（本次实际抓取）**

- [arlept/mtklogo](https://github.com/arlept/mtklogo) ｜ [README（raw）](https://raw.githubusercontent.com/arlept/mtklogo/master/README.md) ｜ [GitHub API 元数据](https://api.github.com/repos/arlept/mtklogo)
- [wiretoscreen/YetAnotherMediaTekLogoPatcher](https://github.com/wiretoscreen/YetAnotherMediaTekLogoPatcher) ｜ [main.py（raw）](https://raw.githubusercontent.com/wiretoscreen/YetAnotherMediaTekLogoPatcher/main/main.py) ｜ [utils/binary.py（raw）](https://raw.githubusercontent.com/wiretoscreen/YetAnotherMediaTekLogoPatcher/main/utils/binary.py) ｜ [README（raw）](https://raw.githubusercontent.com/wiretoscreen/YetAnotherMediaTekLogoPatcher/main/README.md)
- [bgcngm/mtk-tools](https://github.com/bgcngm/mtk-tools) ｜ [README（raw）](https://raw.githubusercontent.com/bgcngm/mtk-tools/master/README.md) ｜ [GitHub API 元数据](https://api.github.com/repos/bgcngm/mtk-tools)
- [Iscle/Mediatek-Logo-Creator](https://github.com/Iscle/Mediatek-Logo-Creator) ｜ [GitHub API 元数据](https://api.github.com/repos/Iscle/Mediatek-Logo-Creator) ｜ README 抓取失败 ❓
- [affggh/logo_dumper](https://github.com/affggh/logo_dumper) ｜ [README（raw，python 分支）](https://raw.githubusercontent.com/affggh/logo_dumper/python/README.md) ｜ [GitHub API 元数据](https://api.github.com/repos/affggh/logo_dumper)
- [bkerler/mtkclient](https://github.com/bkerler/mtkclient) ｜ [GitHub API 元数据](https://api.github.com/repos/bkerler/mtkclient)

**格式与生态线索（未验证 / 仅参考）**

- [Little Kernel 上游项目（littlekernel/lk）](https://github.com/littlekernel/lk) — MTK LK 分支未验证
- [MTK Logo 逆向解析之 bin 转 rawx（掘金，正文未抓取）](https://juejin.cn/post/7340163679847170084)
- [MTK Logo 逆向解析之 raw 转 bmp（掘金，正文未抓取）](https://juejin.cn/post/7341192656994435083)
- [MTK 一个分支兼容多客户开机 Logo 方案（阿里云社区，正文未抓取）](https://developer.aliyun.com/article/1197495)
- [MIO-KITCHEN-SOURCE 第三方评测页](https://hysenlabs.com/projects/coldwindscholar-mio-kitchen-source)
- [SSTIC 2024: When Samsung meets MediaTek（PDF，未读）](https://www.sstic.org/media/SSTIC2024/SSTIC-actes/when_vendor1_meets_vendor2_the_story_of_a_small_bu/SSTIC2024-Article-when_vendor1_meets_vendor2_the_story_of_a_small_bug_chain-rossi-bellom_neveu.pdf)

**本地证据**

- `analysis/il/il/LogoBuilder.ImagePreviewControl..ctor.68.il`（原版为 .NET GUI，含图像预览控件）
- `LogoBuilder_jb51.rar` / `LogoBuilder_jb51/`（原版分发包）
