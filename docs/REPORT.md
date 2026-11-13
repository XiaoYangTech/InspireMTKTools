# 亿方联发科工具箱 / InspireMTKTools v3.0.0 重制报告

> 老软件：`LogoBuilder v1.6 中文版`（ankadnikov，2011–2015，.NET Framework 4.0 WinForms，闭源分发包）
> 新软件：`亿方联发科工具箱 / InspireMTKTools`（Python 3.10+ / ttkbootstrap / Pillow / capstone，跨平台，MIT）
> 逆向：REA（托管程序元数据 + 调用图）+ dnfile/dncil（IL）+ 自编译 ICSharpCode.Decompiler（C# 还原）+ Crypto Obfuscator 字符串池破解 + 反射 oracle 交叉验证
> 报告日期：2026-11-13

---

## 0. 摘要（TL;DR）

- **完全还原了原软件的私有格式**：`logo.bin`/`logo.img` = 512B MTK 头 + `{count,total,offsets[]}` 块表 + 连续 zlib 流；像素格式 RGB565 / BGRA8888；分辨率靠外部 `sizes.txt`（key = W×H×bpp）。**逆向结论用两份独立实现互相验证**（`docs/spec_original.md` 与 `analysis/format/spec_bin.md`），真实样本 51 块全部解出，**未修改块回包字节级一致**（SHA-256 相同）。
- **破解了原版保护**：识别出 Crypto Obfuscator（LogicNP）保护，解密出 137 条字符串（文件名、`.project` 行格式、CLI 命令、错误文案全部明文），还原 9097 行 C#，并定位了原版 **10 类已知缺陷 + 13 条新缺陷**（含"启动即崩"的 N1）。
- **重制版已可交付**：`python app.py` 跨平台 GUI（全中文单语、统一 ttkbootstrap **litera** 浅色主题、HiDPI 100–200%）、`python -m logobuilder.core.cli` 命令行、PyInstaller 单文件 `dist/InspireMTKTools.exe`；实测解析/回包/GUI 全流程/DPI/冻结 exe 自检全部通过。
- **修复了用户抱怨的全部问题**：分辨率窗口不出现（B1 根因：异常被吞 + 永久写 ignored.txt）、目录选择（B2 自绘 Win32 对话框）、软件内换图（B3）、只认 bin（B4）、只能出卡刷包（B5 增加 logo.img / pad 导出）、不能自动识别分辨率（B6 候选列表 + 置信度 + 相邻块投票）。
- **开源代替品结论**：2026 年仍**没有**带 GUI 的中文 LogoBuilder 替代品；最值得参考的是 YAMLP（MIT，2026-08，现代 logo.img）、mtklogo（Rust，MIT，旧格式 CLI）。我们的差异点 = 中文 GUI + 自动探测 + 新旧格式 + 字节级回归测试语料。

---

## 1. 交付物清单

| 交付物 | 路径 | 说明 |
|---|---|---|
| GUI 程序 | `app.py` → `src/logobuilder/ui/` | ttkbootstrap，跨平台、HiDPI、线程化、日志面板 |
| 核心库 | `src/logobuilder/core/` | 容器/像素/尺寸推断/工程/卡刷包/第二屏/CLI，纯 stdlib+Pillow |
| CLI | `python -m logobuilder.core.cli` | `info/extract(pack)/zip/bootlogo/bl2img/sizes` + 原版命令别名 `unpack/pack/img2bl/bl2img` |
| Windows 单文件 | `dist/LogoBuilder2.exe`（PyInstaller onefile，36.4 MB） | `--self-test` 冻结版自检通过 |
| 测试 | `tests/` | 单元 + 集成 + GUI + 夹具生成器，共 362+ 项 |
| 逆向文档 | `docs/spec_original.md` | 原版格式/工程/卡刷包/缺陷（B1–B10 + N1–N13，含代码行号） |
| 格式交叉验证 | `analysis/format/spec_bin.md` + `parse_logo.py` | 独立实现；51 块清单、逐字节回包证明 |
| 行为分析 | `analysis/original_behavior.md` | 逐方法行为（SizeDialog/MainForm/Program/ImagePreviewControl/Tips），含反编译行号 |
| 原版文案/资源 | `docs/original_ui_strings.md`、`analysis/resources/ui_assets/` | 17 键 Strings 中英表、5 语言 tips、11 张原版图标 PNG |
| 开源调研 | `docs/alternatives.md` | 6 个可核实仓库 + 能力矩阵 + 许可证边界 + 现代 logo.img 格式 |
| QA 报告 | `docs/qa.md`、`analysis/qa/report.md` | 测试矩阵、DPI 探针、构建日志、发布检查单 |
| 逆向证据 | `analysis/rea/evidence_bundle.json`（11 MB，18 条 REA 记录）、`analysis/decompiled/`、`analysis/il/`、`analysis/resources/`、`analysis/tools/` | 可复现 |

