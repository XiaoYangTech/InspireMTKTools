# MTK LK（Little Kernel）AArch64 警告补丁逆向分析

> 项目：亿方联发科工具箱 / InspireMTKTools（v3.0.0）
> 任务：task-7（独立逆向验证 AArch64 警告分发器 / dm-verity 警告函数 + 规则库）
> 分析者：lk-analyst
> 日期：2026-11-13
> 关联规格：`docs/spec_lkpatch.md`（§2 镜像结构、§3 AArch64 结论、§5 校验规则）
> 机器可读产物：`analysis/lk/rules_lk.json`、`analysis/lk/validate_detector.py`、`analysis/lk/samples/README.md`
> 原始证据：`analysis/lk/_evidence_a64.txt`、`_classify.txt`、`_query1.txt`、`_validate_a.txt`、`_validate_all.txt`、`_validate_arm32.txt`

---

## 0. 结论摘要（先看这里）

| 项目 | 结论（全部经本机 capstone 反汇编复核） |
|---|---|
| 样本 A 槽 | `analysis/lk/samples/lk_a.bin`，4,194,304 B，SHA-256 `bb2f2e6a6f7b7603dee3de08dc09e29dd74da49bd4cb98f20e8be648ba66f873` |
| 样本 B 槽 | `analysis/lk/samples/lk_b.bin`，SHA-256 `f4ecd3eed32f6f8ce112115af11b054de14bf01a8d6cab7d12b31bd9051aa6e4` |
| 第三方 unlocked 变体 | `analysis/lk/samples/lk_a.unlocked.bin`，SHA-256 `ba5c57ebb1614784882b50eb05b237d42badf1f809c94b2a4c7c954f73b4518f`（tfast 工具的解锁输出） |
| 镜像结构 | 512B MTK 头 + 870,352B payload；**4MB 文件不是"payload+全 0 填充"**，而是 12 个 MTK 子镜像紧密排列（详见 §1.3，**修正 spec §2**） |
| 警告分发器 | payload **0x4D728** / file **0x4D928**（函数 0x4D728..0x4D7EC） |
| dm-verity 警告函数 | payload **0x4CA58** / file **0x4CC58**（函数 0x4CA58..0x4CC68） |
| 推荐补丁 | 两个函数入口 8 字节均改为 `mov w0, wzr; ret` = `E0 03 1F 2A C0 03 5F D6` |
| 补丁安全性 | 两函数原所有路径均 `return 0` / void；两个调用点均忽略返回值；无分支/数据引用/指针落到被改写的 8 字节内；栈平衡与 callee-saved 语义不被破坏（§5） |
| 状态映射 | **state 1/3 → Red 警告路径，state 2 → Orange 警告路径，state 0 → 无警告**（**修正 spec §3.1 的描述**） |
| 等待时长 | 分发器 1 × 5,000,000µs = **5.0 s**；dm-verity 警告 **30 × 100,000µs = 3.0 s**（**修正 spec §3.2 的"32 次≈3.2s"**） |
| 规则库 | `rules_lk.json`：**15 条规则** = ARM32 13 条签名规则（7 条规格签名 + 2 条通配变体 + 4 条"补丁 A"push/pop 变体）+ AArch64 2 条启发式规则（含全部参数） |
| 验证器 | `validate_detector.py`：`lk_a/lk_b/lk_a.unlocked` 全部 **PASS**（2/2 期望偏移、safety OK）；布局漂移 `--shift 0x1000` PASS；ARM32 合成镜像 13/13 PASS |
| 真实解锁样本对比 | tfast 的 unlocked 变体 **没有**改这两个警告函数（差异全在解锁相关函数/版本串），且其补丁风格同样是 `mov w0,wzr; ret` —— 侧面印证本方案（§7） |

---

## 1. 样本与镜像结构

### 1.1 已核实的 MTK 头

`lk_a/lk_b/lk_a.unlocked` 三个文件头一致（逐字节相同）：

```
0x000  u32  magic    = 0x58881688
0x004  u32  size     = 870352 = 0xD47D0   ← 有效负载长度（从 0x200 起的字节数）
0x008  char[32] name = "lk\0..."
0x030  u32  ext magic= 0x58891689
0x200  payload[size] = 裸 LK 代码（AArch64）
```

### 1.2 `size` 字段语义（用相邻子镜像头验证，非猜测）

- payload 结束于 file `0x200 + 0xD47D0 = 0xD49D0`；
- **恰好**在 file `0xD49D0` 处出现下一个合法 MTK 头：`magic=0x58881688, size=1733, name="cert1", ext=0x58891689`。
- 因此 `size` 是 **payload 长度**，`payload = data[0x200:0x200+size]`。

> ⚠️ 注意方向相反的两个易错写法：
> - 正确：`payload = data[0x200 : 0x200+size]`；
> - 错误：`payload = data[0x200 : size]`（会少掉最后 512 字节；tfast 的 `src/lk_image.py::payload_of()` 就是这种切片，对这类打包镜像少 512 字节。本工具不要照抄）。

### 1.3 4MB 文件布局（修正 spec §2 的"尾部 0x00 填充至分区大小"）

实测 4MB 文件是 **多个 0x200 头子镜像的紧密排列（16 字节对齐）**，共 12 个：

| offset | 子镜像 | size 字段（payload 长度） | 结束（含 0x200 头，16B 对齐） |
|---|---|---|---|
| 0x000000 | **lk** | 0xD47D0 = 870,352 | 0x0D49D0 |
| 0x0D49D0 | cert1 | 1733 | 0x0D52A0 |
| 0x0D52A0 | cert2 | 982 | 0x0D5880 |
| 0x0D5880 | bl2_ext | 610,400 | 0x16AAE0 |
| 0x16AAE0 | cert1 | 1733 | 0x16B3B0 |
| 0x16B3B0 | cert2 | 982 | 0x16B990 |
| 0x16B990 | aee | 811,208 | 0x231C60 |
| 0x231C60 | cert1 | 1733 | 0x232530 |
| 0x232530 | cert2 | 982 | 0x232B10 |
| 0x232B10 | lk_main_dtb | 206,820 | 0x265500 |
| 0x265500 | cert1 | 1733 | 0x265DD0 |
| 0x265DD0 | cert2 | 982 | 0x2663A0 |

对齐验证：例如 cert2 结束 `0xD52A0+0x200+0x3D6 = 0xD5876` → 向上取 16 对齐 = `0xD5880`（下一个 bl2_ext 头）✓。

