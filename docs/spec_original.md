# LogoBuilder v1.6 原版逆向分析报告（REA + ILSpy 静态还原）

> 目标样本：`LogoBuilder_jb51/LogoBuilder_v1.6中文版/LogoBuilder.exe`
> SHA-256：`f6a9a037c89c0a80e73f99cc7617773c993b0ffc71609fcab75503111c512fed`
> 程序集：`LogoBuilder, Version=1.6.0.0`，.NET Framework 4.0 Client Profile，WinForms，x86 (AnyCPU)
> 保护：**Crypto Obfuscator for .NET（LogicNP）**——控制流混淆 + 字符串加密（明文串已从 `.resources` 提取，格式化串由上下文推断）
> 方法：REA `inspect_managed_artifact` / `inspect_managed_members` 取得元数据全量（623 TypeDef、2568 MethodDef、3026 call edges），dnfile/dncil 导出全部 IL，再用自编 host 调用 `ICSharpCode.Decompiler 4.0.1`（net46，csc 编译）还原 C#；8 个程序内嵌资源全部解出。

---

## 1. 核心发现：文件格式就是 MTK LOGO 分区镜像

`logo.bin` / `lk.bin` 均为 MediaTek LOGO 分区镜像（`/dev/logo`、`logo` 分区），由 **512 字节固定头 + 偏移表 + zlib 压缩的图像块** 组成。

### 1.1 文件布局

```
┌────────────────────────────────────────────── offset 0
│ Header (512 bytes, 原样保留/回写)
│   +0x00 u32 magic      = 0x58881688 (LE bytes 88 16 88 58)
│   +0x04 u32 dataLen    = 0x200 之后的负载总长
│   +0x08 char[8] tag    = "LOGO\0\0\0\0"   或 "LK\0\0\0\0\0\0"（第二屏）
│   +0x10 .. 0x1FF       = 0x00 + 0xFF 填充（原样保留）
├────────────────────────────────────────────── offset 0x200 (512)
│ u32 blockCount
│ u32 dataLen          （与 +0x04 相同，用于二次校验）
│ u32 offsets[blockCount]   （相对 0x200 的偏移；offsets[0]=8+4*count）
│ block[0] ... block[count-1]   （每块 = zlib(CompressionLevel.Best) 数据）
└────────────────────────────────────────────── offset 0x200 + dataLen
```

- **负载长度**：`dataLen = 8 + 4*count + Σ len(block[i])`
- **validity**：`dataLen <= filesize - 512`（多出的尾部字节被忽略，不报错）
- **块数据**：每块是 **zlib 容器**（首字节 0x78，实测 0x78DA = level 9/BestCompression），解压后为**未压缩像素数组**，长度 = `width * height * bpp`。
- **offsets 相对 0x200**：第 i 块数据位于 `0x200 + offsets[i]`，长度 = `offsets[i+1] - offsets[i]`（最后一块 = `dataLen - offsets[last]`）。

### 1.2 像素格式

| bpp（sizes.txt 第 3 列） | 存储单元 | 字节序/转换 |
|---|---|---|
| 2 | `u16` LE，RGB565 | R=`(v>>11)&0x1F`, G=`(v>>5)&0x3F`, B=`v&0x1F`；各通道线性放大 32/64→256（原版 `Color16ToInt`：`b=(v&0x1F)/32*256; g=((v>>5)&0x3F)/64*256; r=(v>>11)/32*256`） |
| 4 | `u32` LE | .NET `Format32bppArgb` 的 ARGB int（`0xAARRGGBB`），故文件字节序为 **B,G,R,A** |

- 一像素单元对应一个目标 32bpp 像素；图像行无额外 stride 填充（行字节 = width*bpp）。
- 每块解压后没有单独的子头（无宽高字段）——**图像尺寸必须由外部推断**，这是原版"让用户自己选分辨率"的根因。

### 1.3 尺寸推断表 `sizes.txt`

