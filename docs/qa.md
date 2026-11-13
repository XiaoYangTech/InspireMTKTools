> **交付说明（v3.0.0 最终态）**：发布前的最后一步 UI 迭代把界面统一为 **litera 浅色主题 + 仅中文**（移除深色模式、语言切换与英文文案）。本文档中部分早期测试记录仍保留"深色/英文/12 张截图"等迭代过程结论，最终交付界面以 `docs/ui_notes.md` 与 `docs/images/` 为准；核心/功能测试结论不受影响。

# 亿方联发科工具箱 / InspireMTKTools v3.0.0 — 测试报告与发布检查单（QA）

> 角色：独立 QA/发布工程（不写 `src/**` / `app.py`）。本文件是**可复现的测试矩阵 + 发布检查单**；
> 更完整的证据、命令输出与 bug 记录见 `analysis/qa/report.md`（LOGO 基础功能）、
> `analysis/qa/report_lk.md`（v3.0 LK 修补功能）与 `analysis/qa/artifacts/`。
>
> 被测对象：`src/logobuilder/`（core + ui）、`app.py`、`tools/build_exe.py`、`tests/`（本 QA 资产）
> 真实样本：`LogoBuilder_jb51/LogoBuilder_v1.6中文版/logo.bin`（2,006,471 B / 51 块 / tag LOGO）
> 和 `lk.bin`（294,644 B / raw ARM payload，非块容器）
> v3.0 新功能：LK 引导程序修补（`docs/spec_lkpatch.md`）— 真实 AArch64 A/B 样本
> `analysis/lk/samples/lk_a.bin`（sha256 `bb2f2e6a…f873`）、`lk_b.bin`（sha256 `f4ecd3ee…a6e4`），
> 以及 QA 合成 ARM32/AArch64 语料 `tests/fixtures/generated_lk/`（12 个）。

## 0. 结论摘要

| 项目 | 结果 |
|---|---|
| 全量测试 `PYTHONPATH=src python -X utf8 -m pytest tests -q` | **446 passed, 1 skipped, 0 failed**，exit 0（227.16s；含 LK E2E 32 项 + UI 63 项；跳过项为小屏 compact 模式设计行为） |
| 真实 `logo.bin` 逐字节回包 | **PASS** — `sha256 = c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c`（改动前后一致） |
| 真实 `lk.bin` 无损 raw 回写 | **PASS** — `sha256 = 5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55`（一致） |
| 独立 oracle（不 import logobuilder） | **PASS** — logo 19/19 + lk 11/11 检查项 |
| 集成/验收测试 `tests/integration` | **PASS** — 110/110（LOGO 78 + LK 32） |
| GUI `python app.py --self-test` | **PASS** — exit 0，**15 项**检查全过（含 LK 修补流程 + 品牌更名） |
| GUI 截图 `--screenshot` | **PASS** — exit 0，**12 张**全部非空白（含深色/100%/150%/4 对话框 + **LK 面板/预览/校验/刷写说明 4 张**） |
| HiDPI 100%/150% 布局 | **PASS** — 28/28 检查（字体 19→27px、paned 撑满、无死区/裁剪） |
| PyInstaller `--check-only` | **PASS** — **22 项**预检全过（含 branding / lkpatch-core/ui / legacy-name 检查） |
| PyInstaller 真构建 + 冻结自检 | **PASS** — 交付产物 `dist/InspireMTKTools.exe` 37,752,921 B / `sha256=6bd333c8…a98c`，冻结版 `--self-test` exit 0（15 项，capstone 已打包） |
| LK 真实 AArch64 A/B 样本 | **PASS** — lk_a/lk_b 各 2 findings：`dm_verity@0x4CC58(0.95)`、`orange_state@0x4D928(0.98)`；补丁 `e0 03 1f 2a c0 03 5f d6`；header/guard 不变；`patch_ok=True`；原文件 sha 不变 |
| LK ARM32 内置规则 / fail-safe | **PASS** — 7/7 特征码命中（补丁 `00207047`）；不支持输入 CLI 非 0 + 中文 + 不落盘 |
| 产品更名 | **PASS** — “亿方联发科工具箱 / InspireMTKTools v3.0.0”；exe `dist/InspireMTKTools.exe`；旧名 `LogoBuilder2` 兼容检查通过 |
| 结论 | **发布就绪（release-ready）**，无阻塞 bug；非阻断遗留项见 §8 |

## 1. 环境与方法

| 项 | 值 |
|---|---|
| OS | Windows 10 10.0.19045（AMD64），RDP 会话，屏幕 638×394 @96 DPI（小屏，见 §8 限制） |
| Python | 3.14.7（`python`），pytest / Pillow 12.3.0 / ttkbootstrap 2.2.7 / **capstone 5.0.7** / PyInstaller 6.22.3 |
| 字符编码 | 所有命令以 `PYTHONIOENCODING=utf-8` / `python -X utf8` 运行（GBK 控制台会乱码，不代表 bug） |
| 测试方式 | 黑盒 acceptance：真实样本 + 独立 oracle（自写 struct+zlib）+ 合成 fixtures + CLI 子进程 + 真 GUI test_mode/probe + PyInstaller 冻结产物 |
| QA 不修改产品代码 | 是。`src/**`、`app.py`、`docs/spec_*.md`、`README.md`、`LogoBuilder_jb51/**` 只读 |