**工程含义**：
1. 只允许改第一个 lk payload 内的字节；**cert1/cert2（签名/证书 blob）、bl2_ext、aee、lk_main_dtb 一律不能碰**，否则可能破坏其它阶段的校验/功能（本工具 `apply_patches` 应断言所有改动都落在 `[0x200, 0x200+size)` 内）。
2. 不要把"payload 之后的非零数据"当成填充而做 `data.rstrip(b'\0')`/"清空尾部"之类的处理。
3. 规格 §2 的头部字段/魔数/无校验块结论仍然成立；仅"尾部全 0 填充"描述不准确。

### 1.4 地址/偏移约定

本分析全部使用 **payload 偏移**（0 = file 0x200）；**文件偏移 = payload 偏移 + 0x200**。
LK 代码以 PC 相对寻址 + `ADRP+ADD` 字面量引用字符串；把 payload 映射到地址 0 时，`ADRP(绝对页) + ADD(imm)` 的计算结果就等于"字符串的 payload 偏移"（前提：真实链接基址页对齐。本样本可反推出链接基址 = **0x50F00000**，见 §2.3）。
利用 `--shift 0x1000` 的合成测试已证明：整体平移（页对齐）后偏移计算不变，只有绝对偏移变化（§8.4）。

---

## 2. 分析方法与三个关键工具事实

### 2.1 反汇编方法

- `capstone 5.0.7`，`CS_ARCH_ARM64`，按 4 字节对齐线性反汇编；
- `ADRP` 操作数 imm 为"绝对页地址"（capstone 已按 PC 页计算）：`target_page = imm`；
- 字符串引用模式：`adrp Xd, #page` 后 1~3 条内 `add Xd, Xd, #imm` → `target = page + imm`；
- 函数入口识别：反向扫描 `stp x29, x30, [sp, #-imm]!` / `sub sp, sp, #imm` / `stp x29, x30, [sp, #imm]`；
- 函数结束识别：正向扫描 `ldp x29, x30, [sp], #imm` 后的 `ret`。

### 2.2 全镜像统计（lk_a，payload 内）

- `adrp` 指令数 → 大量；ADRP+ADD 且目标落在 payload 内的引用共 **6,382** 条（含日志/字符串/数据引用）；
- 其中指向"警告/状态相关 C 字符串"的引用极少，且分组后 **只有 2 个函数**（下表），说明镜像内警告路径集中，天然适合"字符串锚点→函数分组"的通用检测法。

### 2.3 重要发现：LK 字符串指针的 `0xFFFF0000` 编码（链接基址 0x50F00000）

部分常量字符串不是用 `ADRP+ADD` 引用，而是以 64 位"标记指针"存在数据表里：

```
u64 = 0xFFFF0000_00000000 | (link_base + payload_offset)      # link_base = 0x50F00000
例：0xFFFF0000_50FACBB7  -> payload 0xACBB7  "androidboot.verifiedbootstate=green"
```

- 全 payload 中这类标记字共 1148 个；
- **boot state 名称表**位于 payload **0xACC90**（4 × u64，按 state 索引）：

| index | 标记字 | 字符串（payload 偏移） | 含义 |
|---|---|---|---|
| 0 | `0xFFFF0000_50FACBB7` | 0xACBB7 `androidboot.verifiedbootstate=green` | green |
| 1 | `0xFFFF0000_50FACB70` | 0xACB70 `...=yellow` | yellow |
| 2 | `0xFFFF0000_50FACB4B` | 0xACB4B `...=orange` | orange |
| 3 | `0xFFFF0000_50FACB95` | 0xACB95 `...=red` | red |

- 使用该表的函数是 payload **0x4D6EC**：读 boot state 全局 → `cmp #3; b.hi` → `ldr x0,[0xACC90 + state*8]` → `b 0x10A04`（`0x10A04` 是 cmdline 追加函数，内部有互斥量/格式化），即把 `androidboot.verifiedbootstate=...` 写进内核 cmdline。
- **这就是为什么"verifiedbootstate 字符串没有 ADRP+ADD 引用"**：它们只被标记指针表引用。工具做"内核参数守卫"时应按**文本出现次数**校验（前后不变），不要因为"扫不到引用"就以为可以随意改。
- 该发现同时被用作 `validate_detector.py` 的 patch-safety 检查：先反推 link base，再判断是否有标记指针指向被改写的函数入口（本样本：无）。

---

## 3. 函数一：开机状态警告分发器（payload 0x4D728 / file 0x4D928）

### 3.1 完整反汇编（lk_a = lk_b，逐字节相同）

```
04d728: stp   x29, x30, [sp, #-0x20]!     ← 入口 / 被补丁覆盖
04d72c: str   x19, [sp, #0x10]            ← 被补丁覆盖
04d730: mov   x29, sp
04d734: adrp  x8, #0xc7000
04d738: ldr   w8, [x8, #0xdc8]            ; w8 = boot_state 全局（payload 0xC7DC8）
04d73c: cmp   w8, #1
04d740: b.eq  #0x4d784                    ; state 1 -> Red 路径
04d744: cmp   w8, #3
04d748: b.eq  #0x4d784                    ; state 3 -> Red 路径
04d74c: cmp   w8, #2
04d750: b.ne  #0x4d7dc                    ; state 0/其它 -> 直接 return 0
        ; ---- state 2（Orange）路径 ----
04d754: bl    #0x397a4                    ; 取屏幕/lcm 参数（与警告打印相关）
04d758: bl    #0x39798
04d75c: cmp   w0, #0
04d760: cinc  w8, w0, lt
04d764: asr   w0, w8, #1
04d768: mov   w1, wzr
04d76c: bl    #0x39644                    ; 控制台初始化/清屏类
04d770: adrp  x19, #0xac000
04d774: adrp  x0, #0xac000
04d778: add   x19, x19, #0xbea            ; x19 = "OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.\n"
04d77c: add   x0, x0, #0xbdb              ; x0  = "Orange State\n\n"
04d780: b     #0x4d7bc                    ; 汇合
        ; ---- state 1/3（Red）路径 ----
04d784: bl    #0x397a4
04d788: bl    #0x39798
04d78c: cmp   w0, #0
04d790: cinc  w8, w0, lt
04d794: asr   w0, w8, #1
04d798: mov   w1, wzr
04d79c: bl    #0x39644
04d7a0: adrp  x0, #0xac000
04d7a4: add   x0, x0, #0xc28              ; x0 = "Red State\n\n"
04d7a8: bl    #0x39580                    ; 打印 "Red State\n\n"
04d7ac: adrp  x19, #0xac000
04d7b0: adrp  x0, #0xac000
04d7b4: add   x19, x19, #0xc0e            ; x19 = "Dismiss After 5 Seconds.\n"
04d7b8: add   x0, x0, #0xc34              ; x0  = "Device Cannot Boot. Enter Repair Mode."
04d7bc: bl    #0x39580                    ; 打印 x0（Orange State 或 Device Cannot Boot）
04d7c0: mov   x0, x19
04d7c4: bl    #0x39580                    ; 打印 x19（OS Not Being Verified... 或 Dismiss...）
04d7c8: bl    #0x39b84                    ; 刷新/同步输出（写 MMIO 0xffff10007008）
04d7cc: mov   w0, #0x4b40
04d7d0: movk  w0, #0x4c, lsl #16          ; w0 = 0x004C4B40 = 5,000,000 µs = 5 秒
04d7d4: bl    #0x5f8b0                    ; 延时/等待函数
04d7d8: bl    #0x39b84                    ; 再次刷新
04d7dc: ldr   x19, [sp, #0x10]            ; 公共返回路径（所有分支汇合）
04d7e0: mov   w0, wzr                     ; return 0
04d7e4: ldp   x29, x30, [sp], #0x20
04d7e8: ret
04d7ec: sub   sp, sp, #0xc0                ; ← 下一个函数开始（0x4D7EC）
```