- 文本格式：每行 `WIDTHxHEIGHTxD`（D∈{2,4}，缺省 2）。
- 程序内部字典 key = `Width * Height * ColorDepth`（＝解压后块字节数）。
  因此 `sizes.txt` 实际是"解压长度 → 分辨率/bpp"的查表。
- 解压后块大小在表中 → 自动识别；不在表中 → 弹 `SizeDialog` 让用户选；用户取消/关闭 → 加入 `ignored.txt`（按解压长度记录）以后不再询问。

### 1.4 样例数据（实测解析）

| 文件 | 大小 | tag | blockCount | 备注 |
|---|---|---|---|---|
| `logo.bin` | 2,006,471 | `LOGO` | 51 | dataLen=2,005,959；首块 offset=212（0xD4），zlib 解压 2,073,600 = 540*960*4。逐块尺寸：540x960x4 ×27、720x1280x4 ×2、38x54x4 ×10、135x24x4 ×10、48x54x4 ×1、135x1x4 ×1（全部 bpp=4；解压后长度均命中 sizes.txt） |
| `lk.bin` | 294,644 | `LK` | **无块表** | dataLen@4=294,132=filesize-512，但 payload（@0x200 起）是**裸 ARM LK bootloader 代码**（`07 00 00 ea f2 52 00 ea ...` = ARM B 指令；全文件 0 个完整 zlib 流，无任何块表）。所以它不是 LOGO 块容器，只是复用了同一个 512B 通用头。重建版必须按"不透明 raw 容器"无损处理/原样回写，而不是当块表解析。 |

> ⚠️ 修正：`lk.bin` 与 `logo.bin` **不是**同一种 payload 结构（此前 §1.4 原文误标"原版用于第二屏"——第二屏走的是裸 `boot_logo` 文件，与 lk.bin 无关）。tag=`LOGO` 才是块容器；其它 tag（如 `LK`）在块表校验失败时应退回 raw 模式（见 §6 实现约定）。

（format-analyst 会给出逐块清单与 byte-identical 回包验证，见 `analysis/format/spec_bin.md`。）

---

## 2. 工程（项目）目录格式

创建项目时从 `logo.bin` 解包为：

```
<工作目录>/
├─ header              # 原文件前 512 字节（无扩展名！字符串池 #442='header'；打包时原样写回前 8 字节 magic/size，8..512 保留）
├─ .project            # 文本，每行一个块描述（顺序即 logo.bin 内块顺序）
├─ img1.png, img2.png, # 图像块；文件名 img{N}.png，N 从 1 开始且必须连续
├─ blockN.dat          # 非图像块（原样保存的解压后二进制），打包时原样回写
├─ sizes.txt           # 分辨率表（程序目录配置的副本/合并）
├─ ignored.txt         # 用户忽略的尺寸（按解压字节数）
├─ updater-script      # 可选：放入后打包 update.zip 时覆盖模板中的同名文件
└─ update-binary       # 可选：同上
```

`.project` 行格式（推断自 `LogoProject.AddImageBlock/AddBinaryBlock/ExtractImageName/ExtractColorDepth`）：

```
image:img1.png 540 960 4      # 图像块：前缀 image:文件名 空格 W 空格 H 空格 bpp
binary:block5.dat             # 二进制块：前缀 binary:+文件名
```

- 解析规则：`image:` 开头 → 取前缀后 `Split(' ')[0]` 为文件名；`Split(' ')[3]` 为 bpp（非 2/4 回退 2）。
- 重新打包时必须**保持块顺序**与块数一致；图像块在打包时被重新编码（读 PNG → 按 bpp 转像素 → zlib）。

---

## 3. 打包（PackProject）算法

1. 读 `.project` 的 BlockList；空 → 报错 `noImages`。
2. 逐块：
   - `image:` → `CompressImage(projectPath/filename, colorDepth)`：读图片 → 按 W,H(取自 BlockList) 缩放/取像素 → 转 RGB565 或 BGRA → **Ionic.Zlib.ZlibStream(CompressionLevel.BestCompression)** 压缩。
   - `binary:` → `blockN.dat` 原始字节（**不再压缩**，原样写入）。
