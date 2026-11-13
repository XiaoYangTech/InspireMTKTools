# LK 引导程序修补功能 — 独立 QA 验证报告（v3.0.0）

> 功能：去除 MTK LK 的 Orange/Red State 开机警告与 5 秒等待、去 dm-verity 警告与 ~3 秒等待；
> 产品更名 **亿方联发科工具箱 / InspireMTKTools v3.0.0**。
> 规格：`docs/spec_lkpatch.md`（含 Lead 二次修正：dm-verity 30×100,000µs=3.0s；state 2→Orange、1/3→Red；真实警告文本）。
> 报告人：独立 QA（不修改 `src/`、`app.py`；只写 `tests/fixtures/make_lk_fixtures.py`、`tests/integration/test_lkpatch_e2e.py`、`tools/build_exe.py`、`docs/qa.md`、`analysis/qa/**`）。
> 时间：2026-11-13 14:15（Asia/Shanghai）

---

## 1. 最终结论

### 发布就绪：**YES（release-ready，LK 功能 + 品牌更名）**

| Gate | 结果 | 关键证据 |
|---|---|---|
| 全量回归 `PYTHONPATH=src python -X utf8 -m pytest tests -q` | **PASS — 446 passed, 1 skipped, 0 failed，exit 0**（227s；skip 为小屏 compact 设计行为） | `analysis/qa/artifacts/pytest_lk_final.log` |
| LK 集成 E2E（QA 独立编写） | **PASS — 32/32** | `analysis/qa/artifacts/lk_e2e_final.log` |
| UI 测试 | **PASS — 63 passed, 1 skipped** | `analysis/qa/artifacts/ui_tests_lk_final.log` |
| 真实 AArch64 A/B 样本扫描 | **PASS** — lk_a/lk_b 均 2 findings：`dm_verity @0x4CC58(0.95)`、`orange_state @0x4D928(0.98)` | `lk_real_samples.json` |
| 真实样本补丁 + 校验 | **PASS** — 两处均写为 `e0 03 1f 2a c0 03 5f d6`；header 0x200 不变；`androidboot.*` 计数不变；`patch_ok=True`；原文件 sha256 不变 | 同上 |
| ARM32 内置规则 | **PASS** — 7/7 特征码精确命中，补丁 `00207047` | LK E2E |
| fail-safe / 负样本 | **PASS** — 不支持输入 CLI 非 0 + 中文原因 + 不写文件 | LK E2E |
| GUI `app.py --self-test` | **PASS** — exit 0，**15 项**（含 LK flow、品牌更名） | `self_test_lk_final.log` |
| `--screenshot` | **PASS** — 12/12 非空白；LK 面板/预览/校验/刷写说明 **4/4 非空白**；标题“亿方联发科工具箱 / InspireMTKTools v3.0.0” | `screens_lk/` |
| PyInstaller `--check-only` | **PASS** — 22/22（含 branding、lkpatch-core/ui、legacy-name 检查） | `build_check_lk.json` |
| 冻结产物 | **PASS** — `dist/InspireMTKTools.exe` 37,752,921 B，`sha256=6bd333c8…a98c`，冻结版 `--self-test` exit 0（15 项，capstone 已打包） | `build_lk_final.log` / `dist/InspireMTKTools.smoke.log` |
| LOGO 旧功能回归 | **PASS** — 真实 logo.bin 仍逐字节回包 `c321a9f1…655c` | 全量 `tests/integration/test_e2e.py` |
| 阻断项（blocking） | **0 条**（开发过程中 3 个 blocking 问题已全部修复并回归） | §6 |
| 非阻断遗留项 | **6 条** | §7 |

---

## 2. 验证方法（独立性）

1. **独立合成语料**（`tests/fixtures/make_lk_fixtures.py`，12 个 fixture，QA 自写，不 import logobuilder）：
   * ARM32 分区镜像：真实 512B MTK 头（magic/size/name="lk"/ext magic@0x30）+ 7 条内置特征码 + **Lead 修正后的真实警告文本** + guard params + 0x00 分区尾部填充到 4 MiB；
   * AArch64 合成镜像：**真实指令编码**构造（stp prologue、adrp+add 字符串引用、cmp 链、movz/movk 常量的机器码由 QA 手写编码器生成并与真实样本逐字节校验）——state 映射按 Lead 修正：**state 2 → Orange 路径，state 1/3 → Red 路径**；dm-verity 函数引用 6 条真实 dm 文本、嵌入 30 次循环计数与 100,000µs 常量（3.0s）；
   * 负样本：no-signature / already-patched / bad magic / truncated / all-zero / random / raw payload；
   * manifest 记录每个文件 sha256 + **expected findings（类别 + 精确文件偏移 + 补丁字节）**。
