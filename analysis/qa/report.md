# LogoBuilder v2 重建版 — 独立 QA 验证报告（Final）

> 验证对象：`D:\Desktop\deepseek\logobuilder`（Windows 10 / Python 3.14.7）
> 报告性质：独立第三方 QA（不修改产品源码；只写 `tests/fixtures/**`、`tests/integration/**`、`tools/build_exe.py`、`docs/qa.md`、`analysis/qa/**`）
> 报告时间：2026-11-13 13:15（Asia/Shanghai）
> 配套文档：测试矩阵与发布检查单见 `docs/qa.md`

---

## 1. 最终结论

### 发布就绪：**YES（release-ready）**

| Gate | 结果 | 关键证据 |
|---|---|---|
| 全量测试 `PYTHONPATH=src python -X utf8 -m pytest tests -q` | **PASS** | **369 passed, 1 skipped, 0 failed**，exit 0（129.85s） |
| 真实 `logo.bin` 逐字节回包 | **PASS** | sha256 `c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c`（改前=改后） |
| 真实 `lk.bin` raw 无损 | **PASS** | sha256 `5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55` |
| 独立 oracle（不 import logobuilder） | **PASS** | logo 19/19 + lk 11/11（`oracle_samples.json`） |
| `app.py --self-test` | **PASS** | exit 0，14 项检查（`self_test_final.log`） |
| `--screenshot` 截图 | **PASS** | 8/8 非空白，`analysis/qa/artifacts/screens/` |
| HiDPI 100%/150% 布局 | **PASS** | dpi_probe 28/28（`dpi_probe.json`） |
| PyInstaller `--check-only` | **PASS** | 17/17（`build_check.json`） |
| 冻结 exe 构建 + `--self-test` | **PASS** | 交付产物 `dist/LogoBuilder2.exe` 36,368,438 B / `sha256=18ab317e…b023b3` / `--self-test` exit 0 |
| 阻断项（blocking） | **0 条** | 全部发现项已修复或经 Lead 裁定同步规格（§6） |
| 非阻断遗留项（non-blocking） | **7 条** | 见 §6.1 / §7 |

---

## 2. 范围与方法（独立性说明）

QA 的判定不依赖开发者自测，而是四层独立证据：

1. **独立 oracle**（`tests/integration/oracle_sample.py`）：QA 自写 `struct`+`zlib` 解析器，
   **不 import `logobuilder`**，直接断言真实样本格式不变式（magic/dataLen/count/offsets/zlib 头/尺寸直方图），
   并**从头重打包**验证逐字节可达性；对 `lk.bin` 证明其 payload 不是块表并用 opaque 方式逐字节回写。
2. **合成语料**（`tests/fixtures/make_fixtures.py`）：25 个确定性 fixture（11 valid / 11 corrupt / 2 raw / 1 legacy project），
   每个带 manifest sha256 与期望解析行为；不依赖 core 即可生成，作为外部输入。
3. **黑盒 acceptance**（`tests/integration/test_e2e.py`）：真实样本、CLI 子进程（退出码 + 中文 stderr 解码）、
   project 生命周期、update.zip 结构、像素数学（RGB565 全 65536 值双射）、错误路径。
4. **运行态验收**：
   * `python app.py --self-test`（true GUI，test_mode，14 步真实代码路径）；
   * `python app.py --screenshot`（8 张窗口/对话框截图）；
   * `analysis/qa/gui_probe.py`（独立 headless 驱动真实 `LogoBuilderApp`：打开真实 logo.bin→替换→导出 img/bin/zip→项目→lk→错误映射）；
   * `analysis/qa/dpi_probe.py`（100%/150% 布局测量 + 对话框截图回归）；
   * PyInstaller onefile --windowed 真构建 + 冻结产物 `--self-test`。

---

## 3. 环境