---

## 2. 逆向方法与证据链（REA 为主线）

### 2.1 REA 托管程序分析

| 步骤 | REA 操作 | 结果 |
|---|---|---|
| 程序集识别 | `inspect_managed_artifact` | PE32 .NET Framework 4.0 Client Profile，`LogoBuilder 1.6.0.0`，MVID `8b1b385d-…`，623 TypeDef / 2568 MethodDef / 9309 成员，SHA-256 `f6a9a037…2fed` |
| 成员/调用图 | `inspect_managed_members` | 全量类型/字段/方法/签名/3026 call edges/1464 field accesses/资源清单 |
| 类型与签名分页 | `inspect_analysis_view`（managed-members） | 业务类定位：`LogoProject`(35 方法)、`MainForm`(35)、`SizeDialog`、`ImagePreviewControl`、`LogoSize`、`Program`、`SignControl`、`ShellContextMenu`、`Ionic.Utils.FolderBrowserDialogEx` |
| 证据固化 | `export_evidence_bundle` | `analysis/rea/evidence_bundle.json`（18 条记录，含全部上述证据 ID） |

> REA 证据 ID 示例：程序集 `ev_7f9f11a5…52d6`；成员清单 `ev_f8ea4f50…2f79`；类型分页 `ev_308a1c79…5b7d`。

### 2.2 保护识别与字符串解密

- `inspect_managed_artifact` 资源清单发现 2 个 ManifestResource；解包后魔术 `02 ed bd 07` → **DEflate 后得 `ResourceAssembly`（LogicNP Software, 2009）** → 判定为 **Crypto Obfuscator for .NET**。
- 该保护的控制流混淆把 BCL 调用替换为 `A.<控制字符>` 代理类；字符串以"解密器 + 资源池"形式存在。
- **破解过程**（re-analyst）：定位 `A.<class>::<method>(int)` 解密器 → 发现参数是 **字符串池字节偏移**，池记录 = `[变长长度][UTF-16LE]` → 用原解密器反射往返校验，**137/137 条字符串全部恢复**（`analysis/tools/string_usages.tsv`、`analysis/decrypted_strings_clean.txt`，224 处使用点带行号）。
- 由此确证原版真实常量：工程头文件 `header`（无扩展名）、`.project` 行格式 `image:img1.png 540 960 4` / `binary:block1.dat`、卡刷包模板 `LogoBuilder.update.zip`、CLI 命令 `unpack/pack/bl2img/img2bl`、全部错误文案。

### 2.3 C# 还原与行为 oracle

- 本机无 .NET SDK/ILSpy 运行环境；用 **`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`** 编译一个 30 行 host，引用 **ICSharpCode.Decompiler 4.0.1（net46）**，把 `LogoBuilder.exe` 整体反编译为 **193 KB / 9097 行 C#**（`analysis/decompiled/LogoBuilder.cs`）。
- 辅助：`dnfile + dncil` 全量 IL 导出（`analysis/il/`，215 个文件）；`LogoBuilder.annotated.cs` 把解密字符串内联回反编译代码。
- format-analyst 进一步用**反射调用原程序内部方法**做行为 oracle（不启动 GUI）：验证像素字节序（BGRA）、RGB565 简单移位、原版自己的重压缩产物大小（2,007,537 B，`Ionic.Zlib` 46/51 流与样本一致）、`lk.bin` 被拒绝的路径等。**关键结论：样本 logo.bin 的压缩流是标准 C zlib level 9 产生的（51/51 可复现），不是程序自带 Ionic.Zlib。**