3. `dataLength = 8 + 4*count + Σ blockLen`。
4. 写文件：
   ```
   header[0..4) = magic(原 header 前 4 字节)
   u32 dataLength
   header[8..512) = 原 header 8..512
   u32 count
   u32 dataLength
   u32 offsets[i] = 8 + 4*count + Σ_{j<i} len(block[j])
   block[i] 依次写入
   ```
5. `header` 来自项目 `header.bin`（必须是 512 字节；也可来自当前打开的 logo.bin）。

- 注意：原版对重新编码的图像**不做尺寸校验以外的检查**；`imageSizeError` 在打包时若 PNG 尺寸与 BlockList 不符会报错（要求用户改尺寸或删图）。
- 注意：`Binary` 块直接写原始字节，若原块本身是 zlib 压缩数据，会被原样保留（正确保真）。

---

## 4. update.zip（卡刷包）生成

- 模板为程序内嵌资源 `LogoBuilder.update.zip`（**已提取**：`analysis/resources/ra_LogoBuilder.update.zip`，126,439 B）。内容：

| 路径 | 大小 | 说明 |
|---|---|---|
| `META-INF/com/google/android/update-binary` | 190,484 | ARM ELF，CWM 通用 update-binary（2012 版） |
| `META-INF/com/google/android/updater-script` | 184 | 见下 |
| `META-INF/MANIFEST.MF` / `CERT.SF` / `CERT.RSA` | — | JAR 签名（CWM 需要） |

模板 `updater-script` 原文：

```
ui_print("Flashing logo...");
show_progress(0.200000, 0);
package_extract_file("logo.bin", "/dev/logo");
ui_print("Patched!");
ui_print("");
ui_print("Now you can reboot your phone");
```

- 生成流程：复制模板 zip → 根目录加入新的 `logo.bin`（原版固定名 `logo.bin`）→ 若项目目录存在 `updater-script`/`update-binary`，用它们**覆盖**模板中的同名路径（`META-INF/com/google/android/`）。
- 结果写 `<项目路径>/update.zip`（或用户指定路径），成功后弹 `takeIt` 提示。

### 4.1 现代机型差异（重建版必须支持，原版没有）

- Android 11+ 的 `logo` 分区可直接 `fastboot flash logo logo.img`（原始镜像即上面格式，**无需 zip**）；原版只能出卡刷包。
- 部分 MTK 机型 logo 分区为 `logo_a`/`logo_b`（A/B），或用 `logo.img` 容器（YAMLP 文档：0x200 头 + block_count/payload_end + offsets[] + zlib，见 `docs/alternatives.md`）。
- `lk.bin` 与 logo.bin **不是**同构：同一 512B 通用头 + tag=`LK`，但 payload 是裸 ARM LK bootloader（无块表、无 zlib），重建版按 raw/不透明容器无损处理（详见 `analysis/format/spec_bin.md` §8 与本文 §1.4）。
- `boot_logo`（老 MTK 第二屏裸数据）：**无容器头**，裸 RGB565 像素，长度 = W*H*2。
- 现代工具还需处理：dump 出的 `logo.img` 与 `logo.bin` 命名混用、分区大小约束（打包后需 ≤ 原分区大小，YAMLP 要求零填充回原文件大小）。

---

## 5. 原有功能与交互（MainForm / SizeDialog / Program）

### 5.1 主流程