| 项 | 值 |
|---|---|
| OS | Windows 10 10.0.19045（AMD64） |
| Python | 3.14.7 |
| 库 | pytest、Pillow 12.3.0、ttkbootstrap 2.2.7、PyInstaller 6.22.3 |
| 显示 | RDP 会话，638×394 @96 DPI（小屏；见 §7 限制） |
| 编码 | 命令统一 `PYTHONIOENCODING=utf-8` / `python -X utf8`（GBK 控制台乱码非 bug） |

---

## 4. 命令与结果

### 4.1 全量测试

```text
$ PYTHONPATH=src python -X utf8 -m pytest tests -q
SKIPPED [1] tests/test_ui/test_main_window.py:123: 小屏/虚拟缩放环境（窗口宽 1240px < 阈值 1464px），compact 模式隐藏副标题属预期行为
369 passed, 1 skipped in 129.85s
EXIT=0
```

历史过程（供追溯）：QA 首轮 integration 69/78 → 修测试预期后 78/78；13:02 全量 315 passed；
13:04 core-dev 修改期间其单测短暂红；13:07 gui-dev 新增 `tests/test_ui` 后首跑 362 passed/7 failed（新 UI 测试在小屏环境的断言问题）；
13:20 源码/测试稳定后最终全量 **369 passed, 1 skipped**（唯一 skip 为 compact 模式设计行为）。
完整输出：`analysis/qa/artifacts/pytest_final.log`。

### 4.2 独立 oracle

```text
$ python -X utf8 tests/integration/oracle_sample.py --quiet
ORACLE RESULT: PASS
```

logo.bin：19/19 检查通过 —— magic 0x58881688、tag LOGO、dataLen=2,005,959=filesize-512、
count=51、offsets[0]=212、offsets 严格递增、payload 完整覆盖、51 块全部 zlib(78DA)、
直方图 540x960x4×27 / 720x1280x4×2 / 38x54x4×10 / 135x24x4×10 / 48x54x4×1 / 135x1x4×1、
**从头重打包 sha256 == 原文件**。

lk.bin：11/11 检查通过 —— 通用 512B 头（magic、tag LK、dataLen@4=294,132=filesize-512），
但 payload 首字 `0xEA000007`（ARM B 指令）、块表自洽性失败（count 巨大），确认**不是 LOGO 块容器**；
opaque 回写逐字节一致。

### 4.3 GUI 自检 / 截图 / headless / DPI

```text
$ python -X utf8 app.py --self-test
SELF-TEST OK（14 项检查全部通过）        exit 0

$ python -X utf8 app.py --screenshot analysis/qa/artifacts/screens/main_window.png
SCREENSHOT OK（8 张）                    exit 0

$ python -X utf8 analysis/qa/gui_probe.py
GUI PROBE RESULT: PASS                   exit 0（14/14）

$ python -X utf8 analysis/qa/dpi_probe.py
DPI PROBE RESULT: PASS                   exit 0（28/28）
```

GUI probe 关键结果（真实 `LogoBuilderApp(test_mode=True)`）：
打开真实 `logo.bin` → 51 块/tag LOGO；未修改导出 sha256 == `c321a9f1…`；
替换第 0 块纯红后导出，红块像素正确且其余 50 块 raw compressed 逐字节不变；
导出 `logo.img` pad_target=2,100,000 → 大小=2,100,000、前缀==bin、尾部全 0；
GUI 生成 `update.zip` 有效（6 条目、含 `/dev/logo`、update-binary sha256 与模板一致）；
GUI 新建工程→打包 sha256==原文件→重开 51 块 dirty=False；打开 `lk.bin` raw 模式回写 sha256==原文件；
打开坏 magic 文件 → 中文 NotLogoFile 消息（含 hint），不崩溃。

DPI probe（`overrideredirect` 虚拟大窗，绕开本机 638×394 小屏 clamp）：
100% 窗口 1239×819、150% 窗口 1860×1230；字体 linespace 19→27 px；
7 个主区域全部 mapped、正尺寸、在窗内、行序无重叠；最大行间 gap 5 px（100%）/8 px（150%）；
paned 内容撑满（638 px / 981 px）；最小内容宽度达标；4 个对话框截图尺寸正确（非主窗口截错）。