### 2.4 样本与证据

`logo.bin`（2,006,471 B, 51 块, tag LOGO）、`lk.bin`（294,644 B, tag LK, raw ARM 载荷）、`sizes.txt`（49 行）、`update.zip` 模板（126,439 B，CWM update-binary + updater-script + JAR 签名）。全部 SHA-256 记录在 `analysis/format/spec_bin.md`。

---

## 3. 原软件解剖摘要

### 3.1 格式（`logo.bin` / `logo.img`）

```
+0x000  header[512]: magic u32=0x58881688 | dataLen u32 | tag[8]="LOGO"/"LK" | 24B 0 + 472B 0xFF
+0x200  TOC: u32 count | u32 total | u32 offsets[count]   (offset 相对 0x200)
+0x200+8+4*count: block[0..count-1] = 连续 zlib 流（78 DA = level 9），块间无对齐/填充
图像块解压后 = W*H*bpp 字节，无任何块内元数据：
  bpp=2: u16 LE RGB565（R=(v>>11)&31, G=(v>>5)&63, B=v&31；<<3/<<2/<<3 扩展）
  bpp=4: BGRA8888 小端（ARGB int 的字节序），top-down、无行 padding
尺寸只能由外部 sizes.txt 反查：key = W*H*bpp（49 条内置 + ignored.txt）
```

- `lk.bin`：同 512B 通用头但 payload 是裸 ARM LK bootloader（8 项 ARM B 向量表 + 代码，0x37C00 附近有 MTK 分区表字符串），无块表、无 zlib → 重建版按 raw 容器无损保留。
- 打包：header[0:4]+[8:512] 原样，只重算 `dataLen@4`；TOC 重算；未修改块复用原始压缩流即可**逐字节**一致，已修改块用 zlib level 9（标准 zlib 可复现原样本字节；Python 3.14 的 zlib-ng 压缩字节不同但解压数据一致）。

### 3.2 工程目录（原版）

```
header            # 512B 原头（无扩展名）
.project          # 每行 image:imgN.png W H BPP 或 binary:blockN.dat
img1.png …        # 图像块
block1.dat …      # 未知尺寸块的解压后数据
（可选）updater-script / update-binary   # 打包 update.zip 时覆盖模板
```
注意：sizes.txt / ignored.txt 写在**程序目录**而非工程目录；"打包/卡刷包"无另存为，固定写工程目录下 `logo.bin`、`update.zip`。

### 3.3 update.zip

模板内嵌于程序（`LogoBuilder.update.zip`，126,439 B）：`META-INF/com/google/android/{update-binary(190,484 B ARM ELF),updater-script}` + JAR 签名。`updater-script` = `package_extract_file("logo.bin", "/dev/logo")`。原版生成流程：复制模板 → 加根目录 `logo.bin` → 可选用工程内脚本覆盖 → **不重签名**。

### 3.4 原版缺陷（用户抱怨 → 代码根因）

| # | 用户抱怨 | 根因（证据） |
|---|---|---|
| B1 | 分辨率窗口有时不出现/卡死 | `SizeDialog.InitSizes` 因数分解失败 → 异常被 `DecompressBlock` 吞掉 → 该尺寸**永久写入 ignored.txt**（取消也永久忽略）；滑块拖动每次全量解码 |
| B2 | 目录选择经常出问题 | 自绘 Win32 `SHBrowseForFolder` + CSIDL 封装（`Ionic.Utils.FolderBrowserDialogEx`），无 writable 校验 |
| B3 | 软件内不能换图 | 只有"打开目录"+FileSystemWatcher，无替换流程 |
| B4 | 只识别 bin | 只按 512 头 + 双 dataLen 判断，不校验 magic；其它形态直接报"数据长度错误" |
| B5 | 不能出单独 LOGO IMG | 只有 logo.bin/update.zip 两种输出，无 fastboot 镜像/分区填充概念 |
| B6 | 不能自动识别分辨率 | 仅靠 sizes.txt 长度表；无候选/投票/置信度 |
| B7 | 32bpp/alpha 误判 | `LooksLike32Bit` 以"每像素最高字节=0xFF"猜测 |
| B8 | 损坏输入易崩 | 无 count 上限、无 offset 单调校验、异常直接弹 `ex.ToString()` |
| B9 | 工程文件/图片易不同步 | 无校验/修复 |
| B10 | 仅 Windows、无 HiDPI | WinForms 固定像素布局 |
| N1 | （新发现，致命） | `LastColorDepthIndex` 越界 → 静态构造 `new SizeDialog()` 抛异常 → **启动即崩且无法自恢复** |
| N2–N13 | （新发现） | 块编号错位、句柄泄漏、滚轮 8400px、header 空引用、zip 反斜杠路径、不重签名、死校验等（见 `analysis/original_behavior.md` §9） |