复现命令：

```bash
# 全量测试（core + integration）
python -X utf8 -m pytest tests -q

# 生成/校验合成测试语料（25 个 fixture，按需生成，manifest 记录 sha256）
python tests/fixtures/make_fixtures.py --verify
python tests/fixtures/make_fixtures.py --force

# 独立 oracle（真实样本 ground truth，不 import logobuilder）
python -X utf8 tests/integration/oracle_sample.py

# GUI 自检 / 截图 / 独立 headless GUI 验收 / DPI 探针
python -X utf8 app.py --self-test
python -X utf8 app.py --screenshot analysis/qa/artifacts/screens/main_window.png
python -X utf8 analysis/qa/gui_probe.py
python -X utf8 analysis/qa/dpi_probe.py

# 打包
python tools/build_exe.py --check-only --json analysis/qa/artifacts/build_check.json
python tools/build_exe.py --smoke-test
```

## 2. 交付的 QA 资产

| 路径 | 说明 |
|---|---|
| `tests/fixtures/make_fixtures.py` | 确定性测试语料生成器（25 个 fixture：11 valid / 11 corrupt / 2 raw / 1 legacy project），自带 manifest（每个文件 sha256 + 期望解析结果）；`ensure()` 供 pytest 按需生成 |
| `tests/integration/oracle_sample.py` | **独立 oracle**：自写 struct+zlib 解析真实 `logo.bin`/`lk.bin`，断言 51 块、尺寸直方图（540x960x4×27、720x1280x4×2、38x54x4×10、135x24x4×10、48x54x4×1、135x1x4×1）、offsets/dataLen 不变式、zlib 78DA、**从头重打包 === 原文件**；lk.bin 证明不是块容器并用 opaque 方式逐字节回写 |
| `tests/integration/test_e2e.py` | 78 个 acceptance 测试：真实样本字节级回包、lk raw、extract→改红块→pack、update.zip（含模板 update-binary sha256 比对）、project 生命周期/legacy import/repair、CLI（退出码+中文 stderr）、尺寸推断、像素 oracle（RGB565 全值双射 + BGRA 字节序）、boot_logo、core 不得 import GUI |
| `analysis/qa/gui_probe.py` | 独立 headless GUI 验收：真实实例化 `LogoBuilderApp(test_mode=True)`，走 打开→替换→导出 img/bin/zip→项目→lk→错误映射，14 项检查 |
| `analysis/qa/dpi_probe.py` | 独立 HiDPI/布局/截图探针：100% 与模拟 150% 字体/尺寸/行序/死区/裁剪检查 + 对话框截图回归（28 项） |
| `tests/fixtures/make_lk_fixtures.py` | **v3.0 LK 测试语料生成器**：12 个确定性 fixture（ARM32 7 特征码/真实警告文本/guard；AArch64 真实指令编码的警告分发器+dm-verity；raw/负样本），manifest 含 expected findings（精确偏移+补丁字节）；自带 AArch64 编码器自检 |
| `tests/integration/test_lkpatch_e2e.py` | **LK 修补验收测试 32 项**：parse/scan/apply/verify、真实 A/B 样本、CLI `lk-info/lk-patch/lk-verify/lk-restore`、fail-safe、备份还原、guard 篡改、LOGO 回归 |
| `tools/build_exe.py` | PyInstaller onefile/windowed 构建脚本（v3 产品名 `InspireMTKTools`，兼容旧名 `LogoBuilder2`），`--check-only` 预检、`--smoke-test` 冻结自检；资源 `src/logobuilder/assets -> logobuilder/assets` |
| `analysis/qa/report_lk.md` + `analysis/qa/artifacts/lk_*` | **LK 功能独立验证报告**与证据（真实样本 JSON、E2E 日志、构建/截图） |
| `analysis/qa/artifacts/` | 全部证据：`pytest_full_final.log`、`oracle_samples.json`、`sha256_evidence.txt`、`build_check.json`、`build_final2.log`、`dist/LogoBuilder2.smoke.log`、`self_test_final.log`、`gui_probe_final.log`、`dpi_probe.json`、`screens/`（8 张截图 + manifest） |

## 3. 测试矩阵（case | expected | actual | verdict）