2. **独立 E2E**（`tests/integration/test_lkpatch_e2e.py`，32 个测试）：API 级 + CLI 子进程级 + 真实 A/B 样本级。
3. **运行态验收**：`app.py --self-test`（15 步真实 GUI 代码路径，含 LK flow）、`--screenshot`（12 张）、独立布局/截图检查沿用上一轮。
4. **打包验收**：`tools/build_exe.py`（品牌/新名称）真构建 + 冻结 exe `--self-test`。

---

## 3. 真实 AArch64 样本验证（核心验收）

样本仅本地分析、不随仓库分发（`.gitignore: analysis/lk/samples/`）。

| 样本 | 大小 | SHA-256 | 架构/结论 | Findings（文件偏移/类别/置信度） | 补丁后 sha256 | 校验 |
|---|---|---|---|---|---|---|
| `lk_a.bin`（A 槽） | 4,194,304 | `bb2f2e6a6f7b7603dee3de08dc09e29dd74da49bd4cb98f20e8be648ba66f873` | AArch64 / `supported-aarch64`，already_patched=False | `0x4CC58` dm_verity 0.95；`0x4D928` orange_state 0.98 | `28589ddfe8b9ba7892b152f4b510aed667c88e9c544a96e84b0ad1f720cc6c59` | size/header/guard 不变；`patch_ok=True`；原文件 sha 不变 |
| `lk_b.bin`（B 槽） | 4,194,304 | `f4ecd3eed32f6f8ce112115af11b054de14bf01a8d6cab7d12b31bd9051aa6e4` | AArch64 / `supported-aarch64`，already_patched=False | 同上（偏移相同） | `51462a9e591270021182f79011c8b74b08302ae9ea8de6204ff877f1a8be330f` | 同上 |

补丁前后字节（两样本一致）：

```
0x4CC58: fd 7b be a9 f3 0b 00 f9  ->  e0 03 1f 2a c0 03 5f d6   (mov w0,wzr ; ret)
0x4D928: fd 7b be a9 f3 0b 00 f9  ->  e0 03 1f 2a c0 03 5f d6
```

`verify_patch()` 输出：`size_same=True, header_same=True, guard_counts_same=True, patch_ok=True`；
证据 `analysis/qa/artifacts/lk_real_samples.json`（含每条 finding 的反汇编证据、字符串引用、变更日志）。
A/B 双槽提醒在 A/B 文件同目录时由工具自动提示（真实样本目录即触发）。

---

## 4. 合成语料矩阵（12 fixtures）

| # | Fixture | 期望 | 实际 | Verdict |
|---|---|---|---|---|
| L1 | `arm32/lk_arm32_needles.img` | 7 findings（4 orange/1 red/2 dm），偏移 = 0x1200..0x1800，after=`00207047` | 完全一致，conf 0.98 | PASS |
| L2 | `aarch64/lk_aarch64_warnings.img` | orange_state@0x1200 + dm_verity@0x1A00，after=`e0 03 1f 2a c0 03 5f d6`，含真实文本引用 | 一致（conf 0.98/0.95） | PASS |
| L3 | `aarch64/lk_aarch64_dmverity_only.img` | 仅 dm_verity@0x1A00 | 一致 | PASS |
| L4 | `aarch64/lk_aarch64_raw_payload.bin` | 裸 payload（无头），偏移 0x1000/0x1800 | 一致，`is_container=False` | PASS |
| L5 | `arm32/lk_arm32_no_signature.img` | 无候选 → unsupported，fail-safe | 一致（CLI 非 0 + 中文 + 不落盘） | PASS |
| L6 | `arm32/lk_arm32_already_patched.img` | 已补丁：无候选/`already-patched` | verdict `already-patched`，不写文件 | PASS |
| L7 | `arm32/lk_arm32_bad_magic.img` | magic 错误 → 拒绝 | 一致 | PASS |
| L8 | `arm32/lk_arm32_truncated.img` | size 超出文件 → 拒绝 | 一致 | PASS |
| L9 | `aarch64/lk_aarch64_no_warnings.img` | 无警告文本 → unsupported | 一致 | PASS |
| L10 | `aarch64/lk_aarch64_bad_magic.img` | 拒绝 | 一致 | PASS |
| L11 | `negative/all_zero_4mb.bin` | 拒绝 + 中文原因 | 一致 | PASS |
| L12 | `negative/not_lk_random.bin` | 拒绝 + 中文原因 | 一致 | PASS |