### 4.4 打包

```text
$ python tools/build_exe.py --check-only --json analysis/qa/artifacts/build_check.json
RESULT: OK - build prerequisites satisfied     exit 0（17/17 PASS）

$ python tools/build_exe.py --smoke-test
BUILD OK: dist\LogoBuilder2.exe
  size   : 36,368,438 bytes
  sha256 : 18ab317e6f136b583bd2759c9565115adab1a21c7834a777155a4b0609b023b3
SMOKE TEST: dist\LogoBuilder2.exe --self-test
SELF-TEST OK（14 项检查全部通过）
SMOKE TEST PASSED: exit code 0
```

> 注：PyInstaller onefile 非位级可复现（PE 构建时间戳），QA 自建版本与发布重建版本 sha 不同（均同源码、
> 均 `--self-test` exit 0）；上表为**最终交付产物**，QA 已直接复验。

冻结产物自检证明：`--collect-all ttkbootstrap`（主题 JSON）与
`--add-data src/logobuilder/assets -> logobuilder/assets`（`sizes.txt`、`update_template.zip`）在 onefile 内解析正确；
`--windowed` 无控制台，退出码可作为 CI gate。

---

## 5. 字节级证据（sha256）

| 文件 | sha256 |
|---|---|
| 原 `LogoBuilder_jb51/.../logo.bin` | `c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c` |
| core `to_bytes()` / `save()` 回写 | `c321a9f1…655c`（相同） |
| GUI 未修改导出 `logo_unmodified.bin` | `c321a9f1…655c`（相同） |
| CLI extract→pack 回写 | `c321a9f1…655c`（相同） |
| 原 `lk.bin` | `5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55` |
| core / GUI lk raw 回写 | `5bc6c099…ce55`（相同） |
| `src/logobuilder/assets/update_template.zip` | `cc15a4c59e93b5037567e8896f4841dbb5cc68308fa40bd60cc04d579b2dfd10` |
| 模板内 `META-INF/com/google/android/update-binary` | `7a6e70ae1605188f97e624a47092a4954659b164958bbe66685de7500b21644a` |
| 生成 update.zip 内同文件（Python zipfile 与 `unzip -p | sha256sum` 双验） | `7a6e70ae…21644a`（相同，模板未被改动） |
| GUI 修改块 0 后导出 `logo_replaced.bin` | `725f79b2519d404d4250cb7e2bf5d3a2b1be8dfd9da600a01ab499c01b1e85b9` |
| GUI 导出 `logo_replaced.img`（padded 2,100,000） | `60ad57b58ec2e9d8da009fe57d1103f3f56277dc27d13a0974c6744e96db09d0` |
| GUI 生成 `update.zip` | `5f26b03e33e7c65ff739a60d0b9d0fe5c5a1143877b45e373184a6acda35ae31` |
| GUI 项目打包 `project_pack.bin` | `c321a9f1…655c`（相同） |
| `dist/LogoBuilder2.exe`（最终交付产物，QA 复验） | `18ab317e6f136b583bd2759c9565115adab1a21c7834a777155a4b0609b023b3`（36,368,438 B） |

完整列表：`analysis/qa/artifacts/sha256_evidence.txt`。

---

## 6. Bug / 问题清单（发现 → 处置）

> 严重度：Blocker=发布阻塞；Major=功能/契约；Minor=体验/工具链；Info=记录。
> 所有条目均**未由 QA 修改 src 代码**，均通过 `send_message` 报给 Lead 并由对应开发者修复。