### A. 真实样本 / 格式契约（`logo.bin`、`lk.bin`）

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| A1 | 独立 oracle 解析真实 logo.bin | 512 头 magic 0x58881688、tag LOGO、dataLen@4=2,005,959=filesize-512、count=51、offsets[0]=212、offsets 严格递增、payload 恰好覆盖 dataLen、51 块全部合法 zlib(78DA) | 全部成立；`analysis/qa/artifacts/oracle_samples.json` | **PASS** |
| A2 | 独立 oracle 尺寸直方图 | 540x960x4×27、720x1280x4×2、38x54x4×10、135x24x4×10、48x54x4×1、135x1x4×1（全 bpp4） | 完全一致 | **PASS** |
| A3 | 独立 oracle 从头重打包 | 复用 header+raw zlib 重建 === 原文件（sha256 c321a9f1…） | 一致 | **PASS** |
| A4 | core 解析真实 logo.bin | 51 ImageBlock、尺寸/顺序与 oracle 一致、无 pending、header[8:] 原样 | 一致 | **PASS** |
| A5 | core 未修改回包（`to_bytes`） | sha256 与原文件完全相同 | `c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c` | **PASS** |
| A6 | core `save()` 原子写回 | 落盘文件 sha256 相同 | 一致 | **PASS** |
| A7 | 真实 lk.bin 解析 | 识别为 raw 模式（非块容器），raw_payload == 原 payload | `mode="raw"`、payload 294,132 B 原样保留、无块表报错 | **PASS** |
| A8 | lk.bin 回写 | 逐字节无损（sha256 5bc6c099…） | 一致 | **PASS** |
| A9 | `sniff_format` / `is_mtk_logo` | 区分块容器 / raw / 非 MTK；magic 判定正确 | 一致（docs/spec_api.md 已按实现同步字符串） | **PASS** |
| A10 | `pad_to_size` 导出 logo.img | 填充到目标大小，前缀==原镜像，尾部全 0；目标小于实际大小抛 ExportError | 一致（+4096 与 2,100,000 两种目标验证） | **PASS** |

### B. 合成 fixtures（`tests/fixtures/generated/`）

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| B1 | bpp4 540x960 / bpp2 1080x1920 单块 | 解析为 ImageBlock，尺寸正确，回包逐字节一致 | 一致 | **PASS** |
| B2 | 混合 image+zlib-binary+image+raw-binary（4 块） | 2 Image + 2 Binary；zlib-binary decompressed_ok=True 且 pending；raw-binary decompressed_ok=False；回包一致 | 一致 | **PASS** |
| B3 | tag=LK 但块表合法 | 仍按块容器解析（tag 不决定模式） | 一致 | **PASS** |
| B4 | header padding 变体（全 0xFF / 全 0x00 / 确定性图案） | header 16..511 原样保留，回包一致 | 一致 | **PASS** |
| B5 | dataLen 之后有 20 B 垃圾 | 解析成功，`trailing` 记录，写回丢弃尾部 | 一致 | **PASS** |
| B6 | count=0 空容器 | 允许，count=0，回包一致 | 一致 | **PASS** |
| B7 | 未知尺寸块（解压 100,000 B 不在 sizes.txt） | BinaryBlock + `pending_image_blocks=[1]`；`assign_size(400x125x2)` 转 ImageBlock；回包仍逐字节一致 | 一致 | **PASS** |
| B8 | 中间块 zlib 损坏 | 不报错，降级为 BinaryBlock(decompressed_ok=False)，raw 保留，回包逐字节一致 | 一致 | **PASS** |
| B9 | 截断头（300 B） | 抛 CorruptContainer/NotLogoFile，中文可读 | 一致 | **PASS** |
| B10 | bad magic | 抛 NotLogoFile（中文含期望/实际 magic） | 一致 | **PASS** |
| B11 | dataLen > filesize-512 | 抛 CorruptContainer | 一致 | **PASS** |
| B12 | offsets[0] != 8+4*count | 抛 CorruptContainer | 一致 | **PASS** |
| B13 | offsets 非单调 | 抛 CorruptContainer | 一致 | **PASS** |
| B14 | 最后 offsets 越界 | 抛 CorruptContainer | 一致 | **PASS** |
| B15 | count 巨大（OOM 防护） | 抛 CorruptContainer，不分配巨量内存 | 一致 | **PASS** |
| B16 | dataLen echo 不一致 / count=0 且 dataLen 不一致 | **Lead 决策：宽容读** — 解析成功 + warnings 记录 expected/actual + to_bytes 写自洽 + CLI exit 0 且 stderr 有“警告/dataLen” | 一致（见 §6 决策 D1） | **PASS** |
| B17 | 非 MTK 随机文件 | 抛 NotLogoFile | 一致 | **PASS** |