---

## 4. 重制版架构与功能

```
app.py / python -m logobuilder           # GUI 入口
src/logobuilder/
  core/  errors sizes pixel container project zipflash bootlogo cli
  ui/    main app theme toolbar blocklist preview dialogs widgets workers session settings dnd screenshot self_test
  i18n/  catalog.py（仅中文 zh_CN，单语）
  assets/ sizes.txt、update_template.zip
tests/   test_core/ test_ui/ integration/ fixtures/
dist/LogoBuilder2.exe                    # PyInstaller onefile
```

- **分层**：core 无 GUI 依赖（可 CLI/测试/嵌入）；GUI 只消费 core API；CLI 与 GUI 同一核心。
- **HiDPI**：Windows `SetProcessDpiAwareness(2)` + `tk scaling = fpixels/72` + 字体按 linespace 缩放；100%/150% 实测布局无裁剪（QA 探针：100% 1239×819、150% 1860×1230、字体 19→27px）。
- **跨平台**：tkinter 原生文件对话框、无 Win32 依赖；macOS/Linux 可运行（未在真机验证，见限制）。
- **健壮性**：结构全校验（magic/dataLen/count/offset 单调/越界/OOM 保护），可恢复问题记 `warnings` 并在 GUI 日志/CLI stderr 显示；未修改块复用原始压缩流实现 lossless 回包。
- **主流程**：打开镜像 → 块列表（缩略图/尺寸/bpp/状态）→ 预览（适应窗口/1:1/导出 PNG）→ 替换图片（拉伸/等比/裁剪 + 预览）→ 未知尺寸候选对话框（置信度 + 依据 + 自定义 + 记忆 + 应用到同类块）→ 导出 logo.img（可选填充分区大小/0x00 或 0xFF）→ 打包 logo.bin → 生成 update.zip → 第二屏 boot_logo 生成/读取 → 工程校验/修复。
- **界面**：全中文单语（按 v3 需求移除英文切换与文案）；原版中文误译未沿用（见 `docs/original_ui_strings.md`）。

---

## 5. 与开源代替品对比（`docs/alternatives.md` 摘要）

| 工具 | 语言/许可 | GUI | 自动探测 | 旧格式 | 新 logo.img | 备注 |
|---|---|---|---|---|---|---|
| **LogoBuilder 2.0（本项目）** | Python / MIT | ✅ 中文 | ✅ 候选+置信度 | ✅ | ✅（同容器/填充） | 字节级回归、修复原版缺陷 |
| YAMLP | Python / MIT | ❌ | 部分（bpp/stride 穷举） | ❌ | ✅ | 2026-08，8★，现代 logo.img |
| mtklogo | Rust / MIT | ❌ | 部分（explore/guess） | ✅ | ❌ | 36★，2019 停更，最接近旧格式 |
| mtk-tools | Perl / 无许可证 | ❌ | 部分（强制分辨率） | ✅ | ❌ | 226★，禁止再分发 |
| logo_dumper | Python / Apache-2.0 | ❌ | ❌ | 小米格式 | ❌ | 2022 |
| Mediatek-Logo-Creator | C / 无许可证 | ❌ | ❌ | 🟡 | ❌ | 官方 BSP 抽出 |
| mtkclient | Python / GPL-3.0 | ❌ | ❌ | ❌ | ❌ | 刷写通道，非编辑器 |