- 函数边界：**0x4D728 .. 0x4D7EC**（ret 在 0x4D7E8，长度 0xC4）。
- 5 个字符串引用（精确同一函数内）：

| 引用点 | 目标字符串 | payload 偏移 |
|---|---|---|
| 0x4D774 | `Orange State\n\n` | 0xACBDB |
| 0x4D770 | `OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.\n` | 0xACBEA |
| 0x4D7A0 | `Red State\n\n` | 0xACC28 |
| 0x4D7B0 | `Device Cannot Boot. Enter Repair Mode.` | 0xACC34 |
| 0x4D7AC | `Dismiss After 5 Seconds.\n` | 0xACC0E |

- 延时编码（精确字节）：`00 68 89 52` = `mov w0,#0x4b40`；`80 09 a0 72` = `movk w0,#0x4c,lsl#16` → 0x4C4B40 = 5,000,000 µs。

### 3.2 状态映射（实测，修正 spec §3.1）

- cmp 链：`cmp #1 → b.eq 0x4D784`、`cmp #3 → b.eq 0x4D784`、`cmp #2 → b.ne 0x4D7DC`；
- 0x4D784 分支打印 `Red State` 路径（0x4D7A0/0x4D7AC/0x4D7B0）；
- 落空（state==2）分支打印 `Orange State` 路径（0x4D770/0x4D774）；
- 与 §2.3 状态名称表（0=green、1=yellow、2=orange、3=red）一致：

| boot_state | 显示 | 行为 |
|---|---|---|
| 0 (green) | 无 | 直接 return 0 |
| 1 (yellow) | **Red 警告路径** | 打印 Red State + Device Cannot Boot + Dismiss，5s 等待，return 0 |
| 2 (orange) | **Orange 警告路径** | 打印 Orange State + OS Not Being Verified…，5s 等待，return 0 |
| 3 (red) | **Red 警告路径** | 同 state 1 |

- 全局 boot state：payload **0xC7DC8**（镜像初始值为 `02 00 00 00`，即 2）；读写函数：`0x4D710`（get：`ldr w0,[0xC7DC8]; ret`）、`0x4D71C`（set：`str w0,[0xC7DC8]; ret`）。
- 入口参数 `x0` 未被使用（函数内只把 x19 当临时寄存器保存/恢复）；两个调用点传入的 0x92F40（"recovery"）/0x94BD2（"boot"）是死参数（调用方遗留）。

### 3.3 调用点与调用语义（见 §5）

`bl 0x4D728` 仅两处，都在函数 **0x4CEB4**（开机 boot flow）内：payload 0x4D21C（w19==1 分支）、0x4D22C（w19==0 分支）。两处 `bl` 之后都是 `b #0x4D240`，**返回值被丢弃**。

---

## 4. 函数二：dm-verity 警告函数（payload 0x4CA58 / file 0x4CC58）

### 4.1 完整反汇编（延时块以省略表示）