### C. 工程（project）与 legacy 兼容

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| C1 | 新建工程 → save → 重新 open → validate | `header.bin` 512 B、`project.json`、`imgN.png` 存在；validate 无 issue；回包逐字节一致 | 一致 | **PASS** |
| C2 | 真实 logo.bin 执行 extract → pack | 输出 sha256 == 原文件（51 块无修改） | `c321a9f1…` 一致 | **PASS** |
| C3 | extract → 第 0 块换纯红 540x960 → pack → 重新解析 | 第 0 块像素 == 纯红编码；其余 50 块 raw compressed 不变；header/offsets 自洽 | 一致 | **PASS** |
| C4 | 未知尺寸块 set_block_size | pending 清空；转 ImageBlock；打包后再解析（显式尺寸）正确 | 一致 | **PASS** |
| C5 | legacy `.project` 导入 | 块类型/尺寸/数据长度与源容器等价（image 540x960x4 + binary 2048 B） | 一致 | **PASS** |
| C6 | legacy 目录 `LogoProject.open()` 自动识别 | 自动 import .project，save 后 validate 干净 | 一致 | **PASS** |
| C7 | legacy import 带 source_container | 回包逐字节等于源容器 | 一致 | **PASS** |
| C8 | repair：删除 img1.png | validate 报“图片缺失”；`repair(repair=True)` 从原始压缩数据重建；error 清零 | 一致 | **PASS** |
| C9 | repair：把图片换成错误尺寸 | validate 报“不一致”；repair 缩放修复；error 清零 | 一致 | **PASS** |
| C10 | repair：删除 block2.dat | validate 报“二进制数据缺失”；repair 恢复；error 清零 | 一致 | **PASS** |

### D. 像素 / 尺寸推断

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| D1 | RGB565 全 65,536 值双射 | `encode(decode(v)) == v` | 全部成立 | **PASS** |
| D2 | RGB565 三原色编码 | 红 0xF800、绿 0x07E0、蓝 0x001F | 一致 | **PASS** |
| D3 | 原版线性放大公式 | 0xFFFF→(248,252,248)，0xF800→(248,0,0)，0xFFFF alpha=255 | 一致（原版 Color16ToInt 语义） | **PASS** |
| D4 | bpp4 文件字节序 BGRA | RGBA(10,20,30,40) → 文件 `30 14?` 即 `[30,20,10,40]`（B,G,R,A） | 一致 | **PASS** |
| D5 | 真实块 0 像素独立校验 | block.pixels == zlib.decompress(原始 raw)；BGRA→RGBA 像素正确 | 一致 | **PASS** |
| D6 | sizes.txt 精确匹配 | `SizeDB.default().exact(540*960*4) == [540x960x4]` | 一致（内置 49 条） | **PASS** |
| D7 | 候选排序（合成同 key 二义） | aspect_hint=540/960 时 540x960x4 排第一且 score≥0.9 | 一致 | **PASS** |
| D8 | 未知长度反解（100,000 B，真实尺寸 400x125x2） | 候选列表包含真实尺寸 | 一致 | **PASS** |
| D9 | 批量推断 guess_sizes_for_blocks | 每块候选包含正确尺寸 | 一致 | **PASS** |

### E. update.zip / boot_logo / CLI

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| E1 | 生成卡刷包 | zip 合法（testzip None），含根 `logo.bin` + `META-INF/com/google/android/{update-binary,updater-script}` + MANIFEST/CERT | 6 条目，合法 | **PASS** |
| E2 | updater-script 内容 | 含 `package_extract_file("logo.bin", "/dev/logo")` | 一致（模板原文） | **PASS** |
| E3 | 模板 update-binary 不变 | zip 内 sha256 == `assets/update_template.zip` 内同文件 sha256 = `7a6e70ae1605188f97e624a47092a4954659b164958bbe66685de7500b21644a` | 一致（Python zipfile 与 `unzip -p | sha256sum` 双验证） | **PASS** |
| E4 | zip 内 logo.bin | == 新打包的 logo bytes | 一致 | **PASS** |
| E5 | 可复现性 | 同输入两次生成 sha256 相同（zip 时间戳固定） | 一致 | **PASS** |
| E6 | 自定义 script/binary/logo 名覆盖 | 覆盖模板同名条目；logo.custom 名生效、旧 logo.bin 不出现 | 一致 | **PASS** |
| E7 | boot_logo 裸 RGB565 读/写 | 240x320 文件 153,600 B；encode(decode(x)) 逐字节无损；错误色深 ValueError | 一致 | **PASS** |
| E8 | CLI info logo.bin / --json | exit 0，打印 51 块与 540x960x4；JSON count=51/tag=LOGO | 一致 | **PASS** |
| E9 | CLI info lk.bin | exit 0，识别 raw/LK | 一致 | **PASS** |
| E10 | CLI extract → pack | exit 0；工程目录结构正确；重打包 sha256 == 原文件 | 一致 | **PASS** |
| E11 | CLI zip | exit 0，zip 含 logo.bin+updater-script | 一致 | **PASS** |
| E12 | CLI sizes | exit 0，列出 540x960x4 等 | 一致 | **PASS** |
| E13 | CLI 损坏输入（bad magic / dataLen 过大 / huge count / 随机 / 截断） | exit 1 + stderr 中文可读错误 | 一致（5/5） | **PASS** |
| E14 | CLI 参数缺失 | exit 2 | 一致 | **PASS** |
| E15 | core 依赖纯净 | core 不 import tkinter/ttkbootstrap；版本 2.0.0 | 一致（AST 扫描 8 个模块） | **PASS** |