0. **命令行模式**（原版 `Program.RunArgs`，参数个数>=3 时）：`LogoBuilder unpack <logo.bin> <项目目录>` / `pack <项目目录> <输出logo.bin>` / `bl2img <boot_logo> <png>` / `img2bl <图片> <boot_logo>`；1 个参数时当作语言代码（如 `en-US`）。重制版 CLI 已提供等价命令并对齐这些名称。
1. **新建项目**：选择 `logo.bin`（或 `logo.img`）→ 选择**新项目目录** → 解析 header、块表、逐块解压 → 图像块存 `imgN.png`、二进制块存 `blockN.dat` → 写 `.project` → 搜索程序目录/当前目录的 `updater-script`、`update-binary` 复制进项目 → 打开项目。
2. **打开已有项目**：选择含 `header.bin`+`.project` 的目录 → 读 BlockList → 加载图片预览。
3. **替换图片**：双击预览图（或按钮）选 `bmp/jpg/png`，自动缩放/转格式后覆盖 `imgN.png`；也可直接改项目目录里的图片文件（FileSystemWatcher 自动刷新）。
4. **打包**：`PackProject` → 生成新的 `logo.bin`，**无另存为对话框**，固定写入 `<项目目录>/logo.bin`（`MainForm.btnPack_Click` 5633 行附近）。
5. **生成卡刷包**：`logo.bin` + 模板 → 固定写入 `<项目目录>/update.zip`（同样无另存为），成功后弹 `takeIt` 提示。
6. **第二屏（boot_logo）**：拖图片到窗口 / "创建第二屏"按钮 → 生成裸像素 `boot_logo` 文件；"打开第二屏"→ 读取裸数据并推断。
7. **分辨率选择对话框（SizeDialog）**：滑块 + 预览 + 下拉 bpp，用于未知尺寸的块；用户选择后记入 `sizes.txt`。
8. 设置保存位置（修正）：`sizes.txt`/`ignored.txt` 写在**程序 exe 目录**（`LogoProject.SaveSettings` 2125/2199 行）；"上次打开的镜像/过滤索引"等 Last* 设置走 .NET ApplicationSettingsBase → `%LOCALAPPDATA%\LogoBuilder\...\user.config`；**语言不持久化**（仅命令行参数）、**窗口位置不持久化**。项目目录本身**不包含** sizes.txt/ignored.txt。

### 5.2 已确认的原版缺陷（重建版须修复）

| # | 现象 | 代码证据 / 根因 |
|---|---|---|
| B1 | 分辨率选择窗口有时不出现/弹出后卡死 | **根因修正（re-analyst 反射实测，`analysis/original_behavior.md` §SizeDialog）**：GUI 下 owner 恒为主窗体，并非 owner=null。真实链：`SizeDialog.InitSizes` 按像素数做因数分解生成滑块候选，遇到质数/因数≥6000（`MaxSize=6000`）时失败 → `tbSize.Maximum=-1` 抛异常 → 被 `DecompressBlock` 的 `catch` 吞掉 → **把该尺寸永久写入 `ignored.txt`**（用户点取消/关闭同样永久忽略）→ 下次不再弹窗。另：拖动滑块每次全量解码/重绘导致"假死"观感；CLI 场景才存在 owner=null → `ErrorCode=7`。 |
| B2 | 目录选择经常出问题 | 使用自带的 `Ionic.Utils.FolderBrowserDialogEx`（包装 Win32 `SHBrowseForFolder`，CSIDL 枚举），在中文/长路径/权限/OneDrive 路径下易失败；且"选择新项目目录"直接以该对话框结果作为项目路径，不校验可写性。 |
| B3 | 无法在软件内替换图片（只能外部改文件） | MainForm 只提供"打开目录"按钮 + FileSystemWatcher；没有"选择图片替换当前块"的完整流程（双击预览的编辑路径有限、易错）。 |
| B4 | 只识别 bin 格式 | **修正**：OpenFileDialog 过滤器其实有 `logo.bin|logo.bin|logo.img|logo.img|所有文件|*.*`，且解析**根本不校验 magic/tag**，只校验 512 字节头 + `dataLen@4`/`dataLen@0x204` 一致性（这正是它对 `lk.bin`、其他变体格式报"数据长度错误"的原因；对无头裸格式直接失败）。 |
| B5 | 只能打包卡刷包，不能导出单独 LOGO IMG | "打包"输出 logo.bin；"卡刷包"输出 update.zip；没有直接导出 `logo.img`（原始分区镜像）或按分区大小对齐的选项。 |
| B6 | 无法自动识别正确分辨率 | 解压块无尺寸元数据，只能查 `sizes.txt` 的"解压长度→尺寸"表；表未命中就弹窗人工选；没有基于宽高比/stride/相邻块一致性的自动推断，也没有从文件名/内容启发式猜测。 |
| B7 | 图像块无 alpha 处理 / 32bpp 误判 | 4bpp 直接写 BGRA；32bpp 检测 `LooksLike32Bit` 用"每 4 字节最高位=0xFF"启发（`alpha>=0xFF000000`），透明图会误判。 |
| B8 | 打包/解包对异常输入不健壮 | `dataLen > filesize-512` 才检查；块偏移不排序/不校验重叠；`count` 巨大时 `new int[count]` 直接 OOM；`dataLen` 与块长度不一致时报 `dataLengthError1/2` 但信息不友好。 |
| B9 | 项目文件/图片一致性靠用户 | `.project` 与 `imgN.png` 不同步时报 `noImages`/`imageSizeError`，没有自动修复。 |
| B10 | 仅 Windows / 无 HiDPI | WinForms + 固定像素布局 + `AutoScaleMode` 旧式；UTF-16 资源；无跨平台能力。 |