结论：**没有现成的"中文 + 图形界面 + 自动识别 + 新旧格式通吃"替代品**；本项目填补该空白，并保留了 CLI 供脚本化。

---

## 6. 验证记录

> 详细矩阵见 `docs/qa.md`，独立验证报告见 `analysis/qa/report.md`。以下为 Lead 最终验收实测。

| 项 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `PYTHONPATH=src python -X utf8 -m pytest tests -q` | **369 passed, 1 skipped, 0 failed**（129.85s；skip 为小屏预期） |
| GUI 自检 | `python -X utf8 app.py --self-test` | **14/14 项通过，exit 0** |
| 冻结 exe | `dist/LogoBuilder2.exe --self-test` | **14/14 项通过，exit 0**（PyInstaller onefile **36,368,438 B**，SHA-256 `18ab317e6f136b583bd2759c9565115adab1a21c7834a777155a4b0609b023b3`，资源 add-data 生效） |
| 真实样本回包 | `cli extract/pack` | 51 块，输出 SHA-256 = 输入 `c321a9f1…655c`，**字节级一致** |
| lk.bin | `cli info/extract` | 识别 raw 模式，回包 SHA-256 `5bc6c099…ce55` **字节级一致** |
| 卡刷包 | `cli zip` | 含 `logo.bin`(同 sha) + `META-INF/.../updater-script`(含 `/dev/logo`) + update-binary(sha 与模板一致) |
| 工程 legacy | `import_legacy`（原版 `header`+`.project`） | 51 块导入成功，解压数据与源镜像完全一致；`export_legacy` 可写回原版兼容工程 |
| bpp2 合成 | 1080×1920×2 RGB565 | 编码→解码像素完全一致 |
| 分区填充 | `to_bytes(pad_to_size=N)` | 输出 N 字节，尾部 0x00 |
| GUI 截图 | `python app.py --screenshot` | 非空白：主窗口(1240×820)/150%/分辨率/替换图片/设置/错误/帮助/LK 面板/预览/校验/刷写说明（`docs/images/` 为对外精选图；全量在本地 `analysis/ui_screenshots/`） |
| HiDPI | QA `dpi_probe.py` | 100%/150% 探针 **28/28 PASS**（paned 撑满、无死区/裁剪；字体 19→27px；`analysis/qa/artifacts/dpi_probe.json`） |
| GUI 无头探针 | QA `gui_probe.py`（真实实例化 App） | 14/14 PASS（含真实 logo.bin 打开、替换、导出、zip、lk raw） |
| 损坏输入 | QA 夹具（截断/坏 magic/offset 乱序/超大 count） | 均抛 typed 中文错误，退出码/消息可读 |
| QA 最终结论 | `analysis/qa/report.md` | **RELEASE-READY**：阻断项 0，非阻断遗留 7 条（见 report §6.1/§7）；独立 oracle logo 19/19 + lk 11/11 PASS；四路 sha 一致 |

### 6.1 最终测试数字

- `tests/test_core`：容器/像素/尺寸/工程/zip/CLI/bootlogo/错误/API 契约，覆盖真实样本与合成夹具（bpp2/bpp4/混合/损坏）。
- `tests/integration`（QA 独立 oracle）：78/78 绿，含字节回包、zip 结构、GUI 全流程、lk raw、pad、损坏输入。
- `tests/test_ui`：GUI 冒烟/对话框/DPI/i18n/成像/导出路径。
- 全量（交付前最终验收）：**447 passed, 1 skipped, 0 failed**（skip = 小屏 compact 模式契约行为；含 core、QA integration、UI/会话/LK 等）
- 连续多轮运行稳定；GUI 截图、自检、冻结 exe 自检均通过。

---

## 7. 使用说明（摘要）

完整见 `README.md`。

```bash
pip install -r requirements.txt
python app.py                          # GUI
python app.py --self-test              # 自检
python -m logobuilder.core.cli info logo.bin
python -m logobuilder.core.cli extract logo.bin proj/
python -m logobuilder.core.cli pack proj/ new_logo.img
python -m logobuilder.core.cli zip new_logo.img update.zip
python -m logobuilder.core.cli bootlogo image.png boot_logo
python -m logobuilder.core.cli bl2img boot_logo boot_logo.png
```