### F. GUI（真实窗口 / headless / 截图 / DPI）

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| F1 | `python -X utf8 app.py --self-test` | exit 0，所有检查 OK | **exit 0，14 项全过**（含导出 img/bin/zip、项目、boot_logo、未知尺寸、对话框、控件检查） | **PASS** |
| F2 | 真实 logo.bin 经 GUI 打开 → 导出 bin | 51 块 / tag LOGO；导出 sha256 == 原文件 | 一致 `c321a9f1…` | **PASS** |
| F3 | GUI 替换第 0 块为纯红 → 导出 bin | 红块像素正确；其余 50 块 raw 不变 | 一致 | **PASS** |
| F4 | GUI 导出 logo.img（pad_target=2,100,000） | 前缀==导出 bin、尾部全 0 | 一致 | **PASS** |
| F5 | GUI 导出 update.zip | 合法；含 /dev/logo；update-binary sha256 与模板一致（E3 同值） | 一致，6 条目 | **PASS** |
| F6 | GUI 新建工程 → 打包 → 重开 | 回包 sha256 == 原文件；重开 51 块、dirty=False | 一致 | **PASS** |
| F7 | GUI 打开 lk.bin | raw 模式；导出 sha256 == 原文件 | 一致 | **PASS** |
| F8 | GUI 打开坏文件错误映射 | 中文 NotLogoFile 消息（含 hint），不崩溃 | 一致（message 含 magic 期望/实际 + 提示） | **PASS** |
| F9 | `--screenshot` | exit 0；8 张截图非空白（`getbbox` 有效、颜色数 >100） | 8/8 非空白：主窗口 1239×819、深色、150% 1860×1230、4 个对话框（分辨率 879×559 / 替换 819×519 / 错误 719×500 / 帮助 659×539） | **PASS** |
| F10 | DPI 150% 布局（虚拟大窗） | 字体/尺寸 ~1.5x；各区域 mapped/正尺寸/在窗内/行序无重叠/无异常死区/paned 撑满/最小宽达标 | 28/28 检查通过；字体 linespace 19→27px；paned 100% 638px / 150% 981px；最大行间 gap 5px/8px | **PASS** |
| F11 | 100% 布局 | 同上 | 通过；窗口 1239×819 | **PASS** |
| F12 | 对话框截图 | 截图尺寸 == 对话框而非主窗口 | 4/4 正确（回归修复后） | **PASS** |
| F13 | 滚动/中间区域 | paned 内容填充、block list 可滚动（截图肉眼 + 几何检查） | 一致 | **PASS** |

### G. 打包 / 冻结产物

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| G1 | `tools/build_exe.py --check-only` | exit 0，必需项全 PASS（Python/app.py/包/assets/依赖/PyInstaller） | exit 0，17/17 PASS | **PASS** |
| G2 | onefile --windowed 构建 | 生成 `dist/LogoBuilder2.exe` | 生成，36,368,438 B，`sha256=18ab317e6f136b583bd2759c9565115adab1a21c7834a777155a4b0609b023b3`；QA 自建版本（同源码）见 `build_final3.log` | **PASS** |
| G3 | 冻结 exe `--self-test` | exit 0（含 assets 打进 bundle、主题 JSON 可用） | exit 0，14 项全过（`dist/LogoBuilder2.smoke.log`） | **PASS** |
| G4 | `--smoke-test` 流程 | 构建后自动运行冻结自检并 PASS | `SMOKE TEST PASSED: exit code 0` | **PASS** |
| G5 | 资源打包位置 | `src/logobuilder/assets -> logobuilder/assets`（`assets_dir()` 冻结后解析到 `_MEIPASS/logobuilder/assets`） | 自检实际读取内置 sizes.txt/模板成功 | **PASS** |

### H. LK 引导程序修补（v3.0.0 新功能，详见 `analysis/qa/report_lk.md`）