### 5.3 资源清单（重建版可复用的事实）

- 多语言：es / it / pt / ru / zh-Hans / 默认英文（.resources satellite 已提取，`analysis/resources/strings/*.txt`）。
- 提示文本：`tips.txt`（各语言），中文版说明"需要官方线刷包里的 logo.bin"、"支持第二屏"、"放入 update-binary/updater-script 自定义"。
- 图标/图片资源：`LogoBuilder.Properties.Resources.resources`、`MainForm.resources` 等已提取（RC 数据）。

### 5.3 逆向过程中新发现的原版缺陷（N1–N13，完整证据见 `analysis/original_behavior.md` §9）

| # | 严重度 | 缺陷 |
|---|---|---|
| N1 | **致命** | `LastColorDepthIndex` 用户配置越界时，`LogoProject` 静态构造 `new SizeDialog()` 抛 `ArgumentOutOfRangeException` → **程序每次启动即崩且用户无法自恢复**（只能删 user.config） |
| N2 | 高 | 解包时 `bitmap==null` 的块被静默跳过 → 后续 `imgN` 编号与块顺序错位，打包时块数/顺序与镜像不一致 |
| N3 | 中 | `Image.FromFile` 从不 Dispose → 批量替换/预览导致文件句柄泄漏，图片被占用无法删除 |
| N4 | 中 | 主窗体滚轮 `-e.Delta*70` 一次滚动 8400px，直接到列表底/顶 |
| N5 | 中 | `header==null`（未打开项目）时打包抛裸异常 |
| N6 | 中 | 图像块解码失败原因被吞，只显示通用错误 |
| N7 | 中 | update.zip 自定义脚本条目用**反斜杠** `META-INF\com\google\android` 路径 → 非 Windows 的 zip 工具/Recovery 可能找不到 |
| N8 | 低 | RGB565 解码量程：5bit→`<<3`(max 248)、6bit→`<<2`(max 252)，不回填到 255（标准做法，但预览会略暗） |
| N9 | 低 | `imageSizeError` 是死字符串：打包时**没有**校验 PNG 尺寸与 BlockList 是否一致 |
| N10 | 低 | 加 logo.bin 后**不重新签名** zip（CERT 摘要仍是模板旧值，仅靠 CWM 关闭签名校验才能刷） |
| N11 | 低 | 模板 zip 的 MANIFEST 里列有 logo.bin 摘要，但模板内实际没有 logo.bin（旧模板残留） |
| N12 | 低 | `sizes.txt` 解析用当前区域性的 `int.Parse`，非 invariant（某些区域设置下解析失败） |
| N13 | 低 | 反调试守卫（`IsDebuggerPresent`/`CheckRemoteDebuggerPresent`/`EnumWindows`）会让现代调试器/合规工具直接拒绝运行，且错误信息无中文提示 |