Windows 打包：`python tools/build_exe.py`（PyInstaller onefile → `dist/LogoBuilder2.exe`）。

---

## 8. 限制与风险（诚实声明）

1. **仅在一个真实样本（`logo.bin`，51 块，全 bpp4）上做字节级验证**；bpp2 路径由原程序反射 + 合成数据验证，缺少真实 bpp2 真机样本。
2. **现代变体**（bpp=3、stride 对齐、块前缀、A/B 分区命名、部分厂商自定义头）不在本版自动解码范围：遇到时按 BinaryBlock 原样保留（不破坏），日志标注；`logo.img` 与 `logo.bin` 本版按同一容器处理（已与 YAMLP 源码核实结构一致），但**未在 Android 11+ 真机刷入验证**。
3. **跨平台未在 macOS/Linux 真机运行**：代码避免平台专属依赖（Windows DPI 用 ctypes 有 try/except），但仅 Windows 实测。
4. **卡刷包沿用原版 CWM 模板**（旧 update-binary + 2008 测试签名，加 logo.bin 后不重签名）：Recovery 若强制签名校验会失败；推荐优先用 `logo.img` + fastboot 或 TWRP。
5. **刷机风险**：写错 logo 分区可能导致无法开机；务必先备份原分区。本软件只生成镜像，不直接操作设备。
6. GUI 自动化验证在本机 638×394 RDP 小屏环境完成，真实 2K/4K 屏视觉细节仍建议人工确认。
7. `sizes.txt` 的理论多解性（W×H×bpp 相同）无法仅凭镜像消除，未知尺寸时以候选列表 + 人工确认为准；已选择会被记忆。

---

## 9. 复现与证据

```bash
# 逆向证据
analysis/rea/evidence_bundle.json            # REA 证据包（18 条记录）
analysis/decompiled/LogoBuilder.cs           # 9097 行 C# 还原
analysis/il/                                 # 全量 IL（dncil 导出）
analysis/tools/string_usages.tsv             # 137 条解密字符串 + 224 处使用点
analysis/decompiled/EmbeddedPayload.cs       # Crypto Obfuscator ResourceAssembly 识别
analysis/tools/LogoBuilder.annotated.cs      # 字符串内联注释版反编译

# 格式独立验证
python analysis/format/parse_logo.py "LogoBuilder_jb51/LogoBuilder_v1.6中文版/logo.bin" analysis/format/extracted --table
python analysis/format/parse_logo.py ... --repack out.bin          # 字节级一致
python analysis/format/parse_logo.py ... --repack out2.bin --recompress  # 全部重压缩也一致

# 重制版验收
PYTHONPATH=src python -X utf8 -m pytest tests -q
python -X utf8 app.py --self-test
python -X utf8 app.py --screenshot analysis/ui_screenshots/review.png
python tools/build_exe.py
```


---

# 第二轮迭代（v3.0.0）：更名「亿方联发科工具箱 / InspireMTKTools」+ LK 引导程序修补

> 用户新需求：去除联发科开机 **Orange State / 解锁警告 + 5 秒等待**；去除 **dm-verity 错误警告 + 等待**；产品更名。
> 交付时间：2026-11-13 ｜ 详细规格：[docs/spec_lkpatch.md](spec_lkpatch.md)、调研：[docs/lk_patch_research.md](lk_patch_research.md)

## 10. 调研结论（互联网现有方法）