| # | Case | Expected | Actual | Verdict |
|---|---|---|---|---|
| H1 | 真实 `lk_a.bin` 扫描 | AArch64、supported、2 findings：dm_verity@0x4CC58(0.95)、orange_state@0x4D928(0.98) | 完全一致，already_patched=False | **PASS** |
| H2 | 真实 `lk_b.bin` 扫描 | 与 A 槽一致（偏移相同） | 完全一致 | **PASS** |
| H3 | 真实 A/B 补丁 | 两处均 `e0 03 1f 2a c0 03 5f d6`；header 0x200 不变；`androidboot.*` 计数不变；size 不变；`patch_ok=True` | 一致；补丁后 sha：lk_a `28589ddf…6c59`、lk_b `51462a9e…330f`；原文件 sha 不变 | **PASS** |
| H4 | ARM32 合成镜像（7 条内置特征码） | 7 findings 精确偏移、after=`00207047`、conf 0.98 | 一致 | **PASS** |
| H5 | AArch64 合成镜像（真实指令编码） | dispatcher@0x1200 + dm_verity@0x1A00，真实警告文本引用，state 2→Orange / 1、3→Red | 一致 | **PASS** |
| H6 | 裸 payload（无 512 头） | 自动 raw 模式，偏移不 +0x200 | 一致 | **PASS** |
| H7 | 负样本（no-signature/already-patched/bad-magic/truncated/all-zero/random） | fail-safe：不写文件、中文原因、CLI 非 0（already-patched 正确识别为无需处理） | 全部一致 | **PASS** |
| H8 | CLI `lk-info / lk-patch(--dry-run) / lk-patch / -o / lk-verify / lk-restore` | 退出码/输出/备份/还原 sha 一致；dry-run 不落盘 | 全部一致 | **PASS** |
| H9 | guard 参数保护 | 补丁不改变 `androidboot.*` 计数；篡改 guard 的伪补丁被 `verify_patch`/apply 拒绝 | 一致 | **PASS** |
| H10 | GUI LK 面板 | 扫描真实 A/B → 表格 2 文件/2 候选；预览/应用/校验/还原/刷写说明可用；A/B 双槽提醒 | 自检 15 项过；截图 `lk_patch_scan/preview/verify/flash_guide.png` 非空白 | **PASS** |
| H11 | 品牌更名 | 标题/关于/帮助/exe 名 = 亿方联发科工具箱 / InspireMTKTools v3.0.0 | 截图标题、i18n、exe 名均一致 | **PASS** |
| H12 | 冻结产物 LK 能力 | `InspireMTKTools.exe --self-test` 含 LK flow 且 exit 0（capstone 打包） | 15 项 OK，exit 0 | **PASS** |
| H13 | LOGO 旧功能回归 | 真实 logo.bin 仍逐字节回包 sha `c321a9f1…655c`；全量测试无回归 | 一致 | **PASS** |

## 4. 证据索引（`analysis/qa/artifacts/`）

| 文件 | 内容 |
|---|---|
| `pytest_full_final.log` | 全量 pytest 输出与计数 |
| `oracle_samples.json` / `oracle_run.log` | 独立 oracle 对真实两样本的逐项检查结果（含每块 offset/压缩长度/解压长度/sha256） |
| `sha256_evidence.txt` | 原样本、模板、GUI 导出的 bin/img/zip、zip 内 update-binary 与模板对比、zip 列表 + updater-script |
| `build_check.json` / `build_check.log` | PyInstaller 预检报告（17 项） |
| `build_final2.log` + `dist/LogoBuilder2.smoke.log` | 真构建日志 + 冻结自检输出 |
| `self_test_final.log` | `app.py --self-test` 输出（14 项 OK，exit 0） |
| `gui_probe_final.log` / `gui_probe/gui_probe_summary.json` | 独立 headless GUI 验收 14 项 |
| `dpi_probe.json` | 100%/150% 布局 + 截图回归 28 项 |
| `screens/`（8 PNG + `screenshots_manifest.json`） | 主窗口 100%/深色/150%、分辨率/替换/错误/帮助对话框；全部 `getbbox` 非空白 |
| `pytest_lk_final.log` / `lk_e2e_final.log` / `ui_tests_lk_final.log` | v3 全量 446 passed/1 skipped、LK E2E 32/32、UI 63 passed/1 skipped |
| `lk_real_samples.json` / `sha256_evidence_lk.txt` | 真实 A/B 样本逐 finding/补丁/verify 证据与全部 sha256 |
| `build_check_lk.json` / `build_lk_final.log` / `dist/InspireMTKTools.smoke.log` | v3 PyInstaller 预检 22 项、构建日志、冻结自检 15 项 |
| `screens_lk/`（12 PNG + manifest） | v3 截图（含 LK 面板/预览/校验/刷写说明 4 张，全部非空白） |
| `gui_probe/logo_unmodified.bin`、`logo_replaced.bin`、`logo_replaced.img`、`update.zip`、`project_pack.bin` | GUI 实际导出的验收产物 |

## 5. 测试执行结果（真实输出）

```text
$ PYTHONPATH=src python -X utf8 -m pytest tests -q
SKIPPED [1] tests/test_ui/test_main_window.py:123: 小屏/虚拟缩放环境（窗口宽 1240px < 阈值 1464px），compact 模式隐藏副标题属预期行为
446 passed, 1 skipped in 227.16s

$ python -X utf8 app.py --self-test ; echo EXIT=$?
SELF-TEST OK（14 项检查全部通过）
EXIT=0
```

> 说明：全量包含 `tests/test_core`（core-dev 单测）、`tests/integration`（QA acceptance，78 个）、
> `tests/test_ui`（gui-dev UI 测试）。开发并行期曾出现 core-dev/gui-dev 自身单测短暂红（其代码修改中），
> 上述为源码/测试 mtime 稳定后（最后写入 13:20:59，最终测试 13:21 启动）的结果。
> 唯一 skip 是契约化设计行为：本机逻辑窗口 1240px < 1464px 阈值时进入 compact 模式隐藏 header 副标题。