```
04ca58: stp   x29, x30, [sp, #-0x20]!     ← 入口 / 被补丁覆盖
04ca5c: str   x19, [sp, #0x10]            ← 被补丁覆盖
04ca60: mov   x29, sp
04ca64: mov   w19, w0                     ; w19 = 入参（dm-verity 状态码）
04ca68: add   x0, x29, #0x1c
04ca6c: str   wzr, [x29, #0x1c]
04ca70: bl    #0x4cc74                    ; dm-verity 状态检查（写 [x29+0x1c]，返回错误码）
04ca74: cmp   w19, #2
04ca78: b.eq  #0x4ca88                    ; 入参==2 -> 警告
04ca7c: ldr   w8, [x29, #0x1c]
04ca80: cmp   w8, #1
04ca84: b.ne  #0x4cc5c                    ; 检查结果!=1 -> 跳过警告
04ca88: bl    #0x397a4                    ; 控制台/屏幕准备
04ca8c: bl    #0x39798
04ca90: cmp   w0, #0
04ca94: cinc  w8, w0, lt
04ca98: asr   w0, w8, #1
04ca9c: mov   w1, wzr
04caa0: bl    #0x39644
04caa4: adrp  x0, #0xac000 ; add x0, x0, #0x6a3  ; "dm-verity corruption\n\n"
04caac: bl    #0x39580
04cab0: adrp  x0, #0xac000 ; add x0, x0, #0x6ba  ; "Your device is corrupt.\n"
04cab8: bl    #0x39580
04cabc: adrp  x0, #0xac000 ; add x0, x0, #0x6d3  ; "It can't be trusted and may not work properly.\n"
04cac4: bl    #0x39580
04cac8: adrp  x0, #0xac000 ; add x0, x0, #0x703  ; "Device will continue to bootup in %ds\n"
04cad0: mov   w1, #3                             ; 参数：3 秒
04cad4: bl    #0x39580
        ; ---- 30 × 100,000µs 延时 = 3.0 秒 ----
04cad8: mov   w0, #0x86a0
04cadc: movk  w0, #1, lsl #16             ; 0x186A0 = 100,000 µs
04cae0: bl    #0x5f8b0
04cae4: mov   w0, #0x86a0
04cae8: movk  w0, #1, lsl #16
04caec: bl    #0x5f8b0
04caf0: ...                               ; 同样的 3 条指令重复，bl 站点：
                                          ; 0x4cae0,0x4caec,0x4caf8,0x4cb04,0x4cb10,0x4cb1c,
                                          ; 0x4cb28,0x4cb34,0x4cb40,0x4cb4c,0x4cb58,0x4cb64,
                                          ; 0x4cb70,0x4cb7c,0x4cb88,0x4cb94,0x4cba0,0x4cbac,
                                          ; 0x4cbb8,0x4cbc4,0x4cbd0,0x4cbdc,0x4cbe8,0x4cbf4,
                                          ; 0x4cc00,0x4cc0c,0x4cc18,0x4cc24,0x4cc30,0x4cc3c
                                          ; 共 30 处 → 30 × 100,000µs = 3.0s
04cc40: bl    #0x4cd44                   ; clear/reset dm-verity 状态（写 seccfg digest）
04cc44: adrp  x0, #0xac000 ; add x0, x0, #0x72a  ; "tran_dm_fix=1"
04cc4c: bl    #0x10a04                   ; cmdline 追加（不是打印！）
04cc50: adrp  x0, #0xac000 ; add x0, x0, #0x738  ; "Device occur dm vertity corruption\n"
04cc58: bl    #0x610fc                   ; printf/日志输出
04cc5c: ldr   x19, [sp, #0x10]           ; 公共返回路径（void）
04cc60: ldp   x29, x30, [sp], #0x20
04cc64: ret
04cc68: mov   w8, #4                     ; ← 下一个函数（0x4CC68）开始
04cc6c: str   w8, [x0]
04cc70: ret
04cc74: sub   sp, sp, #0x40              ; ← dm-verity 状态检查函数（被 0x4CA70 调用）
```

- 函数边界：**0x4CA58 .. 0x4CC68**（ret 在 0x4CC64，长度 0x210）。
- 5 个警告字符串引用 + 2 个尾部引用：

| 引用点 | 目标字符串 | payload 偏移 | 作用 |
|---|---|---|---|
| 0x4CAA4 | `dm-verity corruption\n\n` | 0xAC6A3 | 打印 |
| 0x4CAB0 | `Your device is corrupt.\n` | 0xAC6BA | 打印 |
| 0x4CABC | `It can't be trusted and may not work properly.\n` | 0xAC6D3 | 打印 |
| 0x4CAC8 | `Device will continue to bootup in %ds\n` | 0xAC703 | 打印（w1=3） |
| 0x4CC44 | `tran_dm_fix=1` | 0xAC72A | **cmdline 追加**（0x10A04） |
| 0x4CC50 | `Device occur dm vertity corruption\n` | 0xAC738 | 打印/日志（0x610FC） |

- 延时编码（精确字节）：`00 d4 90 52` = `movz w0,#0x86a0`；`20 00 a0 72` = `movk w0,#1,lsl#16` → 0x186A0 = 100,000 µs；**共 30 次**（不是 32 次），合计 **3.0 秒**。
- 入参 w0 是 dm-verity 状态码（调用方从 `[x29,#-0x14]` 传入，该值早先从 seccfg 相关结构 `+0x130` 读出）；函数为 **void**（返回路径不设置 w0），调用方也不使用返回值。
- 结尾 `bl 0x4CD44`：清 dm-verity 状态（`clear_dm_verity_status`），属于"警告后清除、避免下次再报"的副作用；补丁后该副作用同样被跳过（这是去警告的预期行为）。

### 4.2 dm-verity 相关其它函数（不是用户可见警告，不要打补丁）

| payload | 角色（依据字符串/调用关系） |
|---|---|
| 0x4CC74 | dm-verity 状态检查：内部调 0x78E38/0x78EB0 读 seccfg 状态，失败时日志 `[%s] set_dm_verity_state fails(0x%x)`，写 `*x0=status` |
| 0x4CD44 | `clear_dm_verity_status`：重置 seccfg 中的 dm-verity digest/状态（被警告函数与 fastboot reset 调用） |
| 0x4CC78 | `get_dm_verity_status` 封装（引用 get/set 字符串，日志级别，不是警告） |
| 0x4CE88 | fastboot 命令 `[FASTBOOT] Start reset dm-verity status.`（日志 + 调 0x4CD44） |
| 0x7B084 | libavb `restart_on_corruption`/`ignore_corruption` + `androidboot.veritymode` cmdline 处理（AVB 逻辑，非警告） |
| 0x7D2FC / 0x7DA3C / 0x802FC / 0x80A70 / 0x80CD0 | vbmeta/avb 校验与日志（libavb），非用户警告 |

---

## 5. 补丁证明：`mov w0, wzr; ret` 为什么安全

### 5.1 补丁字节

| 函数 | 文件偏移 | 原 8 字节 | 反汇编 | 新 8 字节 | 反汇编 |
|---|---|---|---|---|---|
| 分发器 | **0x4D928** | `FD 7B BE A9 F3 0B 00 F9` | `stp x29,x30,[sp,#-0x20]! ; str x19,[sp,#0x10]` | `E0 03 1F 2A C0 03 5F D6` | `mov w0, wzr ; ret` |
| dm-verity | **0x4CC58** | `FD 7B BE A9 F3 0B 00 F9` | `stp x29,x30,[sp,#-0x20]! ; str x19,[sp,#0x10]` | `E0 03 1F 2A C0 03 5F D6` | `mov w0, wzr ; ret` |

（`E0 03 1F 2A` = `mov w0, wzr`；`C0 03 5F D6` = `ret`）

### 5.2 返回值语义

1. **分发器原本所有路径都 return 0**：Red/Orange 路径汇合到 0x4D7DC → 0x4D7E0 `mov w0, wzr` → 返回；state 0/其它也跳到 0x4D7DC。因此补丁后返回 0 与原函数**完全一致**。
2. **调用点忽略返回值**：0x4D21C/0x4D22C 之后均为 `b #0x4D240`，w0 未被读取（0x4CEB4 函数最后返回的是自己的 w20，不是分发器返回值）。
3. **dm-verity 函数是 void**：返回前不设置 w0；调用点 0x4D254 之后是 `mov w0, w20`（用自己的局部变量），同样忽略。补丁返回 w0=0 不会影响任何调用方。
4. 分发器入口参数 x0 在原函数内**根本未被使用**（只保存/恢复 x19），补丁不读取 x0 也没有问题。

