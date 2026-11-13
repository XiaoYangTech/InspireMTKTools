# LK 修补功能规格（去 Orange/Red State 警告 + 5 秒延时 + dm-verity 警告）

> 目标用户需求：
> 1. 去除联发科（MTK）开机 "Orange State / 你已解锁 Bootloader" 警告及 5 秒等待；
> 2. 去除 dm-verity 校验失败（"Red State / Your device has failed verification / dm-verity error"）开机警告及等待时长。
> 3. 软件更名为 **亿方联发科工具箱 / InspireMTKTools**（v3.0.0）。

---

## 1. 调研结论（方法来源）

| 来源 | 许可 | 提供的信息 |
|---|---|---|
| [Hovatek 教程 thread-31664](https://www.hovatek.com/forum/thread-31664.html) | 社区教程 | 手工特征码方法：搜 `7B441B681B68012B`(A10-) / `082B`(A10+)，要求前 4 字节 `08B5????`，把函数开头改成 `push {r3,lr}; movs r0,#0; pop {r3,pc}` → 警告与 5 秒等待一起消失（该函数就是警告分发器） |
| [modlk](https://github.com/programmersd21/modlk) | MIT | 直接清零 "Orange State.../Red State..." 警告文本（只隐藏文本，不改等待） |
| [mtk-lk-warning-patch](https://github.com/justistab3-bot/mtk-lk-warning-patch) | MIT | 完整中文方法+校验：补丁 A（改分发函数，警告与延时一起消失）+ 补丁 B（清零文本，不碰 `androidboot.*`）；LK 头结构；PC 相对寻址说明；fail-safe 策略 |
| [lkpatcher](https://github.com/R0rt1z2/lkpatcher) | GPL-3.0（仅参考方法/特征码事实，不复制代码） | `patches.json` 分类特征码：`orange_state`（2 个 ARM32 特征码）、`red_state`、`dm_verity`（"强制 vbmeta 检查函数返回 0"）；替换为 `00207047`(movs r0,#0; bx lr) |
| [tfast-mtk-bootloader-tool](https://github.com/tfastdigital/tfast-mtk-bootloader-tool) | — | 真实 AArch64 LK 样本（`work_backup/lk_a.bin`，4MB 分区镜像）；其 LK 补丁面向 fastboot 解锁，未处理开机警告 |

**注意**：以上开源项目中 modlk/mtk-lk-warning-patch 的 ARM32 方案只覆盖 32 位 Thumb-2 LK；`lkpatcher` 也仅 ARM32。现代 MTK（如本仓库拿到的真实样本）LK 是 **AArch64**，需要独立逆向（本规格第 3 节）。本项目独立实现，不复制 GPL 代码；特征码字节属于事实数据，已注明来源。

---

## 2. LK 镜像结构（真实样本核实）

`lk.img` / `lk.bin`（SP Flash Tool 分区镜像，tfast 样本 `lk_a.bin`，SHA-256 记录在 `analysis/lk/`）：

```
0x000  u32  magic      = 0x58881688
0x004  u32  size       = 有效负载长度（从 0x200 起）
0x008  char[12] name   = "lk"（NUL 填充）
0x014..0x02F 其他字段
0x030  u32  ext_magic  = 0x58891689（新版 LK 镜像）
0x200  payload[size]   = 裸 LK 代码（ARM32 Thumb 或 AArch64）；size=870,352B
尾部   **不是简单零填充**：4MB 镜像的剩余空间按 16B 对齐排列了 12 个 MTK 子镜像
       （lk + cert1/cert2/bl2_ext/aee/lk_main_dtb 等，独立 RE 复核）。补丁只改 payload 内代码，
       header 与尾部子镜像保持原样。
```

- **无签名块/无校验和字段**（头部随意改 size 也不校验；本工具保持头 512 字节不变，只改 payload）。
- 也可能拿到**裸 payload 文件**（无 512 头，如从 bootloader 组合镜像里拆出）→ 工具需自动识别：有合法 MTK 头则按分区镜像处理，否则按裸 LK 处理（全文件即 payload）。
- LK 使用 **PC 相对寻址 + 字面量池**：警告字符串不会出现绝对地址引用，必须解析 `adrp/add`（AArch64）或 `ldr/add pc`（ARM32）才能定位引用代码。

---

## 3. 真实 AArch64 样本逆向结论（本项目核心发现）

样本：`analysis/lk/samples/lk_a.bin`（tfast `work_backup/lk_a.bin`，4,194,304 B，payload 870,352 B，AArch64，SHA-256 `bb2f2e6a…f873`）；`lk_b.bin`（同仓库 B 槽，4,194,304 B，SHA-256 `f4ecd3ee…a6e4`，与 A 槽仅 2,955 字节差异，**警告函数偏移完全相同**，可用作交叉验证）；`lk_a.unlocked.bin`（该工具刷 fastboot 解锁用的 A 槽改版，SHA-256 `ba5c57eb…518f`，仅作参考）。样本仅本地分析、不随仓库分发（.gitignore）。
用 capstone 扫描 `ADRP+ADD` 字符串引用（payload 映射到地址 0 时引用目标 == payload 偏移），得到 3 个关键函数：

### 3.1 开机状态警告分发器（Orange + Red State + 5 秒）

`payload 0x4D728`（文件 `0x4D928`），调用者 `0x4D21C/0x4D22C`：

```
04d728: stp x29,x30,[sp,#-0x20]!       ← 函数入口
04d72c: str x19,[sp,#0x10]
04d730: mov x29,sp
04d734: adrp x8, #0xc7000
04d738: ldr  w8,[x8,#0xdc8]            ← 读取 boot state 全局变量
04d73c: cmp  w8,#1  ; b.eq 0x4d784     ← 状态 1/3 走 Red 路径（实测）
04d744: cmp  w8,#3  ; b.eq 0x4d784
04d74c: cmp  w8,#2  ; b.ne 0x4d7dc     ← 状态 2 走 Orange 路径（实测）
       状态 2  → 打印 "Orange State

" + "OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.
"
       状态 1/3→ 打印 "Red State

" + "Device Cannot Boot. Enter Repair Mode." + "Dismiss After 5 Seconds.
"
04d7c8: bl   0x39b84                   ← print/flush
04d7cc: mov  w0,#0x4b40 ; movk w0,#0x4c,lsl#16   ← w0 = 0x4C4B40 = 5,000,000 µs = 5 秒
04d7d4: bl   0x5f8b0                   ← 延时函数（udelay/mdelay）
04d7dc: ...
04d7e0: mov  w0,wzr                    ← 返回 0
04d7e4: ldp  x29,x30,[sp],#0x20 ; ret
```

**补丁规则**：函数入口 8 字节改为

```
mov w0, wzr    ; 2A 1F 03 E0 → LE 字节 E0 03 1F 2A
ret            ; D6 5F 03 C0 → LE 字节 C0 03 5F D6
```

→ 整个函数立即 `return 0`：**Orange/Red 文本 + 5 秒等待一起消失**；原函数正常路径末尾本来就 `return 0`，调用方语义不变。

### 3.2 dm-verity 警告函数（含约 3.2 秒长等待）

`payload 0x4CA58`（文件 `0x4CC58`），调用者 `0x4D254`：

```
04ca58: stp x29,x30,[sp,#-0x20]!
04ca64: mov w19,w0
04ca68: add x0,x29,#0x1c ; str wzr,[x29,#0x1c]
04ca70: bl  0x4cc74                  ← dm-verity 检查
04ca74: cmp w19,#2 ; b.eq warn
04ca7c: ldr w8,[x29,#0x1c] ; cmp w8,#1 ; b.ne skip
warn:   打印 "dm-verity corruption

" / "Your device is corrupt.
" /
        "It can't be trusted and may not work properly.
" / "Device will continue to bootup in %ds
"
        mov w0,#0x86a0 ; movk w0,#1 → 0x186A0 = 100,000 µs，**实测循环调用 30 次 = 3.0 秒**
        然后打印 "tran_dm_fix=1" / "Device occur dm vertity corruption"
04cc5c: ldr x19,[sp,#0x10] ; ldp ; ret   ← 无返回值约定（void）
```

**补丁规则**：函数入口同样改为 `mov w0,wzr; ret`（8 字节）→ dm-verity 警告与 **3.0 秒**等待全部跳过。

> 注意：该函数尾部还会调用 `clear_dm_verity_status` 并向 kernel cmdline 追加 `tran_dm_fix=1`；
> 提前 return 会一并跳过（这是"仅警告路径"的副作用，可能影响厂商自修复标记）。如设备异常，
> 可只用 `--categories orange_state` 单独打橙色警告补丁，或自行评估 `tran_dm_fix` 依赖。

### 3.3 通用 AArch64 检测算法（工具实现，不写死偏移）

1. 定位警告字符串（payload 内偏移）：`Orange State`、`Red State`、`dm-verity`、`dm_verity`、`Your device has been unlocked`、`Your device has failed verification`、`5 seconds`。
2. 线性反汇编 AArch64（capstone，容忍数据段）：找 `adrp Xd, #page` 后 1~3 条指令内 `add Xd, Xd, #imm`（或 `add Xn,...` + `mov Xm,Xn`），计算 `target = page + imm`；因为 payload 被映射到 0 且 LK 链接基址页对齐，`target == 字符串 payload 偏移 + delta(≤64)` 即为命中。
3. 由命中点向前（≤0x400B，4 字节对齐）找函数入口：`stp x29,x30,[sp,#-imm]!` 或 `sub sp,sp,#imm`。
4. 分类：
   - 同一函数同时引用 `Orange State` 与 `Red State`（或含 state cmp 链 `cmp w?,#1/#2/#3` 且含 5,000,000/5,000 延时常量）→ **boot-state 分发器**。
   - 函数引用 ≥2 条 `dm-verity`/`dm_verity` 字符串、且不含 `Orange State` → **dm-verity 警告函数**。
   - 同时命中：置信度高；仅字符串无结构特征：中置信度，需用户确认；找不到：FAIL-SAFE 不提供自动补丁。
5. 生成 `Finding`：`category`（`orange_state` / `dm_verity` / `red_state` / `warning_text`）、`arch`、`offset`（文件绝对偏移）、`function_start`、`confidence`、`evidence`（字符串引用位置、函数头/尾部反汇编、延时常量）、`before_bytes`/`after_bytes`。
6. 应用补丁 → 校验（见第 5 节）。

> 该算法已在真实样本上验证：分发器 `0x4D728`、dm-verity 函数 `0x4CA58` 均被识别；补丁后反汇编为 `mov w0, #0; ret`。

### 3.4 ARM32 Thumb-2 LK（老机型）规则库

已知特征码 → 补丁字节（替换特征码前 4 字节；来源见第 1 节，均为社区公开特征码事实）：

| 类别 | 特征码（hex） | 补丁（hex） | 说明 |
|---|---|---|---|
| orange_state | `08b50a4b7b441b681b68022b` | `00207047` | A10+ 分发器（push{...}; ldr; add pc; ldr; ldr; cmp） |
| orange_state | `08b50e4b7b441b681b68022b` | `00207047` | A10+ 变体 |
| orange_state | `08b50a4b7b441b681b68012b` | `00207047` | Android 10 以前 |
| orange_state | `08b50e4b7b441b681b68012b` | `00207047` | 变体 |
| red_state | `f0b5002489b0` | `00207047` | 验证失败警告函数 |
| dm_verity | `30b583b002ab0022` | `00207047` | dm-verity/vbmeta 检查函数 |
| dm_verity | `022800d0704738b53224e0f7` | `00207047` | dm-verity 变体 |

- `00207047` = `movs r0,#0; bx lr`（Thumb）。
- 也可用第 1 节 "补丁 A" 变体：保留 push/pop 的 `08B5 0020 08BD` 方案。本项目统一用 `00207047`（与 lkpatcher 一致，更短）。
- 匹配策略：**精确字节串搜索**；要求命中次数 ==1（多个命中 → 列候选让用户确认）；找不到 → fail-safe。

### 3.5 警告文本清零（可选补丁 B）

- 若用户勾选"同时清空警告文本"：把真实样本中的警告文本区间填 0x00（不改变长度与偏移，不影响代码）。内置文本键至少包括：
  `Orange State`、`Red State`、`OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.`、
  `Device Cannot Boot. Enter Repair Mode.`、`Dismiss After 5 Seconds.`、
  `dm-verity corruption`、`Your device is corrupt.`、`It can't be trusted and may not work properly.`、
  `Device will continue to bootup in %ds`、`Device occur dm vertity corruption`。
  （文本仅作"可选美化"，主补丁是 M1 改函数入口。）
- **绝不修改**：`androidboot.verifiedbootstate`、`androidboot.veritymode`、`androidboot.atm`、`androidboot.*` 等内核启动参数（工具会在前后对比这些串的出现次数，若变化 → 拒绝应用）。

---

## 4. 核心库 API 契约（`src/logobuilder/core/lkpatch.py`）

```python
MTK_MAGIC = 0x58881688
LK_EXT_MAGIC = 0x58891689
HEADER_SIZE = 0x200

@dataclass
class LkImage:
    path: Path | None
    data: bytes                 # 原始完整文件
    header: bytes | None        # 512B MTK 头；裸 payload 时为 None
    payload: bytes              # LK 代码
    payload_offset: int         # 0x200 或 0
    is_container: bool
    sha256: str
    name: str                   # header name 字段（"lk" 等）

@dataclass
class LkFinding:
    category: str               # "orange_state" | "red_state" | "dm_verity" | "warning_text"
    arch: str                   # "arm32-thumb" | "aarch64"
    kind: str                   # "signature" | "heuristic"
    offset: int                 # 文件绝对偏移（要改写的起点）
    function_start: int | None  # 文件绝对偏移
    before: bytes
    after: bytes
    confidence: float           # 0..1
    evidence: list[str]         # 人话证据（字符串引用、延时常量、函数头尾反汇编）
    selected: bool = True

@dataclass
class LkWarningString:
    offset: int                 # 文件绝对偏移
    text: str

@dataclass
class LkScanReport:
    image: LkImage
    arch: str
    findings: list[LkFinding]
    warnings: list[LkWarningString]
    guard_params: dict[str, list[str]]
    already_patched: bool
    verdict: str                # "supported-arm32-thumb" | "supported-aarch64" | "already-patched" | "unsupported" | "pending"
    notes: list[str]

def parse_lk_image(data: bytes, path: Path | None = None) -> LkImage: ...
def detect_arch(image: LkImage) -> str: ...
def scan(image: LkImage) -> LkScanReport: ...
def apply_patches(image: LkImage, report: LkScanReport, *,
                  categories: Iterable[str] | None = None,
                  clear_warning_text: bool = False,
                  text_ranges: list[tuple[int, int]] | None = None) -> tuple[bytes, list[str]]: ...
    # 返回 (新文件字节, 变更日志)；变更日志每条含类别/偏移/before->after
def verify_patch(image: LkImage, new_data: bytes, report: LkScanReport) -> dict: ...
    # {size_same, header_same, guard_counts_same, changed_ranges, disasm_after, residual_warnings, patch_ok}
def backup_path(path) -> Path; patched_path(path) -> Path
def save_backup(image, backup_dir=None) -> Path
def restore_from_backup(backup_path, target_path) -> Path
def load_custom_rules(path) / DEFAULT_ARM32_RULES: list[dict]
```

CLI 子命令（追加到 `logobuilder.core.cli`）：
```
lk-info   <lk.img>                        # 头/架构/警告串/可打补丁结论/候选列表
lk-patch  <lk.img> [-o out] [--in-place] [--dry-run] [--categories orange_state,dm_verity]
         [--clear-text] [--backup-dir DIR]
lk-verify <orig.img> <patched.img>
lk-restore <backup_path> [-o target]
```
退出码：0 成功；1 业务错误；2 参数错误；不支持/无候选 → 非 0 + 中文原因。

---

## 5. 校验 / 安全规则（必须实现）

1. **fail-safe**：找不到特征码或结构校验失败 → 不写文件，返回"不支持"结论与原因。
2. **备份**：写入前默认生成 `<name>_original_backup.img`；`--in-place` 必须先备份成功。
3. **头不变**：前 0x200 字节必须逐字节不变（LK 头含 size/name/ext magic）。
4. **内核参数不变**：`androidboot.*` / `verifiedbootstate` / `veritymode` / `atm` 前后出现次数必须相同，否则拒绝。
5. **补丁后校验**：
   - 在补丁点反汇编确认 `mov w0, #0; ret`（AArch64）或 `movs r0,#0; bx lr`（ARM32）；
   - 差异区间只落在预期补丁/文本区间；
   - 文件大小不变；SHA-256 输出。
6. **A/B 双槽提醒**：若同目录存在 `lk2.img`/`lk_b.bin` 等，提示两个槽位都要刷（只刷一个可能从另一槽启动，警告仍在）。
7. **文档提示**：刷 LK 有风险，先备份；优先用 `fastboot flash lk`；需要解锁 seccfg（mtkclient）等前置条件。

---

## 6. GUI 设计要求（亿方联发科工具箱 / InspireMTKTools）

1. **品牌更名**：窗口标题、Logo 区、关于/帮助、README、exe 名称 = `InspireMTKTools`，中文名"亿方联发科工具箱"，版本 3.0.0；核心包名内部保持 `logobuilder`（避免破坏既有 API/测试），显示层全部改为新名字。
2. 主界面新增 **"LK 修补"** 入口（工具栏/Tab 均可），面板：
   - 打开 LK 文件（单文件/文件夹批量，自动识别 `lk*.img/bin`）
   - 扫描结果：文件信息（大小/头 magic/size/name/ext magic/SHA-256）、架构、verdict 徽章、候选补丁表（类别/偏移/置信度/证据摘要）
   - 警告文本列表、内核参数保护列表
   - 勾选项：`橙色/红色警告+等待`（orange_state）、`dm-verity 警告+等待`（dm_verity）、`同时清空警告文本`
   - 按钮：`预览补丁`（dry-run：diff 区间 + 补丁前后反汇编）、`应用补丁`（生成 `_patched` 文件 + 备份）、`校验结果`、`还原备份`、`刷写说明`
   - A/B 双槽提醒、危险操作确认弹窗、日志面板
3. 全部中英文（沿用现有 i18n 机制）。
4. 测试与截图：新增 `tests/test_ui/test_lkpatch_ui.py`；截图 `analysis/ui_screenshots/lk_patch_*.png`。

---

## 7. 测试与验收

单元/集成测试（`tests/test_core/test_lkpatch.py`、`tests/test_ui/`）：
1. 合成 ARM32 LK 分区镜像（512 头 + 填充 + 嵌入特征码 + 警告字符串）→ 默认规则命中、补丁字节正确、头不变、备份生成、dry-run 不落盘。
2. 合成 AArch64 LK（构造 stp 函数入口 + adrp/add 引用字符串 + cmp 链 + 5,000,000 常量）→ 启发式识别为分发器并打补丁。
3. **真实样本**（`analysis/lk/samples/lk_a.bin`，若存在）：扫描出 2 个 finding（offset 0x4D928 orange、0x4CC58 dm-verity），补丁后反汇编为 `mov w0,#0; ret`；`lk_b.bin` 同样处理；不修改样本原文件。
4. 负样本：全零文件 / 错 magic / 无警告字符串 / 已打补丁 → fail-safe 结论，不写文件。
5. 守护参数测试：补丁前后 `androidboot.*` 计数不变；人为构造"会改到内核参数"的补丁 → 拒绝。
6. 自定义规则 JSON 加载与错误处理。
7. CLI：`lk-info/lk-patch/lk-verify/lk-restore` 退出码/输出/中文错误。
8. GUI：扫描真实样本 → 表格 2 行、勾选、预览、应用、校验、还原全流程；截图。
9. 全量回归：原有 LOGO 功能测试全绿（原 369 passed 不得回归）。

验收标准：
- 真实 AArch64 样本：两个补丁点均被识别（与第 3 节偏移一致）且打补丁后反汇编正确；
- ARM32 合成样本：7 条内置规则全部可命中；
- 任何不支持输入：给出明确中文原因、不写文件；
- 产品名称/文案统一为"亿方联发科工具箱 / InspireMTKTools"；
- `python -X utf8 -m pytest tests -q` 全绿；`python app.py --self-test` 扩展 LK 流程后仍 exit 0。

---

## 7.1 独立 RE 复核结论（lk-analyst，`analysis/lk/lk_a64_analysis.md`）

- 真实镜像 4MB 由 12 个 16B 对齐的 MTK 子镜像组成（不止 payload+零填充）；补丁不影响尾部子镜像。
- 状态名表 @payload 0xACC90：`0 green / 1 yellow / 2 orange / 3 red`；分发器把 1/3 归入 Red 路径、2 归入 Orange 路径。
- 安全性证明：函数所有路径原本 return 0（分发器）/ void（dm-verity）；调用点 0x4D21C/0x4D22C/0x4D254 均忽略返回值；无分支/数据引用/指针/标记指针落入被改的 8 字节；栈平衡与 x19 callee-saved 不被破坏。
- 检测器鲁棒性：`--all --shift 0x1000`（整体布局漂移）仍 PASS；`--simulate-patch`（幂等/已补丁）PASS；ARM32 合成 `--synthetic-arm32` 13/13 PASS。
- 建议（未来加固）：若厂商把分发器内联进大函数，回溯可能误定位 → 可加"函数长度 ≤ 0x1000 + ≥2 条警告字符串引用 + cmp 链/延时常量"三重守卫。

## 8. 风险与限制（必须写进 README/帮助）

1. 刷写 LK/bootloader 有变砖风险，务必先备份；只用于**自己拥有/已获授权**的设备。
2. AArch64 补丁为启发式识别（本规格已在 1 个真实样本验证；不同厂商 LK 代码差异可能导致识别失败 → fail-safe）。
3. 现代设备还可能叠加 AVB2/dm-verity 在 kernel/vbmeta 层的校验（本工具只处理 LK 阶段警告，不等于关闭全部校验）。
4. 部分机型需要 seccfg 解锁/关闭 SBC 才能启动改过的 LK；A/B 双槽必须都刷。
5. 未在真机验证（本仓库无设备）；仅静态分析 + 合成/真实镜像验证。
6. dm-verity 补丁会跳过 `clear_dm_verity_status` 与 `tran_dm_fix=1` cmdline 追加（原逻辑仅在警告路径执行）；若设备异常可只打 orange_state。
7. 真实样本来自公开仓库的专有固件备份（**Proprietary**），仅本地分析、不随仓库分发；SHA-256 与结论已记录。
