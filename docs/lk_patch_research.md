# LK 引导程序修补调研报告：去除 MTK Orange/Red State 与 dm-verity 开机警告及等待

> 产品：**亿方联发科工具箱 / InspireMTKTools v3.0.0**（核心包名内部仍为 `logobuilder`）
> 文档性质：调研 / 参考文档（用于让用户与开发者**核实我们的方法**，不是操作手册）
> 调研日期：2026-11-13 ｜ 撰写：oss-researcher ｜ 上游规格：[docs/spec_lkpatch.md](spec_lkpatch.md)
> 本次调研同时核对了：本地一手资料 `analysis/external/`、GitHub 元数据快照 `analysis/external/ghsearch*.json`、真实样本 `analysis/lk/samples/lk_a.bin`、并**独立抓取**了 Hovatek 原教程。

## 证据分级约定

| 标记 | 含义 |
|---|---|
| ✅ 已验证 | 本机拿到一手内容并独立复核：README/源码/样本字节/反汇编/网络页面抓取成功 |
| 🟡 社区二手 | 项目 README、教程页面的**陈述**，我们读到了原文，但未做真机实验 |
| ❓ 未验证 | 页面打不开、只有搜索摘要、或仅有元数据没有内容；**不要当结论引用** |

复现方式（本文所有"本项目验证"均指此环境）：`capstone 5.0.7` + Python 3.14，对 `analysis/lk/samples/lk_a.bin` 做 SHA-256 / 头部解析 / 全量字符串统计 / AArch64 反汇编；GitHub 星数来自 `analysis/external/ghsearch1.json`、`ghsearch2.json` 快照（2026-11-13 抓取，会随时间变化，引用需注明快照日期）。

---

## 1. 摘要与结论：有没有"一键去警告"的现成开源方案？

**有，但都只覆盖 32 位 ARM（ARM32 Thumb-2）的 LK；没有任何公开开源项目覆盖真实 AArch64 LK 的去警告。** 这是本项目的差异化点，也是必须自己逆向的原因。