### 5.3 栈与 callee-saved 寄存器

- 原入口 `stp x29,x30,[sp,#-0x20]!` + `str x19,[sp,#0x10]`，原出口 `ldp x29,x30,[sp],#0x20`。
- 补丁在入口立即 `ret`，**push/pop 成对地都不执行**，sp 不变 → 栈平衡（不存在"push 了没 pop"）。
- x19 是 callee-saved：入口不再保存、返回前也不再恢复，**等于函数没有改过 x19**，满足 AArch64 PCS；x29/x30 也未被破坏。
- dm-verity 函数的 x19 同样只是工作寄存器（`mov w19,w0`），跳过保存/恢复不影响调用方。

### 5.4 不存在"其它入口"或"数据引用"

`validate_detector.py` 对两个入口做了三类检查（结果：全部为空）：

1. **分支**：全镜像扫描 `b/bl/cbz/cbnz/tbz/tbnz/adr`，目标落在 `(entry, entry+8)` 的：无（`bl entry` 调用是正常的，已排除）。
2. **数据引用**：`ADRP+ADD/LDR/STR` 计算地址、`ldr` 字面量池、`adr` 落在 `[entry, entry+8)` 的：无 → 被覆盖的 8 字节不是任何数据/字面量池。
3. **指针字**：payload 中 u32/u64 等于入口的、以及 `0xFFFF0000|(link_base+entry)` 标记指针（先反推 link_base=0x50F00000）：无。
   （补充：两个函数也没有任何 `adrp+add` 取地址引用，即不存在函数指针/间接调用；dispatcher 的调用者只有两个直接 `bl`。）

### 5.5 补丁影响面

- 只改写两个函数的前 8 字节，**文件大小不变**、**0x200 头逐字节不变**、**payload 之外（cert1/cert2/bl2_ext/aee/lk_main_dtb）不变**；
- 所有 `androidboot.*` / `verifiedbootstate` / `veritymode` / `atm` 字符串出现次数前后不变（补丁不触碰文本区；验证器已打印 "guard strings unchanged"）；
- 字符串本身保留（可选"清空文本"是另一个独立补丁 B，两者互不依赖）。

---

## 6. 整镜像"警告/状态相关"站点全表（分类结论）

> 原则：**只补丁两个函数**；其余文本/函数分三类——"内核 cmdline 守卫串（绝不修改）"、"日志/功能路径（不应修改）"、"未被引用的遗留字符串（不改）"。

### 6.1 真正用户可见的启动警告路径（本工具的目标，2 处）

| 类别 | 函数 | 证据 |
|---|---|---|
| orange_state + red_state + 5s | 0x4D728（file 0x4D928） | 5 条警告串引用 + cmp #1/#3/#2 链 + movz/movk 0x4C4B40 |
| dm_verity + 3s | 0x4CA58（file 0x4CC58） | 5 条警告串引用 + 30 × movz/movk 0x186A0 |

### 6.2 内核 cmdline / 引导参数守卫串（**绝不修改**，仅按出现次数校验）

| 字符串 | payload 偏移 | 引用方式 / 使用位置 |
|---|---|---|
| `androidboot.verifiedbootstate=green/yellow/orange/red` | 0xACBB7 / 0xACB70 / 0xACB4B / 0xACB95 | 标记指针表 0xACC90 → 函数 0x4D6EC → cmdline 追加 0x10A04 |
| `androidboot.veritymode` / `androidboot.veritymode.managed` | 0xB6754 / 0xB676B | 0x7B084（libavb） |
| `androidboot.atm=enable` / `=disabled` | 0x959D6 / 0x95A21 | 0xBF84 内两处（0xCBA8 / 0xC9CC），cmdline 拼接 |
| `androidboot.*`（共 50 处） | 0x94621、0x947B0、0x94AA5、0x95104、… | cmdline 拼接/内核参数处理（0xAC20、0xBCD4、0xBE34、0x1058C、0x31BA8 等） |
| `androidboot.vbmeta.*`、`avb.*`、`vbmeta` | 0xACAFD、0xACB19、0xACB31、0xB6620…、0xAC7ED | libavb（0x7B084/0x7D0C8/0x7D2FC/0x802FC 等） |
| `androidboot.boot_devices=…` | 0xABF60 / 0xAC64A | 0x4888C / 0x4C87C |
| `androidboot.meta_log_disable`、`usbconfig`、`mblogenable` 等 | 0x95170… | 0xBCD4/0xBE34 |

工具实现要求：应用补丁前后对这些串做 `count()` 对比，**任何一个次数变化 → 拒绝写入**（spec §5.4）。

### 6.3 其它警告/日志/功能路径（不是目标，不要补丁）

| 位置 | 内容 | 说明 |
|---|---|---|
| 0x938A8 / 0x93C15 字符串，引用于 0x697C / 0x6C14 | `No  (Auto exit after 5 seconds): Do not unlock/lock bootloader.` | **fastboot 解锁/上锁菜单**的 5 秒超时，不是开机警告；tfast 面向这些函数做解锁补丁 |
| 0x697C / 0x6C14 / 0x6884 / 0x6A4 | `Unlock bootloader?` / `Lock bootloader?` / `Start unlock flow` 等 | fastboot 解锁/上锁流程（tfast unlocked 样本改的就是这一组，见 §7） |
| 0x4CE88 | `[FASTBOOT] Start reset dm-verity status.\n` | fastboot 命令日志 |
| 0x4CC78 / 0x4CD44 | `[%s] set_dm_verity_state fails(0x%x)` / `get_dm_verity_status` / `clear_dm_verity_status` | dm-verity 状态读写/日志，不是用户警告 |
| 0x7B084 | `restart_on_corruption` / `ignore_corruption` | **内核 cmdline 取值**（dm-verity 模式），不是警告文本 |
| 0xAC6A0 附近 `s\n`、0xAC782 等 | 字符串池中的片段 | 非独立消息 |

### 6.4 未被任何代码引用的遗留字符串（**不要改**，改了也没有效果）