- 没有公开开源项目覆盖 **AArch64** LK 的去警告；现有工具（[mtk-lk-warning-patch](https://github.com/justistab3-bot/mtk-lk-warning-patch) MIT、[modlk](https://github.com/programmersd21/modlk) MIT、[lkpatcher](https://github.com/R0rt1z2/lkpatcher) GPL-3.0）都只针对 **ARM32 Thumb-2**，使用固定特征码 / 字符串清零。
- 社区已验证的两类方法：
  - **M1 改警告分发函数入口为 `return 0`**：警告文本与 5 秒等待一起消失，跨代际最稳定（Hovatek 教程 + 多个项目采用）。
  - **M2 只清零警告文本**：不消除等待；且现代 LK 的警告文案已变化（旧文案查找会 0 命中）。
- dm-verity 警告是 LK 内另一个函数（打印 `dm-verity corruption` 等并做约 3 秒倒计时），需单独 patch。
- 结论：本项目采用 M1 为主（ARM32 特征码 + AArch64 启发式逆向），M2 作为可选"清空文本"。

## 11. 真实 AArch64 LK 样本逆向（本项目的核心增量）

样本：`lk_a.bin` / `lk_b.bin`（4MB 分区镜像，payload 870,352B，AArch64；来自公开仓库的 Transsion/MTK 设备备份，仅本地分析、不随仓库分发；SHA-256 `bb2f2e6a…f873` / `f4ecd3ee…a6e4`）。

| 目标 | 位置（文件偏移） | 证据 | 补丁 |
|---|---|---|---|
| 开机状态警告分发器（Orange/Red + 5 秒） | `0x4D928` | `stp x29,x30,[sp,#-0x20]!` 入口；读 state 全局 `0xC7DC8`；`cmp w8,#1/#3/#2`；打印 `Orange State`+`OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.` 或 `Red State`+`Device Cannot Boot. Enter Repair Mode.`；`mov w0,#0x4b40; movk w0,#0x4c` = 5,000,000µs；末尾 `return 0` | 入口 8B → `mov w0,wzr; ret`（`e0 03 1f 2a c0 03 5f d6`） |
| dm-verity 警告（约 3 秒倒计时） | `0x4CC58` | 调用 dm-verity 检查；打印 `dm-verity corruption`/`Your device is corrupt.`/…；30×100,000µs 倒计时 | 同上 |

- 检测算法（通用，不写死偏移）：capstone 反汇编 → `ADRP+ADD` 字符串引用扫描（payload 映射 0 时引用目标==字符串偏移）→ 向前找函数入口 → 按"Orange+Red 双引用 + cmp 状态链 + 5,000,000 常量"识别分发器；按"≥2 个 dm-verity 字符串引用"识别 dm-verity 函数；置信度 + 证据展示，识别不到则 fail-safe。
- 状态映射实测：state 2 → Orange 路径；state 1/3 → Red 路径。

## 12. 实现与安全机制

- 新核心模块 `src/logobuilder/core/lkpatch.py`（+`assets/lk_patch_rules.json`）；CLI：`lk-info` / `lk-patch`（`--dry-run`、`--in-place`、`--categories`、`--clear-text`、`--backup-dir`）/ `lk-verify` / `lk-restore`。
- 新 GUI 面板「LK 修补」：文件/文件夹批量、扫描结果表（架构/结论/SHA-256）、findings 证据表、警告文本与内核参数保护列表、勾选项、预览补丁（diff+反汇编）、应用/校验/还原备份、刷写说明（fastboot/SP Flash Tool、A/B 双槽提醒）。
- 安全：自动备份、补丁前后反汇编、差异区间校验、**禁止改动 `androidboot.*`/`verifiedbootstate`/`veritymode`/`atm`**（前后计数比较，变化则拒绝）、fail-safe（无候选不写文件）、A/B 双槽提示。
- 品牌：对外统一「亿方联发科工具箱 / InspireMTKTools v3.0.0」；内部 Python 包名保持 `logobuilder` 以兼容既有 API/测试；exe 更名为 `dist/InspireMTKTools.exe`。

## 12.1 界面主题（v3.0.0 主题微调：浅色 litera）

- 界面全面改为 ttkbootstrap 的 Bootswatch **litera**（primary `#4582ec`、bg `#ffffff`、fg `#343a40`、border `#dee2e6`），**单一浅色主题**（移除深色模式与 Ctrl+T）；顶栏/卡片/表格/对话框/日志等自绘区域全部按 litera 重绘。
- 界面语言改为**仅中文**（移除 English 切换、语言设置与英文文案；品牌名 InspireMTKTools 保留）。
- ttkbootstrap 2.x 把 litera 等 1.x 主题收进 legacy 兼容层：`theme.py` 在 root 创建前调用 `install_legacy_themes()`（延迟注册），并在 `ThemeManager` 内兜底立即注册 + 名称回退（`litera → bootstrap-light → cosmo → flatly`），旧版 ttkbootstrap（自带 litera）同样可用。
- 自定义 tk 调色板（顶栏/卡片/块列表/日志/标签）对齐 litera 色值；截图 `analysis/ui_screenshots/main_window.png`、`lk_patch_scan.png` 等 12 张已重生成。

## 13. 验证记录（第二轮，Lead 最终验收实测）

| 项 | 命令/方法 | 结果 |
|---|---|---|
| 全量测试 | `PYTHONPATH=src python -X utf8 -m pytest tests -q` | **447 passed, 1 skipped, 0 failed**（最终 litera 中文界面版；skip 为小屏 compact 预期） |
| LK 核心单测 | `pytest tests/test_core/test_lkpatch.py -q` | **32 passed**（合成 ARM32/AArch64、真实 lk_a/lk_b、负样本、guard 保护、自定义规则、备份还原、CLI、capstone 缺失回退） |
| LK 集成 E2E | `pytest tests/integration/test_lkpatch_e2e.py -q` | **32 passed**（真实样本 2 findings、补丁字节、header/guard 不变、原始 sha 不变、verify PASS、备份/还原、fail-safe） |
| GUI 测试 | `pytest tests/test_ui -q` | **63 passed, 1 skipped**（含 LK 面板 10 用例） |
| GUI 自检 | `python -X utf8 app.py --self-test` | **15/15 通过，exit 0**（含 LK 扫描→dry-run→应用→校验→还原） |
| 冻结 exe | `dist/InspireMTKTools.exe --self-test` | **15/15 通过，exit 0**；37,740,792 B，SHA-256 `0f1a55391ae383fdc9fd247769cba07fa31fd4f6e405bcbd54c97ae7c05fd56e`（最终 litera 中文界面版） |
| 真实 AArch64 样本 | `lk-info/lk-patch/lk-verify/lk-restore`（lk_a/lk_b） | verdict=supported-aarch64、finding 2 个（0x4CC58 dm_verity 0.95、0x4D928 orange_state 0.98），补丁 `e0031f2ac0035fd6` → `mov w0,wzr; ret`，patch_ok=True，原文件 SHA 不变 |
| ARM32 特征码 | 合成 fixture | 7 条内置规则全部命中，补丁 `00207047`，header/guard 不变 |
| Logo 回归 | 真实 logo.bin extract→pack | 输出 SHA-256 `c321a9f1…655c` **与原文件字节级一致**（功能未回归） |
| GUI 截图 | `--screenshot`（12 张） | 含 LK 面板/预览/校验/刷写说明 4 张，均非空白；品牌「亿方联发科工具箱 InspireMTKTools v3.0.0」正确 |
| 独立 RE 校验 | `python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin` | RESULT: PASS（2 findings，与 core 检测偏移一致；含安全性检查：无分支/数据引用指向被改 8 字节、guard 不变、推断链接基址 0x50F00000） |

**QA 最终发布结论（task-9）**：**RELEASE-READY — 阻断项 0 / 非阻断遗留 6 条**。3 个阻塞级 bug（capstone SKIPDATA 崩溃、真实样本 already-patched 误报、i18n SyntaxError）均修复并回归；证据见 `analysis/qa/report_lk.md`、`analysis/qa/artifacts/`。非阻断遗留：AArch64 启发式 fail-safe、未真机实刷、capstone 依赖（缺失优雅降级）、A/B 需双槽、dm-verity 等待按 30×100ms 实测、第三方样本不分发。

**独立交叉验证**：`analysis/lk/lk_a64_analysis.md`（40KB）+ `analysis/lk/rules_lk.json`（15 条规则：13 ARM32 签名变体 + 2 AArch64 启发式）+ `analysis/lk/validate_detector.py` 由独立 RE 队友产出，与 core 实现相互印证；`docs/lk_patch_research.md`（39KB）为方法调研与来源引用。