| ID | 严重度 | 状态 | 描述与复现 | 处置/证据 |
|---|---|---|---|---|
| QA-1 | Major | **Resolved（规格裁定）** | 契约偏差：`dataLen2 != header dataLen@4` 原 spec 要求报错，实现只记 warning 并继续解析（`container.py` 的 header/TOC 边界回退逻辑）；`count==0` 且 header dataLen≠8 同样被宽容接受。复现：`LogoContainer.parse(tests/fixtures/generated/corrupt/datalen_echo_mismatch.bin)` 成功且 warnings 非空。 | Lead 裁定采纳“宽容读 + warning”（docs/spec_api.md 已更新）；QA 按新契约改断言并验证：解析成功、warnings 含 expected/actual、重写自洽、CLI exit 0 且 stderr 含“警告/dataLen”（`test_inconsistent_toc_datalen_is_lenient_with_warnings`） |
| QA-2 | Minor | **Resolved（core-dev 修复）** | `LogoProject.set_block_size()` 把 pending BinaryBlock 转 ImageBlock 后遗留 `blockN.dat`，validate 报“存在未引用的文件”。复现：unknown-size fixture → project → set_block_size(1,400x125x2) → validate。 | core-dev 已清理陈旧文件；QA 复验：stale file 删除、`validate() == []`（`tests/integration/test_e2e.py::test_project_set_block_size_unknown_block`） |
| QA-3 | Info | **Resolved（文档同步）** | `sniff_format()` 实际返回 `mtk-logo-block`/`mtk-logo-raw`/`unknown`，旧 spec 写 `mtk-logo`/`raw`/`empty`。 | Lead 决策“以代码为准”，docs/spec_api.md 已同步 |
| QA-4 | **Blocker（当时）** | **Resolved（gui-dev 修复）** | `python -X utf8 app.py --self-test` 退出码 1：self-test 走到 `ResolutionDialog` 时 `NameError: name 'remembered_choice' is not defined`（`src/logobuilder/ui/dialogs.py` `ResolutionDialog._build`）。前面 10 个自检步骤均 OK。 | 已修复；QA 复验 `SELF_TEST_EXIT=0`、14 项全过（`self_test_final.log`） |
| QA-5 | Major | **Resolved（gui-dev 修复）** | 布局死区：主窗口 paned 内容行不随窗口拉伸，paned 与 log panel 之间出现大段空白（100% 时 93 px，150% 时 172 px），窗口越大越明显。根因：`app.py` 中 `grid_rowconfigure` 的 weight 固定给了 `row == 3`，而 core 可用（无 banner）时 paned 实际在 row 2。证据：截图 `main_window.png` 可见空白带；`dpi_probe` 报告 `no-dead-vertical-space` / `paned-expands` FAIL。 | gui-dev 修复后 QA 复验：dpi_probe 28/28 PASS，最大行间 gap 5 px/8 px，paned 高度 638 px(100%)/981 px(150%) 撑满；新截图无空白带 |
| QA-6 | Minor | **Resolved（gui-dev 修复）** | `--screenshot` 中 4 个对话框截图被截成“主窗口/桌面”而非对话框（旧图全部 1255×878 且含任务栏/主窗口碎片）；`screenshot._toplevel_hwnd` 的 parent 链会把 transient/owned Toplevel 解析到 owner 主窗口。 | 已修复；QA 复验 4 个对话框截图尺寸正确（分辨率 879×559 / 替换 819×519 / 错误 719×500 / 帮助 659×539），并加入回归检查 `screenshot:dialog-artifacts-are-dialog-sized` |
| QA-7 | Info | **Resolved（开发中瞬态）** | 开发并行期内 `tests/test_core` 一度 17 failed（core-dev 正在重构/修 QA-2）；`tests/test_ui` 初期为空（无 GUI pytest 测试）。 | 最终源码稳定后 core/ui 测试均转绿；`tests/test_ui/` 已由 gui-dev 补 9 个测试文件，最终全量见 §1/§4.1 |
| QA-8 | Info | **Not blocking（环境）** | 本机为 638×394 @96 DPI 的 RDP 小屏：真实窗口会被 WM clamp，无法直接目测 150% 布局；`--screenshot` 对超屏窗口采用分块拼接。 | QA 用 `overrideredirect` 虚拟大窗做 100%/150% 几何断言（dpi_probe 28/28），并在真实 96 DPI 下跑通自检/截图/headless 全流程；建议发布前在真 150% 物理屏人工过一眼 |