Fixture 生成器自带**编码器自检**：stp/str/mov/ldp/ret/cmp/movz/movk/add/br 等 16 条编码与真实样本/规格逐字节比对通过；AArch64 fixture 的 adrp+add 引用被独立解码器验证指向 11 条真实警告文本。

---

## 5. CLI 端到端（32 项 LK E2E 内覆盖）

| 命令/场景 | 期望 | 实际 |
|---|---|---|
| `lk-info` ARM32/AArch64/真实样本 | exit 0，输出架构/结论/候选/偏移 | PASS（真实样本输出含 0x4d928/0x4cc58） |
| `lk-patch --dry-run` | exit 0，**不写** `_patched`/`_original_backup`，输入不变 | PASS |
| `lk-patch`（默认） | 写 `<name>_patched.img` + `<name>_original_backup.img`；原文件不变；header/guard 不变；补丁字节精确 | PASS |
| `lk-patch -o out.img` | 自定义输出路径 | PASS |
| `lk-verify orig patched` | exit 0，校验通过 | PASS |
| `lk-restore backup -o target` | exit 0，还原后 sha == 原文件 | PASS |
| 不支持/损坏输入（no-signature/bad-magic/truncated/all-zero/random） | exit≠0 + 中文 stderr + 无输出文件 + 输入不变 | 5/5 PASS |
| 参数缺失 | exit 2 | PASS |
| `lk-patch --in-place`（core-dev 子进程矩阵补充） | 先备份成功才覆盖 | core-dev 31 项矩阵 PASS（QA 采信其日志） |

---

## 6. 发现的问题（全部关闭）

| ID | 严重度 | 状态 | 描述 / 复现 | 处置与验证 |
|---|---|---|---|---|
| QA-L1 | **Blocker** | Resolved | AArch64 `scan()` 在 capstone 5.0.7 + Python 3.14 下抛 `CsError: … SKIPDATA mode`（`hasattr(insn,"operands")` 触发）。真实 lk_a 与合成 fixture 100% 崩溃。 | core-dev 改为只用 `id/mnemonic/op_str` 等安全属性、显式保护 operands；QA 复验真实 A/B + 12 fixture 全部通过 |
| QA-L2 | **Blocker** | Resolved | 原始真实样本被误判 `already-patched`（已补丁检测全 payload 搜 `e0031f2ac0035fd6`，真实 LK 中自然 `mov w0,wzr;ret` 极常见）→ 工具无法修补目标设备。 | core-dev 改为只在 refs 推导的候选函数入口比对补丁字节 + 无候选时仅低置信提示；QA 复验原始 A/B → `supported-aarch64`/`already_patched=False`/2 findings，已补丁 fixture → `already-patched` |
| QA-L3 | **Blocker** | Resolved | `src/logobuilder/i18n/catalog.py` SyntaxError（gui-dev 编辑 shortcuts 时未闭合字符串，line 428）→ 全量/构建/GUI 全中断。 | gui-dev 修复；QA 复验 import OK、全量 446 passed |
| QA-L4 | Minor | Resolved | v3 同步缺口：`test_core/test_api_contract.py` 版本断言仍为 2.0.0、capstone 未在 allowlist；QA 自己 `test_e2e` 版本断言过时。 | core-dev 更新测试/allowlist + docs/spec_api.md 依赖章节；QA 更新自己的断言；全量转绿 |
| QA-L5 | Info | Accepted | ARM32 verdict 字符串实现为 `supported-arm32-thumb`，spec §4 写 `supported-arm32`。 | QA 用前缀断言；建议 Lead/spec 后续统一（不阻塞） |
| QA-L6 | Info | Accepted | QA 合成 ARM32 fixture 的 payload 长度 0x8000（spec 未规定具体长度）；测试按 fixture manifest 断言。 | 无影响 |

> 开发过程中另有 core-dev/gui-dev 自身单测短暂红（并行编辑），最终全绿（详见 `pytest_lk_final.log`）。

---

## 7. 非阻断遗留项（6 条）