| 项目 | 语言 / 许可 | 快照星数 | 最后推送 | 支持架构 | 实际能力 | 可信度 |
|---|---|---|---|---|---|---|
| [justistab3-bot/mtk-lk-warning-patch](https://github.com/justistab3-bot/mtk-lk-warning-patch) | Python / MIT | 8★ | 2026-09-25 | ARM32 Thumb | **最完整的开源方案**：中文 GUI（tkinter）+ CLI + 核心库；补丁 A（改警告分发函数→警告与 5 秒一起消失）+ 补丁 B（清零警告文本）；自动校验/备份/还原；实测 MT6739/Android 9 镜像 | ✅ README.zh-CN/lk_core.py 已读；🟡 真机效果未复核 |
| [programmersd21/modlk](https://github.com/programmersd21/modlk) | Python / MIT | 10★ | 2026-06-11 | ARM32（Hovatek 特征码） | **只清零警告文本**（不消除 5 秒等待）；顺带自动下载 platform-tools 并 `fastboot flash lk` | ✅ 源码已读；🟡 "改文本"行为的副作用未验证 |
| [R0rt1z2/lkpatcher](https://github.com/R0rt1z2/lkpatcher) | Python / GPL-3.0 | 415★ | 2026-11-03 | ARM32 Thumb（规则均为 Thumb 字节） | 通用 LK 镜像修补框架：`patches.json` 分类 needle（orange_state/red_state/dm_verity/fastboot），统一替换为 `00207047`；还能列分区/改分区/`--cert-bypass`；**网页版 GUI 不开源** | ✅ README + patches.json 已读（**仅引用事实/特征码，未复制 GPL 代码**）；🟡 其规则语义部分未逐条实证 |
| [tfastdigital/tfast-mtk-bootloader-tool](https://github.com/tfastdigital/tfast-mtk-bootloader-tool) | Python / NOASSERTION（许可不明） | 43★ | 2026-10-26 | LK Patch & Analysis 页（解锁向） | 面向**解锁/刷写**，不是去警告；提供了真实 LK 样本（`work_backup/lk_a.bin` 等，见 §4/§6） | ✅ README + 仓库树快照已读；🟡 "真实设备样本"的具体机型未证明 |
| [nainsharma01/mtk-lk-warning-patch](https://github.com/nainsharma01/mtk-lk-warning-patch) | TypeScript / MIT | 5★ | 2026-10-13 | —（内容不符） | ❌ **仓库描述声称去警告，但快照内容实际是 "Claude Code Visualizer"（扫 `.claude/` 的 TS 可视化工具），不含任何 LK 逻辑** | ✅ 已核实内容不符，**不可信、不要引用** |
| [wangyishanjiadao/lk-universal-patch](https://github.com/wangyishanjiadao/lk-universal-patch) | 语言未知 / 无许可证 | 6★ | 2026-09-06 | 未知 | 描述为 "LK universal patch tool (MTK little kernel, norsgn + semantic patch)"，可能有"语义补丁"思路 | ❓ 仅元数据，未读内容 |
| [Maikyxd/val-protocol](https://github.com/Maikyxd/val-protocol) | Python / AGPL-3.0 | 109★ | 2026-07-21 | Motorola/MTK | LK patch 解锁 bootloader + 去 FRP（fastboot/LK cert exploit），非去警告 | ❓ 仅元数据 |
| [tfastdigital/lk-unlock-Xiaomi-devices](https://github.com/tfastdigital/lk-unlock-Xiaomi-devices) | Python / AGPL-3.0 | 3★ | 2026-08-28 | MTK | 小米 MTK 解锁（RSA token 签名/证书绕过），邻近领域 | ❓ 仅元数据 |

补充核实：`analysis/external/ts_lk/` 即上述 nainsharma01 仓库内容的本地快照——README/CLAUDE.md 是 "Claude Code Visualizer"，`src__scanner.ts` 只扫描 `.claude/agents`、`.claude/skills`；用 `lk|bootloader|logo|orange|dm-verity|mediatek` 检索只命中 `chalk` 一词（因为含 "lk"）。**结论：ts_lk 与本任务无关，不能作为 LK 修补资料。**

### 一句话结论

1. **方法层面**：社区已经证明"警告文本 + 5 秒等待是同一个函数（警告分发器）"，改成 `return 0` 可一并消除（Hovatek 教程 ✅ 抓取成功 + 本项目 AArch64 反汇编独立验证 ✅，见 §4）。
2. **工程层面**：ARM32 有成熟特征码与两个 MIT 实现可参考；**AArch64 没有公开方案**，现有工具的 needle 在真实 AArch64 样本里命中数为 0（本项目验证：`08b50a4b...022b`、`7b441b681b68012b`、`00207047` 等全部 0 次）。
3. **风险层面**：去警告 ≠ 关闭校验。改 LK 只影响 bootloader 阶段的提示与等待；AVB2/dm-verity 的校验链仍然存在，Red State 的真砖仍需回刷官方签名固件（Hovatek 原文 ✅）。

---

## 2. 方法分类（M1–M5）

### M1 改"警告分发函数"入口（推荐；本项目采用）

- **原理**：LK 中有一个状态分发函数：读取 boot state 全局变量 → 按状态打印 Orange/Red 警告 → 调用延时（5 秒）→ 返回。把函数入口改成直接 `return 0`，整段打印与等待都被跳过；而正常 green 状态原本也是返回 0，调用方语义不变。
- **适用架构**：ARM32 Thumb（社区特征码方案）；AArch64（本项目逆向，见 §4）。
- **工具**：十六进制编辑器 + 已知特征码（Hovatek）；或 justistab3-bot 的 `LKTool.py`（ARM32 自动化）。
- **优点**：一次改动同时去掉**文本 + 等待**；不依赖警告字面文本（厂商换文案也有效）；改动仅 4–8 字节，可精确校验。
- **缺点/风险**：必须先正确识别函数；识别错会把重要逻辑改成 `return 0`（因此必须 fail-safe + 人工确认）；不同厂商/版本函数布局不同。
- **本项目**：**采用**。ARM32 用内置规则 + 命中唯一性要求；AArch64 用启发式定位 + 置信度 + 预览 + 校验（见 §4/§9）。

### M2 只清零警告文本

- **原理**：把 `Orange State` / `Red State` / `dm-verity ...` 等字符串内容改成 `\x00`（保持长度与偏移不变，不破坏代码段）。Hovatek 教程的"隐藏"步骤即此；modlk 用整句精确匹配后清零。
- **适用架构**：任意（与指令集无关）。
- **优点**：改动最保守，不碰代码；易回滚。
- **缺点**：**不消除 5 秒等待**；强依赖文案，厂商改写文案即失效（本项目验证：真实样本文案已变为 `OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.`、`Device Cannot Boot. Enter Repair Mode.`，2019 年教程里的 "Your device has been unlocked..." 在样本中命中 0 次）；若误清 `androidboot.*` 内核参数会改变 Android 行为。
- **本项目**：作为**可选附加补丁 B**（`--clear-text`），并强制校验 `androidboot.*` 前后计数不变。

### M3 只改延时参数

- **原理**：把分发器里的 `mdelay(5000)` 常量改 0，或把 `bl 延时函数` 改成 `nop`（AArch64 上即把 `bl` 换 `nop`），只去等待、保留警告。
- **适用架构**：任意（需定位延时调用点）。
- **优点**：保留警告（符合"既要提示又要快"的少数需求）。
- **缺点**：延时函数（样本中 `0x5F8B0`）是共享的 `udelay/mdelay`，直接 patch 函数体会拖垮其它超时逻辑；只 patch 调用点则需精确定位，收益低。
- **本项目**：**不单独实现**（M1 已同时消除等待；单独保留警告的需求可在 M2 之外另行评估）。

### M4 改内核 cmdline（不推荐）

- **原理**：改 `androidboot.verifiedbootstate=*`、`androidboot.veritymode=*` 或追加 `androidboot.*` 参数，试图让 Android/内核忽略状态。
- **适用架构**：任意，但**方向错误**：LK 的警告在构建 cmdline 之后/之前打印，改 cmdline 通常拦不住 bootloader 自己的屏幕输出；而且可能让 Android 侧状态机与真实状态不一致。
- **优点**：（宣传上）看起来"改配置不改代码"。
- **缺点**：误导性强、破坏性大；对开机第一屏无效。**Hovatek 教程与两个开源项目均未采用此法**（✅ 已读）。
- **本项目**：**明确不做**；并且把这些参数列为受保护对象（只读校验，绝不修改）。

### M5 seccfg / bootloader 解锁（不相关，但常是前置条件）

- **原理**：解锁 bootloader（mtkclient 清 seccfg、或 fastboot 解锁），使设备接受"被改过的 LK"。
- **适用架构**：MTK 全系；SBC（Secure Boot Config）设备可能不支持（lkpatcher README ✅）。
- **与本需求关系**：**不消除任何警告**，但很多机型在锁定的 seccfg 下无法启动被修改的 LK，因此属于刷写前提（见 §6）。
- **本项目**：不实现解锁功能，只在文档/对话框提示前置条件与风险。

---

## 3. ARM32 Thumb 细节（社区已知事实）

### 3.1 Hovatek 原教程方法（✅ 抓取成功，2026-11-13）

来源：[Hovatek — How to fix MTK orange, yellow & red state warnings](https://www.hovatek.com/forum/thread-31664.html)（发帖 2019-12-24，最后修改 2024-06-24；作者 hovatek）。

教程分两步：

1. **隐藏警告文本**：HxD 搜 `Red State` 或 `Orange State`（Text-string 模式），把要隐藏的整段字符逐个改成 `00`（长度不变）。
2. **去掉 5 秒等待**：HxD 切 Hex-values，按 Android 版本搜索：

| 类别 | 搜索特征码 | 备注 |
|---|---|---|
| Android 10 以下 | `7B441B681B68012B` | 教程原文 |
| Android 10 及以上 | `7B441B681B68022B` | 教程原文 |

   命中后**必须**确认命中位置前 4 字节为 `08B5****`（原文：the 8 values before what was found is `08B5****`；命中的值前面不是 `08B5` 就继续找下一个）。然后把命中的前缀 + 特征码共 **24 个 hex 字符（12 字节）** 替换为：

| Android 版本 | 替换后 24 hex 字符 | 反汇编（本项目用 capstone Thumb 解码验证 ✅） |
|---|---|---|
| 10 以下 | `08B5002008BD1B681B68012B` | `push {r3,lr}`; `movs r0,#0`; `pop {r3,pc}`; 之后残留的 `ldr r3,[r3]` 等成为不可达代码 |
| 10 及以上 | `08B5002008BD1B681B68022B` | 同上 |

   即 `08 B5 00 20 08 BD` 覆盖前 6 字节（原 `push {r3,lr}` + 之后两条指令的一半），相当于"立刻 `return 0`"。教程明确建议：**改完后用 fastboot 刷入**——若用 SP Flash Tool 刷，可能出现黑底白字的 `.........` 计数（原文 Important Notice ✅）。

教程同时给出限制（✅ 原文）：
- "The patch for removing the 5 seconds boot time delay isn't available to all Mediatek devices."（并非所有 MTK 设备都能用此法去延时）
- 出现 `dm-verity corruption ... Your device is corrupt ...` 时，正确做法是回刷官方固件（Firmware Upgrade），而不是硬改。
- Red State 需要刷官方签名固件救砖；Orange 状态循环可参考 disable dm-verity 教程（thread-32719，见 §5）。

> 说明：本机可直接打开 Hovatek 该帖（HTTP 200）。此前"本机 403"的记录来自其他环境/其他页面；XDA 与 r0rt1z2 博客仍不可用（见 §7、§8）。

### 3.2 justistab3-bot / modlk 的自动化实现（MIT，可参考思路）

- [justistab3-bot/mtk-lk-warning-patch](https://github.com/justistab3-bot/mtk-lk-warning-patch)（MIT，8★）把上述方法工程化：两套特征码 `SIG_PRE_A10=7B441B681B68012B`、`SIG_A10PLUS=7B441B681B68022B`；要求命中前 4 字节 `08B5`；12 字节替换为 `08B5002008BD`+原尾部；**补丁 B** 清零警告文本并绝不触碰 `androidboot.verifiedbootstate`、`androidboot.veritymode`、`androidboot.atm`；自动备份、校验（差异区间、内核参数计数、反汇编显示）、GUI/CLI 双入口。README 自述只在 MT6739 / Android 9 上验证（🟡）。其 README 还给出 LK 头结构（magic `0x58881688`、size@0x04、name@0x08、ext magic `0x58891689`@0x30、payload@0x200、尾部零填充），与我们的样本一致（✅ 本项目验证，见 §4.1）。
- [programmersd21/modlk](https://github.com/programmersd21/modlk)（MIT，10★）只做文本清零（把 2019 年教程的整句 `Orange State...Your device has been unlocked and can't be trusted...Your device will boot in 5 seconds...` 和 Red 版整句清零），然后 `adb reboot bootloader; fastboot flash lk; fastboot reboot`。**它不改延时**；且其整句文本在当前真实样本中不存在（见 §4.4），因此对该样本**无效**（🟡 推断，基于文本命中 0 次）。

### 3.3 lkpatcher 的规范化方案（GPL-3.0，仅引用事实）

[R0rt1z2/lkpatcher](https://github.com/R0rt1z2/lkpatcher)（GPL-3.0，415★，仍在活跃更新）把补丁做成 JSON 分类 needle → 统一替换为 **`00207047`**，capstone Thumb 解码为 `movs r0,#0; bx lr`（本项目验证 ✅）。其 `analysis/external/lkpatcher/patches.json` 快照中的类别与 needle（**字节为事实数据，GPL 代码未复制**）：

| 类别 | needle（hex） | 替换 | 作用 |
|---|---|---|---|
| `orange_state` | `08b50a4b7b441b681b68022b` | `00207047` | 警告分发器（A10+ 变体） |
| `orange_state` | `08b50e4b7b441b681b68022b` | `00207047` | 警告分发器（A10+ 变体 2） |
| `red_state` | `f0b5002489b0` | `00207047` | 验证失败警告函数 |
| `dm_verity` | `30b583b002ab0022` | `00207047` | dm-verity / vbmeta 检查函数 |
| `dm_verity` | `022800d0704738b53224e0f7` | `00207047` | dm-verity 变体 |
| `fastboot` | `2de9f04fadf5ac5d` | `00207047` | fastboot 限制（解锁向，非去警告） |
| `fastboot` | `f0b5adf5925d` | `00207047` | fastboot 限制（解锁向，非去警告） |

> 注：本地快照 `patches.json` 中 `dm_verity` 与 `orange_state` 段各有两行之间缺逗号（不是合法 JSON），属抓取/整理瑕疵；上表按逐条字面读取，**needle 事实不受影响**。另：README 示例中的 `fastboot` 补丁与网页版 GUI（不开源）说明其产品重心是解锁，而不是去警告。

### 3.4 已知 ARM32 needle 汇总与来源对照

| 来源 | 特征码 | 替换/说明 |
|---|---|---|
| Hovatek 教程（✅ 原文） | `7B441B681B68012B`（<A10）/ `7B441B681B68022B`（A10+），且前缀 `08B5****` | 12 字节 → `08B5002008BD` + 尾部 |
| justistab3-bot（MIT） | 同上两条（常量 `SIG_PRE_A10`/`SIG_A10PLUS`） | 同上 12 字节方案 |
| lkpatcher（GPL，仅事实） | 见 §3.3 表 | 4 字节 → `00207047`（`movs r0,#0; bx lr`） |
| `docs/spec_lkpatch.md` §3.4 | 在 `022b` 两条基础上补了 `08b50a4b…012b`、`08b50e4b…012b` 两条 | 属"前缀+后缀"推导扩展（🟡 本项目推导，社区教程未逐条列出这两条完整串；建议实现时按"`08B5` 前缀 + `…1B681B68012B/022B` 后缀"匹配，并在命中数>1 时让用户确认） |

**结论**：ARM32 侧不需要我们发明；把 Hovatek 的"前缀 + 特征码"约束、justistab3 的校验/备份流程、lkpatcher 的 4 字节短补丁统一进规则库即可。命中唯一性是关键安全阀。

---

## 4. AArch64 细节（本项目逆向，核心差异化）

### 4.1 真实样本与镜像结构（✅ 本项目验证）

样本：`analysis/lk/samples/lk_a.bin`、`lk_b.bin`（均 4,194,304 B，来自 [tfastdigital/tfast-mtk-bootloader-tool](https://github.com/tfastdigital/tfast-mtk-bootloader-tool) 仓库的 `work_backup/`，快照见 `analysis/external/tfast_tree.json` ✅）：

| 字段 | 值（lk_a.bin） | 验证方式 |
|---|---|---|
| 文件 SHA-256 | `bb2f2e6a6f7b7603dee3de08dc09e29dd74da49bd4cb98f20e8be648ba66f873` | Python `hashlib` ✅（与任务书 `bb2f2e6a…f873` 一致） |
| 0x000 magic | `0x58881688`（LK 头） | `struct.unpack` ✅ |
| 0x004 size | 870,352（Payload 长度，从 0x200 起） | ✅ |
| 0x008 name | `"lk"`（NUL 填充） | ✅ |
| 0x030 ext magic | `0x58891689`（新版 LK 镜像） | ✅ |
| 0x200 payload | 裸 AArch64 代码（首条指令 `d9550094` = `bl`） | ✅ |
| 尾部 | 零填充到 4 MB 分区大小；头部无签名块/校验和字段（改 payload 不影响头部校验） | ✅（🟡 "无校验"的普适性未跨机型验证） |

`lk_b.bin`（B 槽）头字段与两个警告函数代码均与 A 槽一致，SHA-256 为 `f4ecd3ee…aa6e4`（见 `docs/spec_lkpatch.md` §3：A/B 全文件约 2,955 字节差异，**警告函数偏移相同**，可作交叉验证）。tfast 仓库同时提供 `lk_a.unlocked.bin`（该工具 fastboot 解锁用的 A 槽改版，SHA-256 `ba5c57eb…518f`，本项目**未分析**，仅记录存在 ❓）。样本仅作本地分析、不随仓库分发（`.gitignore`）。

→ 工具必须支持**裸 payload**（无 0x200 头，如从组合 bootloader 镜像拆出）与**带 MTK 头分区镜像**两种输入。

### 4.2 开机状态警告分发器（Orange/Red + 5 秒）

`payload 0x4D728` / **文件 0x4D928**（本项目用 capstone 独立反汇编 ✅，节选）：

```asm
04d728  stp  x29, x30, [sp, #-0x20]!      ; 函数入口
04d72c  str  x19, [sp, #0x10]
04d730  mov  x29, sp
04d734  adrp x8, #0xc7000
04d738  ldr  w8, [x8, #0xdc8]             ; 读 boot state 全局变量（样本地址 0xC7DC8）
04d73c  cmp  w8, #1  ; b.eq 0x4d784       ; 状态 1/3 → 一条打印路径
04d744  cmp  w8, #3  ; b.eq 0x4d784
04d74c  cmp  w8, #2  ; b.ne 0x4d7dc       ; 状态 2 → 另一条打印路径
04d770  adrp x19,#0xac000 ; add #0xbea    ; 字符串引用（ADRP+ADD，PC 相对）
04d7a0  adrp x0, #0xac000 ; add #0xc28    ; → 0x0ACC28 = "Red State"
04d7b4  adrp x0, #0xac000 ; add #0xbdb    ; → 0x0ACBDB = "Orange State"
04d7c8  bl   0x39b84                      ; flush/输出
04d7cc  mov  w0, #0x4b40
04d7d0  movk w0, #0x4c, lsl #16           ; w0 = 0x4C4B40 = 5,000,000 µs = 5 秒
04d7d4  bl   0x5f8b0                      ; 延时（udelay/mdelay）
04d7dc  ldr  x19, [sp, #0x10]
04d7e0  mov  w0, wzr                      ; 正常路径返回 0
04d7e4  ldp  x29, x30, [sp], #0x20
04d7e8  ret
```

要点：
- 两条打印路径（分别打印 Orange/Red 文案）最终汇合到同一个 **5,000,000 µs 延时**（`0x4C4B40`），随后统一 `return 0`。→ **patch 函数入口 = 文本与 5 秒同时消失**，这正是社区"改分发函数"结论在 AArch64 上的实证 ✅。
- 状态值映射：反汇编显示 **1 与 3 走同一分支（引用 `Red State` 字符串），2 走另一分支（引用 `Orange State`）**。规格书 §3.1 的注释（1=Orange、2=另一红色状态、3=Red）与本次反汇编**不完全一致**；green/yellow/orange/red 的数值枚举语义待用更多样本确认（❓）。但**补丁安全性不受影响**：入口 `return 0` 会同时跳过两路打印与等待。

### 4.3 dm-verity 警告函数（含约 3 秒等待）

`payload 0x4CA58` / **文件 0x4CC58**（capstone 反汇编 ✅，节选）：

```asm
04ca58  stp  x29, x30, [sp, #-0x20]!
04ca64  mov  w19, w0
04ca70  bl   0x4cc74                      ; dm-verity 检查子函数
04ca74  cmp  w19, #2  ; b.eq warn
04ca7c  ldr  w8, [x29, #0x1c] ; cmp w8,#1 ; b.ne 0x4cc5c   ; 未失败则跳过
warn:
04caa4  adrp x0,#0xac000 ; add #0x6a3     ; → 0x0AC6A3 "dm-verity corruption"
04cab0  adrp x0,#0xac000 ; add #0x6ba     ; → 0x0AC6BA "Your device is corrupt."
04cabc  adrp x0,#0xac000 ; add #0x6d3     ; → 0x0AC6D3 "It can't be trusted and may not work properly."
04cac8  adrp x0,#0xac000 ; add #0x703     ; → 0x0AC703 "Device will continue to bootup in %ds"
04cad8  mov  w0, #0x86a0 ; movk w0, #1, lsl #16   ; 0x186A0 = 100,000 µs
04cae0  bl   0x5f8b0                      ; 该 mov/movk/bl 组合连续出现 30 次
04cc34  ...（共 30 次 ≈ 3,000,000 µs = 3.0 秒）
04cc40  bl   0x4cd44                      ; 清 dm-verity 状态
04cc44  adrp x0,#0xac000 ; add #0x72a / add #0x738   ; 收尾打印
04cc5c  ldr x19,[sp,#0x10] ; ldp ; ret     ; void，无返回值
```

- 本项目实测：延时循环 **30 次 × 100,000 µs = 3.0 秒**（✅）；规格书 §3.2 写的 "32 次 ≈ 3.2 秒" 是估算，**以本验证为准**（不影响补丁策略）。
- 规格书 §3.2 中"payload 0x6a3/0x6ba/0x6d3/0x703"实际是 `ADRP+ADD` 的 **add 立即数**；对应 payload 偏移为 `0xAC6A3/0xAC6BA/0xAC6D3/0xAC703`，文件偏移再加 `0x200`。
- 该函数**无返回值约定**（void），入口改成 `mov w0,wzr; ret` 只是提前退出，调用方不读 w0，语义安全（🟡 基于单样本观察）。

### 4.4 真实样本的警告文案（与 2019 教程不同，✅ 本项目验证）

样本内字符串（文件偏移）：

| 文本 | 文件偏移 | 说明 |
|---|---|---|
| `Orange State` | 0xACDDB | 状态标题 |
| `OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.` | 0xACDxx 附近 | 现代 Orange 文案（教程旧文案 "Your device has been unlocked…" 命中 0 次） |
| `Red State` | 0xACE28 | 状态标题 |
| `Device Cannot Boot. Enter Repair Mode.` | 0xACE4x | 现代 Red 文案 |
| `dm-verity corruption` / `Your device is corrupt.` / `It can't be trusted and may not work properly.` / `Device will continue to bootup in %ds` | 0xAC8A3 / 0xAC8XX / 0xAC9XX | dm-verity 警告 4 条（函数在 0x4CA58 引用） |
| `androidboot.verifiedbootstate=orange/yellow/red/green` | 0xACDxx 区 | **受保护的内核参数，绝不修改**（样本内 verifiedbootstate ×4、veritymode ×2、atm ×2、vbmeta ×7） |
| `Your device will boot in 5 seconds` | 0xB2AAA / 0x15776F | 属于 **download/flashinfo 模式**的另一处提示，不在本补丁范围（见 §9 边界说明） |
| `Your device will continue boot with POWER key` / `Otherwise your device will shutdown after 5 seconds` | 0x1577xx | 同上 |

**结论**：M2（清零文本）在现代样本上会失效；M1（改分发函数）才是跨代际稳定的方案。这也是本项目主推 M1 的直接证据。

### 4.5 补丁字节与校验（✅ 本项目验证）

| 架构 | 目标函数 | 文件偏移 | before（入口 8 字节） | after |
|---|---|---|---|---|
| AArch64 | 状态警告分发器 | `0x4D928`（payload 0x4D728） | `fd 7b be a9 …` | `e0 03 1f 2a c0 03 5f d6` |
| AArch64 | dm-verity 警告函数 | `0x4CC58`（payload 0x4CA58） | `fd 7b be a9 …` | `e0 03 1f 2a c0 03 5f d6` |

- `e0 03 1f 2a` = `mov w0, wzr`；`c0 03 5f d6` = `ret`（capstone 解码验证 ✅）。
- 补丁后校验：在补丁点反汇编确认 `mov w0,#0; ret`；文件大小不变；头 0x200 字节不变；`androidboot.*` 字符串计数不变；差异区间只落在目标 8 字节（或用户勾选清文本时的文本区间）。
- 样本中的 `lk_b.bin` 同样命中两个函数（同偏移）→ A/B 双槽都要处理（✅）。

### 4.6 通用 AArch64 定位算法（工具实现，不写死偏移）

1. 在 payload 内定位警告字符串：`Orange State`、`Red State`、`dm-verity`、`dm_verity`（另可加通用词，但**不要**依赖单一厂商文案）。
2. 线性反汇编（capstone，容忍数据段）：找 `adrp Xd, #page` 后 1–3 条内 `add Xd, Xd, #imm`，计算 `target = page + imm`；payload 映射到 0 且 LK 链接基址页对齐时，`target ≈ 字符串 payload 偏移` 即命中（本样本逐一验证 ✅）。
3. 由命中点向前（≤0x400 B，4 字节对齐）找函数入口：`stp x29,x30,[sp,#-imm]!` 或 `sub sp,sp,#imm`（本样本两函数入口均命中 ✅）。
4. 分类：
   - 同函数同时引用 `Orange State` 与 `Red State`（或含 `cmp w?,#1/#2/#3` 状态链 + `0x4C4B40` 5,000,000 常量）→ **状态警告分发器**；
   - 引用 ≥2 条 `dm-verity`/`dm_verity` 且不含 Orange/Red → **dm-verity 警告函数**；
   - 结构化证据齐全 → 高置信度自动勾选；仅字符串命中 → 中置信度，**必须用户确认**；找不到 → **FAIL-SAFE 不写文件**。
5. `Finding` 记录：类别 / 架构 / 文件偏移 / 函数起点 / 置信度 / 证据（字符串引用点、函数头尾反汇编、延时常量）/ before / after。

> 局限（必须写进帮助文档）：启发式只在**1 个真实样本**上验证（✅）；不同厂商 LK 的编译差异可能导致识别失败 → fail-safe 拒绝；样本的设备/机型身份未验证（❓，来自第三方仓库的 `work_backup`）。

---

## 5. dm-verity 警告与 AVB2 的关系："去警告 ≠ 关闭校验"

必须区分三层：

| 层级 | 内容 | 我们的补丁是否影响 |
|---|---|---|
| ① bootloader（LK）阶段 | 状态分发器打印 Orange/Red、dm-verity 警告并等待（本报告 §4） | **是**：警告文本与等待被跳过 |
| ② vbmeta / AVB2 校验 | 对 boot/system/vbmeta 的签名与 hash 链校验（由 bootloader 执行，失败即 Red State） | **否**：校验逻辑仍在，改 LK 只改提示分支 |
| ③ kernel dm-verity | 内核挂载 system/vendor 时的 hash 校验（失败会有 kernel 日志/dm-verity error） | **否**：与 LK 无关 |

要点与证据：
- 我们的样本里 dm-verity 警告函数在打印前**先调用检查子函数** `0x4CC74`（✅）；把警告函数入口改 `return 0` 只跳过"打印 + 等待 + 清状态提示"，**检查本身仍在别处执行**。
- 社区画像（🟡）：tfast README 明确写 "AVB 2.0 & dm-verity: Android Verified Boot checks cryptographically enforce signature chains across `vbmeta`, `boot`, and `super` partitions"（本地快照 ✅ 已读）。Hovatek 教程也把真正修复方式指向"回刷官方签名固件 / 关 dm-verity（thread-32719）/ 有 vbmeta 分区的机型另用方法 4"（✅ 原文）。→ 说明"消除开机画面警告"与"让系统通过校验"是两件事。
- **正确用户预期**（必须写进帮助/免责声明）：
  - 本功能只是"去掉 LK 阶段的提示与 5 秒等待"，**不会**关闭 AVB2/dm-verity，**不会**把 Red State 设备救活；
  - 若设备本身校验失败无法开机，回刷官方签名固件才是正解（Hovatek ✅）；
  - 已解锁（Orange）设备上改 LK 通常只是体验优化，风险低于 Red 场景。
- ❓ 官方文档：AOSP `source.android.com` 的 verified boot 页面本次抓取失败（网络 TypeError，两次），因此本报告不直接引用其原文；建议发版前由人工补一条 AOSP 官方链接（见 §8 参考链接中标注）。

---

## 6. 刷写与前置条件

1. **前置**：bootloader 已解锁 / seccfg 已解锁。lkpatcher README 明确写：要让补丁后的镜像启动，需先用 [mtkclient](https://github.com/bkerler/mtkclient) 解锁 seccfg；若解锁后仍不启动，很可能该设备使用 SBC，**不受支持**（🟡 社区经验 ✅ 已读原文）。
2. **备份**：改动前保存原始 `lk.img`/`lk.bin`（工具默认生成 `*_original_backup.img`）；同时备份 `seccfg`、`vbmeta` 更稳妥（tfast 仓库的 `work_backup/` 就包含 `lk_a/lk_b` 各 4MB、`vbmeta_a/vbmeta_b` 各 8MB、`seccfg.bin` 8MB、`frp.bin` 1MB、`misc.bin` 512KB —— ✅ 仓库树快照，可作分区布局参考）。
3. **刷写方式**：
   - `fastboot flash lk lk_patched.img`（Hovatek 建议：改过的 LK 用 fastboot 刷，避免出现 `.........` 黑底白字计数 ✅；modlk/justistab3 也如此）。
   - SP Flash Tool：载入 scatter，`lk`/`lk2` 指向补丁后镜像，模式选 **Download Only**（不要 Format All + Download）（🟡 justistab3 README 经验）。
   - **A/B 双槽**：`lk` 与 `lk2`（或 `lk_a`/`lk_b`）**两个都要刷**；只刷一个，设备可能从另一槽启动，警告仍在（✅ justistab3 README 原文；本样本 `lk_a.bin`/`lk_b.bin` 各一份印证）。
4. **救砖**：Red State 需回刷官方/签名固件（Hovatek ✅）；有 `vbmeta` 分区的设备社区另有方法（thread-32719，❓ 未读正文）。tfast README 提醒：修改 bootloader / 低层分区可能导致**永久硬砖、bootloop 或触发 Anti-Rollback eFuse**（🟡 已读原文）。
5. **电量与环境**：刷写前电量 ≥50%（🟡 tfast README）；SP Flash Tool/驱动环境按机型准备；保留 DA/认证文件。
6. **调试**：若失败，社区建议用 mtkclient dump `expdb` 分区并附 `image.txt` 求助（🟡 lkpatcher README）。

---

## 7. 风险、免责声明与未验证事项

### 7.1 风险（须写入 README/帮助）

1. 修改 bootloader 有**变砖/硬砖/触发 Anti-Rollback** 风险；仅用于自己拥有或已获授权的设备。
2. AArch64 补丁是**启发式**：本次仅 1 个真实样本验证；识别失败必须 fail-safe 不写文件。
3. 去警告 ≠ 关闭校验（见 §5）；Red State 不能靠本工具修复。
4. 部分机型需 seccfg 解锁/关闭 SBC 才能启动改过的 LK；A/B 双槽必须都刷。
5. 本仓库**无真机验证**：仅静态分析 + 真实镜像离线验证；刷写后果自负。

### 7.2 未验证 / 不可信事项清单（诚实标注）

| 事项 | 状态 | 说明 |
|---|---|---|
| XDA 讨论帖正文 | ❓ 未验证 | 仅通过搜索摘要看到标题 [Getting rid of "Orange state" message and 5 second Delay on TB132FU mediatek version](https://xdaforums.com/t/getting-rid-of-orange-state-message-and-5-second-delay-on-tb132fu-mediatek-version.4801113/)；站点对本环境 403/未尝试抓取正文 |
| r0rt1z2 技术指南链接 | ✅ 已确认失效 | lkpatcher README 指向的 `https://blog.r0rt1z2.com/patch-mediatek-bootloader-images-lk.html` 现返回 **HTTP 404**（GitHub Pages "Page not found"） |
| AOSP 官方 verified boot / dm-verity 文档 | ❓ 未获取 | `source.android.com` 两次抓取均网络失败；本文 §5 的 AOSP 层表述基于社区资料 + 通用知识，发版前需人工补官方引用 |
| nainsharma01/mtk-lk-warning-patch 内容 | ✅ 已核实不符 | 仓库描述宣称去警告，实际快照是 Claude Code 可视化工具（无 LK 逻辑）；其 5★/MIT/TS 元数据仅作快照记录，**不要引用为方案** |
| 状态值 1/2/3 的语义映射 | ❓ 待确认 | 规格书 §3.1 注释与本次反汇编分支不一致（1&3 → Red 字符串路径，2 → Orange 字符串路径）；不影响"入口 return 0"的补丁效果 |
| 规格书 §3.4 的 `012b` 扩展规则 | 🟡 推导 | 社区教程只给后缀 `…012B/…022B` + 前缀 `08B5****`；四条完整串为规律外推，建议按前后缀匹配而非死串 |
| dm-verity 延时 32×100,000 µs | ✅ 已修正 | 实测 **30×100,000 µs = 3.0 秒** |
| `lk-universal-patch` / `MTK-Lk-PatcherProject` / `val-protocol` / `honor-mtk-unlocker` | ❓ 仅元数据 | 未读 README/源码，只记录存在与快照元数据 |
| 样本设备身份与"真实设备"来源 | ❓ 未验证 | 来自第三方仓库 `work_backup/`，无设备型号/固件版本佐证；结构真实（可解析、可反汇编），但机型未知 |
| Hovatek 帖子中 thread-32719 / 视频 | ❓ 未读 | 仅作为原文引用出现，未抓取正文/未观看 |

---

## 8. 参考链接

**社区教程（一手，本机抓取成功）**

- [Hovatek — How to fix MTK orange, yellow & red state warnings（thread-31664）](https://www.hovatek.com/forum/thread-31664.html) ✅ 抓取成功（2019-12-24 发布，2024-06-24 修改）
- [Hovatek — disable dm-verity 教程（thread-32719，教程内引用，❓未读）](https://www.hovatek.com/forum/thread-32719.html)
- [Hovatek 教学视频（教程内引用，❓未观看）](https://youtu.be/wFi7Ao4liNw)

**GitHub 项目**

- [justistab3-bot/mtk-lk-warning-patch（MIT，8★，ARM32，最完整开源方案）](https://github.com/justistab3-bot/mtk-lk-warning-patch)
- [programmersd21/modlk（MIT，10★，只清文本）](https://github.com/programmersd21/modlk)
- [R0rt1z2/lkpatcher（GPL-3.0，415★，ARM32 规则库/分区管理/证书绕过）](https://github.com/R0rt1z2/lkpatcher)
- [tfastdigital/tfast-mtk-bootloader-tool（43★，真实 LK 样本来源，解锁向）](https://github.com/tfastdigital/tfast-mtk-bootloader-tool)
- [tfastdigital/lk-unlock-Xiaomi-devices（AGPL-3.0，3★，邻近领域）](https://github.com/tfastdigital/lk-unlock-Xiaomi-devices)
- [bkerler/mtkclient（GPL-3.0，seccfg 解锁/分区读写前置工具）](https://github.com/bkerler/mtkclient)
- [nainsharma01/mtk-lk-warning-patch（内容与描述不符，❓不可信）](https://github.com/nainsharma01/mtk-lk-warning-patch)
- [wangyishanjiadao/lk-universal-patch（❓仅元数据）](https://github.com/wangyishanjiadao/lk-universal-patch)
- [kusnadi3128/MTK-Lk-PatcherProject（❓仅元数据）](https://github.com/kusnadi3128/MTK-Lk-PatcherProject)
- [Maikyxd/val-protocol（AGPL-3.0，109★，Motorola 解锁/FRP，邻近领域）](https://github.com/Maikyxd/val-protocol)
- [Redminote11tech/honor-mtk-unlocker（GPL-3.0，Rust，解锁向）](https://github.com/Redminote11tech/honor-mtk-unlocker)

**XDA / 官方文档（未验证或不可用，如实标注）**

- [XDA — Getting rid of "Orange state" message and 5 second Delay on TB132FU mediatek version（❓仅搜索摘要，站点 403）](https://xdaforums.com/t/getting-rid-of-orange-state-message-and-5-second-delay-on-tb132fu-mediatek-version.4801113/)
- [r0rt1z2 技术指南（✅ 已确认 404 失效）](https://blog.r0rt1z2.com/patch-mediatek-bootloader-images-lk.html)
- [AOSP — Verified Boot（❓本次抓取网络失败）](https://source.android.com/docs/security/features/verifiedboot)
- [AOSP — dm-verity（❓本次抓取网络失败）](https://source.android.com/docs/security/features/verifiedboot/dm-verity)

**本项目内部证据（可复现）**

- [docs/spec_lkpatch.md](spec_lkpatch.md) — 功能规格与 API 契约
- `analysis/external/modlk_warn/README.zh-CN.md`、`lk_core.py`（MIT，方法/校验思路参考，**未复制代码**）
- `analysis/external/lkpatcher/README.md`、`patches.json`（GPL-3.0，仅引用特征码事实与思路，**未复制代码**）
- `analysis/external/tfast_README.md`、`tfast_tree.json`（样本来源与分区布局快照）
- `analysis/external/ghsearch1.json`、`ghsearch2.json`（2026-11-13 GitHub 元数据快照，星数出处）
- `analysis/lk/samples/lk_a.bin` / `lk_b.bin`（SHA-256 见 §4.1；`analysis/lk/tools/fetch_blob.py` 为抓取脚本）

---

## 9. 与本项目实现对照表

| 能力 | 本项目（InspireMTKTools v3.0.0 规格） | 外部开源现状 |
|---|---|---|
| LK 镜像解析 | 0x200 MTK 头（magic/size/name/ext magic）+ 裸 payload 自动识别 | justistab3/modlk 有头校验；lkpatcher 还能解析 bootloader 镜像内多分区 |
| ARM32 Thumb 规则 | 内置 7 条规则（orange_state ×4、red_state ×1、dm_verity ×2）+ 自定义 JSON 规则；命中数必须唯一，否则列出候选 | Hovatek/modlk/justistab3：前缀+特征码法；lkpatcher：同名分类 needle（另含 fastboot 类） |
| AArch64 启发式 | **有**：字符串引用（ADRP+ADD）→ 函数入口 → 分类 + 置信度 + 证据；`mov w0,wzr; ret` | **公开方案均无**（其规则为 Thumb 字节；本项目样本命中数 0）→ 差异化点 |
| 补丁类别 | orange_state / red_state / dm_verity / 可选 warning_text 清零 | justistab3（A+B）、modlk（仅文本）、lkpatcher（更多类含解锁） |
| 内核参数保护 | 强制校验 `androidboot.verifiedbootstate/veritymode/atm` 等前后计数不变，若变化拒绝写盘 | justistab3 有同类校验（补丁 B 不碰这些参数）；modlk 无 |
| 备份/还原 | 默认生成 `*_original_backup.img`；`lk-restore` 子命令 | justistab3 有；lkpatcher 有 `--backup` |
| 校验 | 大小不变、头 0x200 不变、补丁点反汇编、差异区间、SHA-256 | justistab3 有类似；lkpatcher 有 verify |
| CLI | `lk-info` / `lk-patch` / `lk-verify` / `lk-restore`（中文错误/退出码） | justistab3 CLI；lkpatcher CLI 丰富；modlk 单命令 |
| GUI | ttkbootstrap 中文界面（候选表/预览/应用/校验/还原/刷写说明） | justistab3 有 tkinter 中文 GUI（ARM32 only）；lkpatcher 网页版 GUI 不开源；其余无 GUI |
| 失败安全 | 无命中/多命中/结构不符 → **不写文件** + 中文原因 | 三者基本都有 fail-safe（justistab3 明确拒绝；lkpatcher `allow_incomplete` 可配置） |
| 不支持的 | 关闭 AVB2/dm-verity 校验、重签名、bootloader 解锁、SBC 机型保证、download-mode 的 5 秒提示（样本 0x15776F 处）、真机验证 | 同样都不做"关校验"；解锁类能力在 lkpatcher/tfast 等其它工具中 |

**边界说明**：本工具的"去 5 秒"只覆盖**boot state 警告分发器**这一条路径。样本中还存在 download/flashinfo 模式的 `Your device will boot in 5 seconds`（文件 0xB2AAA/0x15776F，另一函数），不在本补丁范围；若用户反馈"仍有 5 秒"，应提示该场景属于 download 模式提示，不能靠本补丁消除。

**实现状态提示**：上表依据 [docs/spec_lkpatch.md](spec_lkpatch.md) 的能力契约整理；最终实现细节以 `src/logobuilder/core/lkpatch.py` 与测试为准（截至本文写作时该文件尚未出现在工作区，属于并行开发中的计划能力）。