**未发现但已检查的风险点**：`count` 巨大 OOM 防护、zlib 损坏降级、trailing 字节、header padding 保留、legacy `.project` 兼容、
`bpp2/bpp4` 像素换算、`update.zip` 模板保真、core 不 import GUI、CLI 中文错误、冻结资源解析。

---

## 6.1 结论口径（两类清单）

- **阻断项（blocking）：无（0 条）**
- **非阻断遗留项（non-blocking leftovers）：7 条**：
  1. 现代 MTK 变体（bpp=3/stride/前缀/YAMLP logo.img）按 opaque/pending 保留、不自动解析像素（best-effort，可手动指定尺寸）；
  2. 跨平台未实测（仅 Windows 10；macOS/Linux 未运行）；
  3. 未在真机 recovery 实际刷写 update.zip；
  4. 150%/200% DPI 真屏未目测（应用内缩放路径 + 几何断言已验证）；
  5. 重新生成 update.zip 后模板 JAR 签名（MANIFEST/CERT.SF/CERT.RSA）对修改后内容失效（与原版一致，CWM 默认不校验，未重签名）；
  6. 未在全新 Windows VM 从零 pip install 验证源码运行（onefile exe 冻结自检已通过）；
  7. Windows GBK 控制台需 `-X utf8`/`PYTHONIOENCODING=utf-8` 才不乱码（编码环境限制，非代码 bug）。

## 7. 限制与残留风险

1. **未上真机刷写**：`update.zip` 的 CWM 结构、脚本 `/dev/logo`、模板 update-binary 均按字节比对验证；但未在真实 MTK 设备 recovery 执行刷写（无设备/模拟器环境）。
2. **150% DPI 为应用内模拟**：本机 96 DPI。验证路径 `ThemeManager.apply_scale(1.5)+_rebuild_ui` 与真实显示器缩放处理一致，且几何断言强于目测；仍建议真机目测一次。
2b. **现代 MTK 变体**：bpp=3 / stride / 前缀子头 / YAMLP `logo.img` 等现代变体不会被自动识别为图像块，
    会安全保留为 BinaryBlock + pending 并允许用户手动指定尺寸（不丢数据、不崩溃）；未在真实现代机型样本上逐一验证。
2c. **跨平台**：仅 Windows 10 实测；macOS/Linux 代码路径（tkinter/ttkbootstrap/文件对话框/DPI guard）未实际运行。
2d. **JAR 签名**：生成的 update.zip 保留模板 MANIFEST/CERT.SF/CERT.RSA，但 logo.bin 已替换 → 签名失效；
    与原版 LogoBuilder 行为一致，CWM 默认不校验；如需签名校验需自行 jarsigner 重签（需私钥）。
3. **测试子集差异**：`pytest tests` 覆盖 core 单测 + QA integration + UI 单测；真实 GUI 主流程由 `--self-test` / gui_probe / 截图矩阵覆盖（自动化 UI 测试不等同真人交互，但已覆盖打开/替换/导出/错误等关键路径）。
4. **Clean-machine**：未在全新 Windows VM 从零 pip install 验证源码运行；但 onefile exe 在冻结形态下 `--self-test` 全过，说明打包完整。
5. **GBK 控制台**：CLI/GUI 中文输出在 GBK 控制台会乱码，需 `-X utf8` 或 `PYTHONIOENCODING=utf-8`；这是 Windows 控制台编码限制，非产品 bug（stderr 内容本身按 UTF-8 语义校验）。
6. **测试样本版权**：`LogoBuilder_jb51/**`（原程序 + 固件镜像样本）为用户提供物，仅作测试输入，不随产品分发。

---