| 字符串 | payload 偏移 | 检查结论 |
|---|---|---|
| `Your device will boot in 5 seconds\n` | 0xB28AA | 无 ADRP+ADD、无标记指针、无 movz/movk 绝对地址引用 |
| `Your device will continue boot with POWER key\n` | 0xB28CE | 同上 |
| `Otherwise your device will shutdown after 5 seconds\n` | 0xB28FD | 同上 |
| `[Efuse] hw sbc is enable\n` | 0xB2932 | 同上 |

> 假设（未验证）：这些字符串属于该 LK 的"启动菜单/按键倒计时"功能，在本固件/配置下未编译进可执行路径（或通过本工具未识别的机制引用）。它们**不是** Orange/Red 警告路径；即使清零也不会去 5 秒等待。列入 README 的"失败模式/不处理项"。

---

## 7. lk_a / lk_b / lk_a.unlocked 对比

### 7.1 lk_a（A 槽）vs lk_b（B 槽）

- 头部逐字节相同；两个警告函数区域（0x4C40..0x4D80 范围外的 0x4D700..0x4D7F0、0x4CA40..0x4CC70）**逐字节相同**；
- 整文件差异：**114 个区间 / 2,955 字节**（与 Lead 报告的 2,955 一致）；其中 **payload 内仅 92 区间 / 847 字节**，集中在：
  - 0x29D04 附近（`adrp` 目标/数据指针调整，疑似同代码不同链接布局）；
  - 0x7BDA5、0x80000~0x80DF5 一带（版本/构建相关数据）；
  - 0xB18F3 起（版本号 ASCII，如 `7221528` vs `9040359`）；
  - 0xB78AF~0xB8B70（libavb 错误文本/ASN.1 常量区）；
  - 0xD3CB0~0xD3D28（数据表）；
  - **警告字符串、两个警告函数、5s/3s 常量、状态表均无差异**。
- payload 之外差异 2,108 字节，位于 cert1/cert2/bl2_ext/aee/lk_main_dtb 子镜像（证书 blob/DTB 等与槽位相关信息），不影响 lk 代码。
- 结论：**A/B 双槽的 lk 代码相同，两个槽位都必须刷补丁**（只刷一个仍可能从另一槽启动、警告仍在）。工具应在同目录发现 `lk2.img`/`lk_b.bin` 时提示。

### 7.2 lk_a vs lk_a.unlocked（第三方 tfast 解锁输出，用于交叉印证）

- 差异 **17 区间 / 106 字节**，全部在解锁相关函数与版本串，**两个警告函数区域无差异**：
  - 0x6AE8：`cbz w0,#0x6af8` → 无条件 `b`（跳过解锁校验）；
  - 0x6AFC：`tbz w0,#0,0x6b58` → `nop`；
  - 0x6EC8：函数入口被改写为 `mov w8,#1; str w8,[x0]; ret; nop`（强制返回"已解锁"标志）；
  - 0x2AF1C：`mov w8,#4` → `mov w8,#3`（LKS_LOCK(4) → LKS_UNLOCK(3)）；
  - 0x78FEC：入口改写为 `str wzr,[x0]; mov w0,wzr; ret` + NOP 填充；
  - 0x795E8：`bl 0x78fec` → `mov w0,wzr`；
  - 0xB18E4 等：版本/构建字符串。
- 结论：
  1. 解锁补丁与警告补丁是**两套互不相干的改动**；tfast 工具不管开机警告，去警告工具不必碰解锁函数；
  2. tfast 的真实补丁同样使用 `E0 03 1F 2A C0 03 5F D6`（`mov w0,wzr; ret`）作为函数入口 stub —— 本方案补丁风格与真实工具一致，降低"补丁点写法"风险；
  3. 该样本可直接作为检测器的第三个真实样本：两个警告函数仍应被识别（验证器 PASS）。

---

## 8. AArch64 通用识别算法（不依赖固定偏移）

### 8.1 算法（`rules_lk.json` → `aarch64_heuristic`，`validate_detector.py` 实现）

1. **解析镜像**：合法 MTK 头（magic 0x58881688、`0x200+size<=文件`）→ `payload=data[0x200:0x200+size]`，否则整文件当裸 payload；同时记录后续打包子镜像（用于越界断言）。
2. **判架构**：统计 `C0 03 5F D6`（AArch64 ret）与 `70 47`（Thumb bx lr），并用 capstone 抽样统计 `stp x29,x30,[sp,...]` 前导；三者投票。
3. **字符串锚点**：在 payload 中搜索警告文本键（`Orange State`、`Red State`、`OS Not Being Verified`、`Dismiss After 5 Seconds`、`Device Cannot Boot`；dm-verity 用 `dm-verity corruption`、`Your device is corrupt`、`It can't be trusted`、`Device will continue to bootup`、`tran_dm_fix`、`Device occur dm vertity corruption`）。
4. **引用发现**：线性扫 4 字节对齐的 `adrp`，向后 1~3 条找同寄存器 `add Xd,Xd,#imm`，计算 target；target 处能取出可打印 C 字符串且命中警告键 → 记录 (ref, string)。
5. **函数定位**：由引用点**反向**扫描最多 `max_backtrack=0x500` 字节，找最近的 `stp x29,x30,[sp,#-imm]!` / `sub sp,sp,#imm` / `stp x29,x30,[sp,#imm]`（三档补丁前导模式都支持）。
6. **函数边界**：从入口正向找 `ldp x29,x30,[sp],#imm` 后的第一条 `ret`（找不到则第一条 ret / 上限 0x800）。
7. **分类**：
   - **分发器**：同一函数内引用 ≥2 条警告键（或至少 Orange/Red 之一且同时满足 cmp 链 / 5s 常量）；且（state cmp 链 `cmp w?,#1/#2/#3` ≥2 次 或 存在 5,000,000µs 常量）→ 高置信 0.95（三要素全齐）/ 0.85；
   - **dm-verity**：引用 ≥2 条 dm-verity 消息键（且不含 Orange/Red），或 ≥1 条且存在 100,000µs ×≥10 的延时循环 → 0.95/0.85；
   - 后缀："dm_verity" 泛 token（日志/状态函数名）只做弱证据，不单独构成分类（避免把 `set_dm_verity_state fails` 日志函数误判）。
8. **补丁与安全检查**：入口 8 字节 → `mov w0,wzr; ret`；输出 before/after 反汇编、调用者、三类引用检查（§5.4）、guard 字符串次数对比。