---

## 6. 重建版设计目标（本仓库实现）

1. **跨平台**：纯 Python 3.10+ / tkinter + ttkbootstrap，Windows/macOS/Linux 一致；高分屏 `SetProcessDpiAwareness` / `tk scaling` 自适应。
2. **格式兼容**：读 `logo.bin`/`logo.img`/`lk.bin`/裸 `boot_logo`；写 `.bin`/`.img`；保留未知二进制块；blockCount/offset 严格自洽校验。
3. **自动识别分辨率**：内置 `sizes.txt` 全量尺寸表 + 解压长度反查 + 宽高比/像素统计启发式 + 相邻块推断；不确定时给出候选而非强制弹窗。
4. **软件内替换图片**：双击/按钮选图，自动缩放（保持/拉伸可选）、预览、恢复原始。
5. **输出**：单文件 `logo.img`（原始镜像，可选按分区大小对齐/零填充）、`logo.bin`、`update.zip`（兼容 CWM/原版模板，可自定义脚本）、`boot_logo`。
6. **健壮性**：任何损坏输入给出可读错误 + 不崩溃；打包前校验并一键修复（尺寸不符/缺图/多余块）。
7. **中文优先 + 英文** UI，CLI 可脚本化；全部核心逻辑可单元测试（无 GUI 依赖）。


---

## 7. 附：字符串池解密后确认的原版常量（Crypto Obfuscator 解除）

> 来源：`analysis/tools/string_usages.tsv`、`stringpool_dump.txt`（对原版 string decryptor 反射调用 + 全量索引扫描，静态分析获得；未执行 GUI）。这些是原版代码里实际使用的字符串，可直接作为兼容性依据。

| 用途 | 原版明文 |
|---|---|
| 工程头文件 | `header`（无扩展名，512 字节） |
| 工程块列表 | `.project`（CRLF 文本） |
| 尺寸表 / 忽略表 | `sizes.txt` / `ignored.txt` |
| 图像块文件名 | `img{0}.png`（编号从 1） |
| `.project` image 行 | `{0}{1} {2} {3} {4}` → `image:img1.png 540 960 4` |
| `.project` binary 行 | `binary:` + `block{0}.dat` → `binary:block1.dat` |
| 图像前缀 / 二进制前缀 | `image:` / `binary:` |
| 卡刷包模板资源 | `LogoBuilder.update.zip` |
| 卡刷包内脚本路径 | `META-INF/com/google/android`、`META-INF/com/google/android/updater-script` |
| 原版 CLI 命令 | `unpack`、`pack`、`bl2img`、`img2bl` |
| 默认窗口标题 | `Logo Builder v. {0}` |
| 未知尺寸提示 | `Unknown image size.` |
| 第二屏输出默认名 | `boot_logo` / 打开后另存 `boot_logo.png` |
| 官网 | `http://sites.google.com/site/kadanutilities/home/logobuilder` |

原版错误信息（从 `.resources` 提取，重制版保留同义中文文案以便老用户辨认）：

```
noImages: "No images found.

Note that images numbers must start from 1."
dataLengthError1: "Error in data length (1st case)"
dataLengthError2: "Error in data length (2nd case)"
imageSizeError: "Image size doesn't correspond to settings {0}.

You must fix this error or delete the image file."
errorPacking: "Error packing new logo.bin

{0}"
errorCreatingUpdate: "Error creating update.zip

{0}"
errorCreatingBootLogo: "Can't create boot_logo file
{0}"
selectNewProjectFolder / selectExistingProjectFolder / cantCreateProject / cantOpenProject ...
```