## 7.1 发布检查单（勾选状态）

| 检查项 | 状态 | 证据 |
|---|---|---|
| 真实 logo.bin 逐字节回包 sha256 一致 | ✅ | `c321a9f1…655c` |
| 真实 lk.bin raw 无损回写 | ✅ | `5bc6c099…ce55` |
| 独立 oracle（不 import logobuilder）全过 | ✅ | logo 19/19 + lk 11/11 |
| 全量 pytest（core+integration+ui）绿 | ✅ | 369 passed, 1 skipped |
| 损坏输入中文错误/不崩溃/CLI 非零 | ✅ | tests + 5 个 CLI corrupt 用例 |
| update.zip 可打开、含 logo.bin+META-INF、/dev/logo、模板 binary sha 不变 | ✅ | `sha256_evidence.txt` |
| 单文件 logo.img pad_to_size 零填充 | ✅ | A10 / F4 |
| project 新建-保存-重开干净 / legacy 导入 / repair | ✅ | C1–C10 |
| `app.py --self-test` exit 0 | ✅ | 14 项 OK |
| `--screenshot` 8 张非空白（100%/深色/150%/对话框） | ✅ | `screens/` + manifest |
| DPI 100%/150% 布局无裁剪/重叠/死区 | ✅ | dpi_probe 28/28 |
| GUI 主流程（打开→替换→导出 img/bin/zip→项目→lk→错误） | ✅ | gui_probe 14/14 |
| PyInstaller `--check-only` | ✅ | 17/17 |
| onefile exe 构建 + 冻结 `--self-test` | ✅ | exe sha + smoke log |
| 许可/第三方声明（CWM update-binary GPL、样本不分发） | ✅ | docs/qa.md §9 |
| 阻断项为空 | ✅ | 0 条 |

## 8. 证据索引

| 路径 | 内容 |
|---|---|
| `analysis/qa/artifacts/pytest_final.log` | 最终全量 pytest 输出 |
| `analysis/qa/artifacts/oracle_samples.json` / `oracle_run.log` | 独立 oracle 逐项结果（含每块 offset/长度/解压长度/sha256） |
| `analysis/qa/artifacts/sha256_evidence.txt` | 全部哈希证据 + zip 列表 + updater-script 原文 |
| `analysis/qa/artifacts/build_check.json` / `build_check.log` | PyInstaller 预检 17 项 |
| `analysis/qa/artifacts/build_final2.log` / `dist/LogoBuilder2.smoke.log` | 真构建 + 冻结自检输出 |
| `analysis/qa/artifacts/self_test_final.log` | `app.py --self-test` 14 项 OK |
| `analysis/qa/artifacts/gui_probe_final.log` / `gui_probe/gui_probe_summary.json` | GUI headless 验收 14 项 |
| `analysis/qa/artifacts/dpi_probe.json` | 100%/150% 布局与截图回归 28 项 |
| `analysis/qa/artifacts/screens/` | 8 张截图 + `screenshots_manifest.json`（尺寸/bbox/颜色数） |
| `analysis/qa/artifacts/gui_probe/` | GUI 实际导出的 bin/img/zip/项目等验收产物 |
| `tests/fixtures/make_fixtures.py` + `tests/fixtures/generated/manifest.json` | 25 个 fixture 的生成器与 manifest |
| `tests/integration/oracle_sample.py` / `test_e2e.py` | 独立 oracle 与 78 个 acceptance 测试 |

---

## 9. 最终判定

- **发布阻塞问题：0**
- **功能/契约问题：全部关闭**（QA-1 规格裁定、QA-2/4/5/6 修复并复验）
- **测试覆盖**：真实样本字节级 + 独立 oracle + 25 合成 fixture + 78 acceptance + core/ui 单测 + GUI self-test/截图/probe/DPI + PyInstaller 冻结自检
- **结论：release-ready**；建议发布前在真 150% 显示器与真机 recovery 各做一次人工确认（§7）。