1. **AArch64 启发式为 fail-safe**：不同厂商 LK 代码差异可能导致识别失败 → 工具不写文件、给明确原因（这是规格要求的安全行为）。
2. **未真机验证**：仅静态分析 + 真实镜像（A/B）+ 合成镜像验证；未在真实 MTK 设备 recovery/fastboot 实刷。
3. **capstone 依赖**：AArch64 反汇编需要 `capstone>=5`（已写入 requirements.txt/pyproject.toml，PyInstaller 冻结版已打包并自检通过；缺失时需优雅降级/提示，见 core-dev 单测）。
4. **A/B 双槽**：只刷一个槽可能仍从另一槽启动并显示警告 —— 工具已提示，但需用户操作两槽。
5. **dm-verity 等待时长**：真实设备为 30×100,000µs=3.0s（Lead 修正）；QA fixture 已按此构造（循环计数 30 + 常量 0x186A0），真实样本函数入口补丁后整段跳过，无需精确模拟循环次数。
6. **第三方样本许可**：`analysis/lk/samples/`（lk_a/lk_b/lk_a.unlocked）为本地分析用第三方镜像，**不提交、不分发**；QA 报告仅记录其 sha256 与结论（`.gitignore` 已忽略该目录）。

---

## 8. 发布检查单（LK v3.0.0 部分）

| 检查项 | 状态 | 证据 |
|---|---|---|
| 真实 A/B 样本 2 findings 偏移正确（0x4D928 / 0x4CC58） | ✅ | `lk_real_samples.json` |
| AArch64 补丁字节 `e0 03 1f 2a c0 03 5f d6` | ✅ | 同上 + LK E2E |
| ARM32 7 条内置特征码 + 补丁 `00207047` | ✅ | LK E2E L1 |
| 补丁后 header 0x200 不变 / androidboot 计数不变 / size 不变 | ✅ | `verify_patch` 四项 True |
| 原始样本不被 QA 修改（sha 不变） | ✅ | `sha256_evidence_lk.txt` |
| 备份 / 还原 SHA 一致 | ✅ | LK E2E |
| dry-run 不落盘 | ✅ | LK E2E |
| 不支持输入 fail-safe（中文、非 0、不落盘） | ✅ | 5/5 |
| GUI LK 面板（扫描/预览/应用/校验/还原/刷写说明） | ✅ | self-test 15 项；截图 4 张 |
| 品牌更名（亿方联发科工具箱 / InspireMTKTools v3.0.0） | ✅ | 截图标题、i18n、`branding` check |
| exe 名称 `dist/InspireMTKTools.exe` + 旧名兼容检查 | ✅ | build_check 22/22 |
| 冻结版自检（含 LK/capstone） | ✅ | `SELF-TEST OK（15 项）` |
| 旧 LOGO 功能无回归 | ✅ | 全量 + logo sha 一致 |
| 全量 pytest 绿 | ✅ | 446 passed, 1 skipped |

---

## 9. 证据索引（`analysis/qa/artifacts/`）

| 文件 | 内容 |
|---|---|
| `pytest_lk_final.log` | 全量 446 passed / 1 skipped / exit 0 |
| `lk_e2e_final.log` | LK E2E 32/32 |
| `ui_tests_lk_final.log` | UI 63 passed / 1 skipped |
| `lk_real_samples.json` | 真实 A/B 样本逐 finding/逐字节/verify 证据 |
| `sha256_evidence_lk.txt` | 样本/补丁后/冻结 exe 的 sha256 + 依赖声明 |
| `build_lk_final.log` + `dist/InspireMTKTools.smoke.log` | 构建 + 冻结自检（15 项） |
| `build_check_lk.json` / `.log` | PyInstaller 预检 22 项（branding/LK/legacy） |
| `self_test_lk_final.log` | GUI 自检 15 项 |
| `screenshot_lk_final.log` | 截图 12 张日志 |
| `screens_lk/`（12 PNG + manifest） | 含 `lk_patch_scan/preview/verify/flash_guide.png`（4/4 非空白） |
| `tests/fixtures/generated_lk/`（+ manifest） | 12 个 LK fixture 与 expected-findings |

---

## 10. 最终判定

- **阻断项：0**
- **非阻断遗留项：6 条**（§7）
- **LK 功能验收：** 真实 A/B 双样本、ARM32 7 规则、AArch64 合成、CLI、GUI、冻结产物全部 PASS
- **产品更名：** 显示层全部为“亿方联发科工具箱 / InspireMTKTools v3.0.0”，exe 名 `InspireMTKTools.exe`
- **结论：release-ready**（建议发布前在真实设备上按“备份→fastboot flash lk→双槽”流程人工确认一次；这是唯一无法自动化的环节）