### 8.2 规则库（`analysis/lk/rules_lk.json`）

- `version: 1`，`rules` 共 15 条：
  - **ARM32-Thumb 13 条**：orange_state 10（4 个规格签名 + 2 个通配变体 + 4 个"补丁 A" push/pop 变体）、red_state 1、dm_verity 2；
  - **AArch64 2 条启发式**（`kind: "heuristic"`，附 `params`：字符串键集合、`state_cmp_chain`、`delay_constants`（含精确字节）、`min_warning_refs`、排除条件）；
  - 另有顶层 `aarch64_heuristic` 参数块（prologues / max_backtrack / ref_delta_max / warning_strings / delay_constants / guard_do_not_touch / warning_text_clear_keys）供 core-dev 的检测器直接加载；
  - `guard_strings` 与 `patch` 常量、`verified_samples`（3 个真实样本的 sha256/偏移）、`mtk_layout_verified`（镜像结构修正）也在同一文件。
- ARM32 7 条签名来源：
  - `lkpatcher/patches.json`（GPL-3.0 仓库，仅用特征码事实）：`08b50a4b7b441b681b68022b`、`08b50e4b7b441b681b68022b`、`f0b5002489b0`、`30b583b002ab0022`、`022800d0704738b53224e0f7`；
  - `modlk/lk_core.py`（MIT）：`7B441B681B68012B`/`7B441B681B68022B` + 前缀 `08B5` → 补出 012b 两条变体；
  - "补丁 A"变体 `08B5002008BD`（push {r3,lr}; movs r0,#0; pop {r3,pc}）来自 modlk PATCH_A_LEAD（Hovatek thread-31664 方法）。
  - 与 spec §3.4 的 7 条完全一致；本规则库额外给出 2 条通配变体（第 2 个 halfword 通配，覆盖其它字面量池偏移）与 4 条 push/pop 变体。

### 8.3 验证器输出（`validate_detector.py`）

```
$ python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin
=== sample: lk_a.bin ===
  MTK container    : yes  name='lk' size(payload len)=870352 (0xd47d0) ext_magic=0x58891689
  packed sub-images: cert1@0xd49d0(size 1733), cert2@0xd52a0(size 982), bl2_ext@0xd5880(size 610400), ...
  arch detected: aarch64  {'aarch64_ret': 1860, 'thumb_bx_lr': 0, 'arm_bx_lr': 0, 'stp_x29_x30': 295}
[PASS] a64-orange-red-state-dispatcher   cat=orange_state
        function : payload 0x04d728..0x04d7ec  (file 0x04d928..0x04d9ec)
        confidence: 0.95  warning keys: Device Cannot Boot, Dismiss After 5 Seconds,
                    OS Not Being Verified, Orange State, Red State
        state cmp chain: [1, 3, 2]   delay(5,000,000us): 1   delay(100,000us): 0
        callers (bl/b): ['0x4d21c', '0x4d22c']
        safety: branches into [entry,entry+8): none / data refs: none / pointer words: none
        guard strings ... : unchanged
        before : fd 7b be a9 f3 0b 00 f9  ->  stp x29, x30, [sp, #-0x20]! ; str x19, [sp, #0x10]
        after  : e0 03 1f 2a c0 03 5f d6  ->  mov w0, wzr ; ret
...
[PASS] orange_state payload 0x04d728 (file 0x04d928)
[PASS] dm_verity    payload 0x04ca58 (file 0x04cc58)
RESULT: PASS (2 finding(s), 2 expected offset(s), safety OK)

$ python analysis/lk/validate_detector.py --all
=== lk_a.bin ===            RESULT: PASS (2 findings, 2 expected, safety OK)
=== lk_a.unlocked.bin ===   RESULT: PASS (2 findings, 2 expected, safety OK)
=== lk_b.bin ===            RESULT: PASS (2 findings, 2 expected, safety OK)
OVERALL: PASS

$ python analysis/lk/validate_detector.py --synthetic-arm32
  ARM32 summary: 13/13 rules PASS
RESULT: PASS (13/13 ARM32 rules PASS)

$ python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin --simulate-patch
=== simulate patch (in memory, no file written): lk_a.bin ===
  [PASS] orange_state payload 0x04d728: re-detected as ALREADY PATCHED
         disasm: mov w0, wzr ; ret
  [PASS] dm_verity    payload 0x04ca58: re-detected as ALREADY PATCHED
         disasm: mov w0, wzr ; ret
  file size unchanged: True   header(0x200) unchanged: True   changed bytes outside lk payload: 0
  simulate RESULT: PASS
```

> 幂等性：检测器会先在回溯窗口内识别"入口 8 字节已经是补丁"的函数（要求其后 0x400 字节内仍有 ≥2 条警告串引用，避免把数据中的巧合字节当成入口），因此对已打补丁的镜像会报 `already_patched` 而不会把引用错误归类到前一个函数（该边界情况已修复并通过 `--simulate-patch` 验证）。

> 说明：`--all` 还会顺带扫描 `analysis/lk/samples/` 下运行期产生的 `*_original_backup*.bin`（其它成员测试备份），对未知文件名用"通用验收"（≥1 个高置信、safety OK）判 PASS。

### 8.4 布局漂移测试（直接回答任务问题）

**问题：如果代码布局变化（偏移漂移），同一套通用检测算法还能找到这两个函数吗？**

能（在合理范围内），因为检测**不以任何固定偏移为锚**：

- 锚点 = 警告字符串内容 + `ADRP+ADD` 的**计算结果**（等于字符串在 payload 内的偏移），与函数/字符串的绝对位置无关；
- 结构判据 = 函数前导模式 + 状态 cmp 链 + 延时 MOVZ/MOVK 常量；
- 实测：把 payload 整体前移 0x1000 字节（页对齐，等价于链接/放置位置变化），`--shift 0x1000`：

```
=== offset-invariance test: lk_a.bin payload shifted by 0x1000 ===
  [PASS] orange_state expected payload 0x04e728 (orig 0x04d728+0x1000) got ['0x4e728']
  [PASS] dm_verity    expected payload 0x04da58 (orig 0x04ca58+0x1000) got ['0x4da58']
  shifted RESULT: PASS
```

即：只要"代码与字符串一起移动、且 payload 映射基址页对齐"，函数位置变化不影响识别；补丁偏移相应变成 `entry+shift+0x200`。