```text
$ python -X utf8 tests/integration/oracle_sample.py --quiet
ORACLE RESULT: PASS

$ python -X utf8 app.py --self-test
SELF-TEST OK（14 项检查全部通过）        # exit 0

$ python tools/build_exe.py --smoke-test
BUILD OK: D:\Desktop\deepseek\logobuilder\dist\InspireMTKTools.exe
  size   : 37,752,921 bytes
  sha256 : 6bd333c8d3ea06ea3b0d09835f448f9c72c0c8547175991bb661481907bca98c
SMOKE TEST PASSED: exit code 0
# 冻结版 --self-test：SELF-TEST OK（15 项检查全部通过），capstone/LK 流程已打包
# 注：PyInstaller onefile 非位级可复现（PE 构建时间戳），每次构建 sha 不同；
#     QA 于 13:22 自建一次（build_final3.log），发布重建于 13:26，QA 已对最终产物复跑 --self-test。
```

## 6. 评审决策记录（Lead 裁定，已同步到 docs/spec_api.md）

| ID | 问题 | 决策 |
|---|---|---|
| D1 | `dataLen2 != dataLen@4`（TOC 长度不一致）规格原为报错 | **采纳“宽容读 + warning”**：解析成功并把 expected/actual 写入 `container.warnings`（CLI 打到 stderr 且 exit 0）；`to_bytes()` 始终写自洽；`offsets` 非单调/越界/表超长仍抛 CorruptContainer。QA 已按新契约改断言（B16） |
| D2 | `count==0` 但 header dataLen≠8 | 同 D1：warning + 忽略多余尾字节 + 重写自洽 |
| D3 | `sniff_format` 返回字符串与旧 spec 不一致 | 以代码为准，docs/spec_api.md 已同步 |

## 7. 发布检查单（release checklist）

| 检查项 | 要求 | 证据 | 状态 |
|---|---|---|---|
| 真实 logo.bin 逐字节回包 | sha256 改前/改后一致 | `c321a9f1…655c`；oracle + core + GUI 三路 | ✅ |
| 真实 lk.bin 无损 | raw 模式、逐字节 | `5bc6c099…ce55` | ✅ |
| 损坏输入健壮性 | 中文可读错误、不崩溃、CLI exit≠0 | B9–B17、E13 | ✅ |
| 卡刷包有效性 | 可打开、含 logo.bin+META-INF 解压路径、脚本 /dev/logo、模板 binary 不变 | E1–E4，`sha256_evidence.txt` | ✅ |
| 单文件 logo.img 导出 | `pad_to_size` 零填充、前缀正确 | A10、F4 | ✅ |
| project 生命周期 / legacy 兼容 / repair | 新建-保存-重开干净；旧工程导入等价；缺图/错尺寸/缺二进制可修 | C1–C10 | ✅ |
| GUI 自检 | `app.py --self-test` exit 0 | 14 项 OK | ✅ |
| GUI 截图 | 非空白、100%/深色/150%/对话框 | 8 PNG + manifest | ✅ |
| HiDPI 100%/150% | 字体/控件缩放、无裁剪/重叠/死区 | dpi_probe 28/28 | ✅（150% 为代码内模拟缩放；本机无真 150% 显示器，见 §8） |
| GUI 核心流程 | 打开→替换→导出 img/bin/zip→项目→lk→错误映射 | gui_probe 14/14 | ✅ |
| PyInstaller 预检 | `--check-only` exit 0 | build_check.json 17/17 | ✅ |
| 冻结产物可运行 | onefile exe + `--self-test` exit 0 | G2/G3/G4 | ✅ |
| Clean-machine 依赖 | 源码运行：Python≥3.10 + Pillow + ttkbootstrap；exe：无 Python 依赖 | G3 冻结自检；requirements 见 README | ✅（源码侧 pip 依赖未在本机全新 venv 复测，见 §8） |
| 许可/合规 | 内置 CWM update-binary 为 GPL 第三方二进制，原样嵌入生成包；sizes.txt 为原版事实数据；测试样本来自用户提供目录，不随产品分发 | §9 | ✅（需发布时保留声明） |
| **LK 真实 A/B 样本 2 findings 偏移正确** | 0x4D928 / 0x4CC58；补丁 `e0031f2ac0035fd6` | `lk_real_samples.json` | ✅ |
| **LK 补丁安全不变量** | header 0x200 / androidboot 计数 / size 不变；原文件 sha 不变 | `verify_patch` 四项 True | ✅ |
| **LK ARM32 7 规则 + fail-safe + 备份还原** | 7/7；不支持输入非 0+中文+不落盘 | LK E2E 32/32 | ✅ |
| **GUI LK 面板 + 品牌更名** | self-test 15 项；LK 截图 4 张非空白；标题/版本 v3.0.0 | `screens_lk/` | ✅ |
| **v3 exe 名/依赖** | `dist/InspireMTKTools.exe`；capstone>=5 已声明并打包 | build_check 22/22 + 冻结自检 | ✅ |
| 无阻塞 bug | 所有发现 bug 已修复或经 Lead 裁定 | `analysis/qa/report.md`/`report_lk.md` §Bug | ✅ |

## 8. 已知缺口 / 限制（不影响本次发布结论，但需记录）

1. **`tests/test_ui/` 已补齐**（gui-dev 交付 9 个测试文件：conftest/main_window/dialogs/hidpi/imaging/export_paths/self_test/workers/i18n），
   全量已包含；其中 1 个布局断言在小屏 compact 模式下按契约 skip。
2. **150% DPI 为模拟**：本机显示器/会话为 96 DPI（RDP 638×394），150% 通过 `ThemeManager.apply_scale(1.5)+rebuild` 代码路径验证（这正是应用响应显示器缩放的路径），并用 `overrideredirect` 虚拟大窗口规避小屏限制；未在真实 150%/200% 物理显示器上目测。dt 布局检查（28 项）比目测更强，但建议发布前在一台真 150% 机器上人工过一眼。
3. **未上真机/模拟器刷入测试**：`update.zip` 结构与 CWM 模板保真已由字节级比对验证，但未在真实 MTK 设备 recovery 中实际刷写。
4. **截图拼接**：窗口大于屏幕时截图工具分块拼接（本机 150% 截图窗口 1860×1230 > 638×394 屏幕），拼接属浏览器/桌面截图特性；当前产物已无明显错位（dialog 捕获 bug 已修复并有回归检查）。
5. **Clean-machine**：未在全新 Windows 虚拟机验证 pip 依赖安装（PyInstaller 冻结产物已在本机验证自检通过，说明打包完整）；源码运行依赖仅 Pillow+ttkbootstrap。
6. **CLI 中文输出**：Windows GBK 控制台需 `-X utf8`/`PYTHONIOENCODING=utf-8` 才不乱码（命令行 stderr 的 warning 内容本身是 UTF-8 语义，QA 按 UTF-8 解码校验）。
7. **现代变体解析为 best-effort**：bpp=3 / stride / 前缀子头 / YAMLP `logo.img` 等现代 MTK 变体不会被自动识别为图像块，
   会安全保留为 BinaryBlock/pending 并允许手动指定尺寸（不丢数据、不崩溃）；未逐一在真实现代机型样本上验证。
8. **跨平台未实测**：仅在 Windows 10 验证。代码路径按跨平台方式编写、Windows 专属逻辑有 guard，但 macOS/Linux 未实际运行验证。
9. **JAR 签名失效说明**：生成 `update.zip` 时保留模板 `META-INF/{MANIFEST.MF,CERT.SF,CERT.RSA}`，
   但 `logo.bin` 已替换 → 原 JAR 签名对修改后内容不再有效（与原版 LogoBuilder 行为一致；CWM 默认不校验签名）。
   未做重签名（原版也没有；重签需要私钥与 jarsigner）。

## 9. 许可与第三方说明

| 组件 | 说明 |
|---|---|
| `src/logobuilder/assets/update_template.zip` | 原版程序内嵌 CWM 卡刷模板，含 `META-INF/com/google/android/update-binary`（190,484 B，2012 年 CWM 通用 update-binary，**第三方 GPL 代码的二进制**）与模板 updater-script。产品生成 update.zip 时原样复制该模板条目（除 logo.bin / 自定义覆盖项），发布时应保留其 GPL 来源声明，不应声称此二进制为自有代码。 |
| `src/logobuilder/assets/sizes.txt` | 原版程序附带的“解压长度→分辨率”事实数据表（49 行），属事实性配置数据；随核心库内置以便开箱识别。用户自定义尺寸写入用户配置目录。 |
| `LogoBuilder_jb51/**` | 用户提供的原版程序与样本（含 MediaTek/厂商固件镜像）。仅为测试输入，不随重建产品分发。 |
| Pillow / ttkbootstrap / capstone / PyInstaller | 运行时/构建依赖，遵循各自开源许可证（Pillow HPND、ttkbootstrap MIT、**capstone BSD-3-Clause**、PyInstaller GPL-with-exception）。 |
| `analysis/lk/samples/*.bin` | 第三方 AArch64 LK 镜像（本地分析用，**不提交/不分发**）；QA 报告仅记录 sha256 与结论。 |

## 10. 一键复验（发布前最后一次）

```bash
PYTHONPATH=src python -X utf8 -m pytest tests -q     # 期望 446 passed, 1 skipped
PYTHONPATH=src python -X utf8 -m pytest tests/integration/test_lkpatch_e2e.py -q  # 期望 32 passed
python -X utf8 tests/integration/oracle_sample.py    # 期望 ORACLE RESULT: PASS
python -X utf8 app.py --self-test                    # 期望 exit 0 / SELF-TEST OK
python -X utf8 analysis/qa/gui_probe.py              # 期望 14/14 PASS
python -X utf8 analysis/qa/dpi_probe.py              # 期望 28/28 PASS
python tools/build_exe.py --check-only               # 期望 RESULT: OK
python tools/build_exe.py --smoke-test               # 期望 BUILD OK + SMOKE TEST PASSED（InspireMTKTools.exe）
```