**会失败/误判的布局变化**：
1. 字符串被移到**另一个分区/段**（例如 DT/logo 分区）而代码仍在 lk → `ADRP+ADD` 计算 target 落在 payload 之外，检测不到（fail-safe，不写文件）；
2. 链接基址非页对齐（AArch64 代码映像极罕见）→ adrp 页计算与 payload 偏移不对应；
3. 编译器换成 `ldr literal` / movz+movk 绝对地址取字符串（本样本未观察到）→ 需扩展引用发现（可加"字面量池指针"扫描，代价是误报增加）；
4. 警告逻辑被**内联**进更大的调用方：反向扫描会找到调用方函数入口，可能把整个 boot flow 当成分发器打补丁 → **危险**。缓解建议（给 core-dev）：加分片守卫——检测到的函数长度 ≤0x1000、且"入口在函数首部、警告串引用≥2、存在 cmp 链或延时常量"三者同时成立才允许自动补丁；否则列候选让用户确认。
5. 警告被拆成多个小函数（每个 state 一个函数，各只引用 1 条字符串）→ 本算法靠 `≥2 key 或 (Orange/Red + cmp链/延时)` 兜底，仍可能只命中一个；未命中则 fail-safe。

---

## 9. 跨厂商 / 其它 LK 的失败模式

| 场景 | 现象 | 本工具行为 |
|---|---|---|
| 老机型 ARM32 Thumb-2 LK | 无 AArch64 ret；Thumb 特征码 | 走 ARM32 规则库（7 签名 + 变体）；命中次数必须==1，否则列候选 |
| ARM32 但特征码被厂商改过（寄存器不同/加了偏移） | 7 条签名全不中 | fail-safe：不支持，给出中文原因；可加载自定义规则 |
| 高通 ABL / UEFI、三星、其它厂商 | 不是 MTK LK（无 MTK magic / 无这些字符串） | fail-safe 不支持 |
| 已做"补丁 B"（警告文本被清零）但分发器完好 | 警告串缺失 → AArch64 检测不到 | fail-safe；用户可手工提供偏移或先还原文本 |
| 已打过分发器补丁 | 入口前 8 字节已是 `e0 03 1f 2a c0 03 5f d6` | 报告 `already_patched`，不重复写入 |
| 警告函数被内联/改动（见 §8.4-4） | 函数边界变大/入口偏移 | 高置信失败或需人工确认；建议 core 端加长度/结构守卫 |
| dm-verity 警告走 kernel/AVB2 层 | LK 内无对应文本 | 只能去 LK 阶段警告；README/帮助里说明"不等于关闭全部校验" |
| 有签名校验的 LK / secure boot | 改字节可能被 BootROM/preloader 拒绝 | 规格 §5/§8 已提示需要解锁 seccfg；本仓库无真机，**未验证** |
| 打包镜像（本样本） | 4MB 内含 cert1/cert2/bl2_ext/aee/lk_main_dtb | 只改第一个 lk payload；越界即拒绝 |
| A/B 双槽 | 只刷一个槽 | 工具提示必须两槽都刷（本样本 A/B 的警告函数逐字节相同） |

---

## 10. 风险、限制与未验证假设

**已验证（有字节级证据）**：
- 两个函数的位置、边界、完整指令序列、字符串引用、调用者、延时常量、状态映射；
- 补丁前后语义（返回值/栈/寄存器/引用面）；
- 3 个真实样本 + 布局漂移 + ARM32 合成样本上的检测器行为。

**未验证（标注为假设）**：
1. 补丁未在真机验证（本仓库无设备）；"开机不再显示警告/不再等待"是静态推断（控制流上必然跳过打印与延时）。
2. `lk_main_dtb`/`bl2_ext`/`cert1/2` 子镜像的用途与是否参与 lk 校验未完全逆向；本工具不改它们，但**不能证明**改 lk 后不会被其它阶段校验拒绝（secure boot 设备）。
3. "未被引用的 0xB28AA 等字符串属于未启用的启动菜单"是假设；不影响补丁正确性。
4. 函数 0x4CEB4（boot flow）的**直接调用者未找到**（无 `bl/b`、无 `adrp+add`、无标记指针）；可能经未识别的间接调用/汇编跳板。不影响补丁（分发器/警告函数的调用点已确证）。
5. 其它厂商 LK 的失败模式来自推理与公开资料，未逐厂商验证。

**已知限制**：
- AArch64 检测为启发式：不同厂商 LK 的字符串文本/结构变化可能识别失败（fail-safe，不写文件）；
- ARM32 只有 7+变体签名，覆盖有限；
- 去 LK 警告 ≠ 关闭 dm-verity/AVB2 校验；
- 刷写 lk 有变砖风险，务必先备份、只用于自己拥有/已获授权设备。

---

## 11. 复现命令与证据文件

```bash
# 1) 深度反汇编证据（生成 _evidence_a64.txt）
python analysis/lk/tools/analyze_a64.py lk_a.bin

# 2) 字符串/函数分类证据（生成 _classify.txt）
python analysis/lk/tools/classify_a64.py

# 3) 目标化查询（延时计数、字符串引用、调用者）
python analysis/lk/tools/query_a64.py

# 4) 规则库验证（正式交付物；退出码 0 = 期望偏移全部命中且 safety OK）
python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin
python analysis/lk/validate_detector.py analysis/lk/samples/lk_b.bin
python analysis/lk/validate_detector.py --all
python analysis/lk/validate_detector.py --all --shift 0x1000
python analysis/lk/validate_detector.py --synthetic-arm32
python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin --simulate-patch

# 5) 查看完整函数反汇编（含 30 次延时块）
python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin --dump
```

证据文件（均在 `analysis/lk/`）：
- `_evidence_a64.txt`：两个函数的完整反汇编 + 全镜像 194 条警告串引用 + 分组；
- `_classify.txt`：6,382 条 ADRP+ADD 引用 → 按函数分组的警告相关/守卫串函数清单；
- `_query1.txt`：字符串原文、延时调用计数（30）、调用者区间反汇编、关键字符串引用；
- `_validate_a.txt` / `_validate_all.txt` / `_validate_arm32.txt`：验证器完整输出；
- `_evidence*.txt` 等为可再生成的中间证据；`tools/` 为分析脚本；`samples/` 为本地样本（gitignored，不随仓库分发）；`tools/refs/` 为 tfast 参考源码副本（专有许可，仅本地交叉核对，不复制其代码）。
