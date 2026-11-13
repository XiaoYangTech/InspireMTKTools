# MTK `logo.bin` / `lk.bin` 容器格式规范（重构参考）
# MTK `logo.bin` / `lk.bin` Container Format Specification

> 状态 / Status: 基于 `LogoBuilder v1.6` 附带样片（`logo.bin`, `lk.bin`, `sizes.txt`）的**静态二进制分析**，
> 并用原程序 `LogoBuilder.exe`（.NET 程序集，通过反射调用其内部方法）做**行为交叉验证**。
> 证据等级标记：**[Verified]** = 样片字节直接验证；**[Tool-verified]** = 用原程序行为验证；
> **[Hypothesis]** = 推断/未验证。
> 分析工具：`analysis/format/parse_logo.py`（提取 + 回打包，纯 stdlib + Pillow）。
> 本文档只描述格式，不包含任何原始文件内容。

> **本文档由 Lead 采用为重建实现依据，与 `docs/spec_original.md` 互为交叉验证。**
> (Adopted by the Lead as the implementation basis for the rebuild; cross-validated against `docs/spec_original.md`.)

---

## 1. 样片与指纹 (Samples and fingerprints) [Verified]

| 文件 | 大小 (bytes) | SHA-256 | magic @0 | size @4 | tag @8 | 结论 |
|---|---:|---|---|---|---|---|
| `logo.bin` | 2,006,471 (0x1E9DC7) | `c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c` | 0x58881688 | 2,005,959 (0x1E9BC7) = 大小-512 | `LOGO\0\0\0\0` | LOGO 块容器（51 个 zlib 块） |
| `lk.bin` | 294,644 (0x47EF4) | `5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55` | 0x58881688 | 294,132 (0x47CF4) = 大小-512 | `LK\0\0\0\0\0\0` | **不是** LOGO 块容器；同一 MTK 头 + 裸 ARM LK 引导程序 (§8) |
| `sizes.txt` | 492 | （见 §9） | — | — | — | 外部“缓冲长度→(W,H,BPP)”映射表（49 行） |

所有整数均为**小端 (little-endian)**。`size` 字段恒等于 `filesize - 512`。

---

## 2. 通用 MTK 分区镜像头（前 512 字节 / 0x200）
## 2. Common MTK partition-image header (first 512 bytes / 0x200) [Verified]

| 偏移 (offset) | 大小 | 类型 | `logo.bin` 值 | `lk.bin` 值 | 含义 / meaning |
|---|---:|---|---|---|---|
| 0x000 | 4 | u32 LE | `88 16 88 58` = 0x58881688 | 同左 | 魔数 `MTK_MAGIC` (magic) |
| 0x004 | 4 | u32 LE | 0x001E9BC7 = 2,005,959 | 0x00047CF4 = 294,132 | 负载长度 = `filesize - 0x200`；LOGO 容器中等于 TOC total（§3） |
| 0x008 | 8 | char[8] | `"LOGO\0\0\0\0"` | `"LK\0\0\0\0\0\0"` | 镜像名/类型标签 (ASCII, NUL 填充) |
| 0x010 | 24 | bytes | 24 × `00` | 24 × `00` | 保留/未使用 (reserved, unused) —— 原程序不解释；改字节不影响解析 [Tool-verified] |
| 0x028 | 472 | bytes | 472 × `FF` | 472 × `FF` | 0xFF 填充至 512 (padding) |
| 0x200 | — | — | 块表开始 | 负载开始 | 见 §3 / §8 |

要点 / Notes：
* **[Tool-verified]** 原程序 `CreateProject` 只校验块表长度（TOC total，见 §3），**不校验** magic/name：
  把 magic[0] 改为 0、把 tag 改为 `LOGX`、修改 header@4、修改保留区字节，仍能正常解出 51 块。
  （但重构程序**应当**在写入时使用 0x58881688 + `"LOGO"`，且真机 LK/内核可能校验 magic，属 [Hypothesis]。）
* **[Tool-verified]** 打包时，原程序把项目里保存的 512 字节 header 原样写回，**只重算偏移 0x04 的 size 字段**；
  `[0x00:0x04]` 与 `[0x08:0x200]`（含 0x10–0x27 保留区和 0x28–0x1FF 的 0xFF 填充）都逐字节保留。
  因此“标准”文件的两个 0xFF/0x00 区间是约定，而不是报错点。
* 0x10–0x27 的 24 个 0 字节具体语义未在样片中出现非零值，标记 **[Hypothesis] unused**。

---

## 3. `logo.bin` 块表（TOC, Table of Contents）[Verified]

块表紧接在 512 字节头之后，位于文件偏移 **0x200**：

| 偏移 | 大小 | 类型 | 样片值 | 含义 |
|---|---:|---|---|---|
| 0x200 | 4 | u32 LE | 51 (0x33) | **块数量** `count`（= 图像/二进制块个数） |
| 0x204 | 4 | u32 LE | 2,005,959 (0x1E9BC7) | **数据总长** `total` = 从 0x200 起的全部负载长度；必须等于 `filesize - 512` |
| 0x208 | 4×count | u32 LE[] | 见 §10 | **块偏移** `offset[i]`，相对 **0x200**，严格递增 |

布局公式 / layout formulas：

```
表长度 table_size   = 8 + 4 * count                (样片: 8 + 4*51 = 212 = 0xD4)
数据区起点 data_start = 0x200 + table_size           (样片: 0x200 + 0xD4 = 0x2D4)
块 i 的压缩数据 (stored bytes) = file[0x200+offset[i] : 0x200+offset[i+1])   (i < count-1)
最后一块 (last block)          = file[0x200+offset[count-1] : 0x200+total) = file[.. EOF]
total = filesize - 512   且   offset[0] = table_size
```

约束（样片全部成立；原程序行为见下）[Verified]/[Tool-verified]：

1. `offset[0] == table_size`（数据区紧接块表，无空洞）。
2. `offset[i] < offset[i+1]`（严格递增；块与块之间**没有任何对齐/填充字节**）。
3. `offset[count-1] < total`；最后一块长度由 `total - offset[count-1]` 推导（文件里**不保存**逐块长度）。
4. `total == filesize - 512`。原程序 `CreateProjectExtractImages` 强制该等式，不满足则 `ErrorCode=3` 并抛错。
5. `count` 必须使 `8 + 4*count <= total`；否则原程序抛 `ArgumentOutOfRangeException` → `ErrorCode=6`。
6. **无** 魔数/版本/每块 W/H/BPP/压缩标志等字段 —— 只有 count、total、offsets 三项。

原程序对畸形 TOC 的行为（用改字节的 `logo.bin` 实测）[Tool-verified]：

| 修改 | 结果 |
|---|---|
| `count` 改为 50 | 解出 50 块：最后一块变成“两段 zlib 流拼接”，zlib 在第一个流结束处停止，第 51 张图丢失 |
| `total` 改为 total-100 | 最后一块被截断；原程序 zlib 解压容忍截断→得到残缺 raw→不在 sizes 表→存为 **binary 块** `block51.dat` |
| `count=50` 且 `total=51`（极端） | 出现负长度 → `ArgumentOutOfRangeException`，`ErrorCode=6` |
| header@4 改值 / magic / tag / 0x10 字节 | **不影响解析**（仍 51 块） |

---

## 4. 块内容 (block payload) [Verified]

* 每个块 = **一个完整 zlib 流 (RFC 1950)**：2 字节头恒为 `78 DA`
  （CMF=0x78：32KB window；FLG=0xDA：FLEVEL=3，即 **level 9 / best compression**），
  后接 DEFLATE 数据 + 4 字节 Adler-32；块尾没有额外对齐/填充。
* 解压后的数据 `raw` = **原始帧缓冲 (raw framebuffer)**，不带任何自身头。
* **尺寸 (W,H,BPP) 不保存在 logo.bin 里**。原程序建立外部映射
  `key = raw_len = W*H*BPP` → `(W,H,BPP)`（来自 `sizes.txt`，见 §9）：
  * `key` 命中 → 图像块 `image:`，用对应 W/H/BPP 解码；
  * `key` 未命中 → 二进制块 `binary:`（原程序存成 `blockN.dat`，打包时原样压缩回去）。
  * 样片 51 块全部命中 sizes 表，无二进制块。
* `raw_len` 出现过的 6 种取值（对应 6 种尺寸）：见 §10。
* 32bpp 判定启发式 `LooksLike32Bit(buf)`（原程序公开静态方法，行为实测）[Tool-verified]：
  `len(buf) % 4 == 0 且 buf[3::4] 全部等于 0xFF`（即把第 4 字节当 alpha，全不透明）。
  样片 51 块的该字节恒为 0xFF。

---

## 5. 像素格式与方向 (pixel layout & orientation) [Verified]

统一约定：**逐行存储、行内从左到右、第 0 行 = 图像的最上面一行（top-down）**；
`stride = W * bytes_per_pixel`，**无行尾 padding**；容器格式/原程序对图像**不做**任何翻转或旋转
（原程序 `StreamToImage` 对 2x2 合成像素实测：raw 第 0 行 → Bitmap 第 0 行）。

> 注意（内容层面，非格式）：样片里**不同块的图像内容朝向并不统一**——
> blocks 0, 41–44, 45–48 等整屏 logo 的内容是以**旋转 180°** 存放的（把 PNG 旋转 180° 后
> “WELCOME / alcatel / MEDIATEK / GALAXY …” 才正读），而充电数字/电池动画帧 blocks 4–35
> 是按正常朝向存放的。解码器**不应**做任何翻转；这是素材来源/面板安装方向的差异
> （可能来自面板倒装的机型或来自不同 ROM 的素材）[Hypothesis]，格式里没有朝向标志。

### 5.1 BPP = 4（32bpp，样片全部为此）

文件中的 4 字节 = **B, G, R, A**（小端 32 位 ARGB 的字节序，即 BGRA）：
`pixel[i] = bytes[4i+0]=B, [1]=G, [2]=R, [3]=A`（alpha 保留，样片恒 0xFF，但解码/编码应保留）。

```
解码 decode:  R = b[4i+2]; G = b[4i+1]; B = b[4i+0]; A = b[4i+3]
编码 encode:  b[4i+0]=B;    b[4i+1]=G;    b[4i+2]=R;    b[4i+3]=A
```

验证：原程序 `StreamToImage` 对合成 4 字节 `01 02 03 04` 得到 R=3,G=2,B=1,A=4；
`ImageToStream` 对 (R,G,B,A)=(10,20,30,255) 写出 `1E 14 0A FF`（B,G,R,A）。

### 5.2 BPP = 2（16bpp RGB565；样片无此块，用原程序合成验证）[Tool-verified]

文件中的 2 字节 = **u16 little-endian RGB565**：

```
位域:  [15:11]=R5   [10:5]=G6   [4:0]=B5
解码:  R = (v >> 11 & 0x1F) << 3      (即 (v & 0xF800) >> 8)
       G = (v >>  5 & 0x3F) << 2      (即 (v & 0x07E0) >> 3)
       B = (v       & 0x1F) << 3      (即 (v & 0x001F) << 3)
       A = 255
编码:  v = ((R>>3) & 0x1F) << 11 | ((G>>2) & 0x3F) << 5 | ((B>>3) & 0x1F)
       写出 u16 LE: 低字节先
```

注意是**简单截断/左移**（不是 5→8 位复制扩展）：`0xF800→(248,0,0)`、`0x07E0→(0,252,0)`、
`0x001F→(0,0,248)`、`0x1234→(16,68,160)`、`0x8410→(128,128,128)`；
反向 `248→0xF800`、`255→0xFFFF`。以上均由原程序 `StreamToImage`/`ImageToStream` 实测。

---

## 6. 压缩实现与“逐字节一致”结论 (compressor identity) [Verified]

样片 51 个 zlib 流全部为 level 9（`78 DA`）。不同 zlib 实现产生的 DEFLATE 字节可能不同：

| 压缩实现 | 与样片 51 个流逐字节一致的数量 |
|---|---:|
| **标准 zlib 1.3.1 / 1.3.2**（`compress2(dest, len, src, len, 9)`，C 实现） | **51 / 51（全部一致）** |
| Ionic.Zlib（随 `LogoBuilder` 附带的 `Ionic.Zip.dll`，旧 zlib 的 C# 移植）Level 9 | 46 / 51（5 个较大的流略大，共 +1066 字节） |
| 本环境 Python 3.14 内置 `zlib` 模块（实为 **zlib-ng 1.3.1**） | 14 / 51（zlib-ng 压缩率更高、字节不同） |

* 原程序自己的 `CompressStream` 输出 = **Ionic.Zlib Level 9**（反射调用实测），
  这也解释了为什么用原程序解包再打包 `logo.bin` 得到 2,007,537 字节
  （仅 5 个流不同：block 1,41,42,43,44；解压后的 raw 完全相同），SHA-256 与原文件不同。
* **结论 / consequence**：
  1. 设备端只需要“解压后的帧缓冲”正确，压缩流字节差异不影响使用；
  2. 若要求**容器逐字节还原**（回归测试/签名比对），必须 **保留原始 compressed stream**，
     或使用标准 C zlib level 9（本机 `parse_logo.py` 会自动通过 `ctypes` 加载
     `zlib1.dll`，因此 `--recompress` 也能逐字节一致）；
  3. 说明原样片的压缩器是**标准 zlib（版本 ≥1.3.x 可复现；具体上游版本未逐一穷举）**，
     而不是程序自带的 Ionic.Zlib。

---

## 7. 打包算法 (pack algorithm) — 从图像列表生成 `logo.bin` [Verified]

输入：`images = [(image_i, width_i, height_i, bpp_i), ...]`（bpp ∈ {2,4}），
输出：`logo.bin` 字节流。伪代码（与实际样片/原程序行为完全一致）：

```
def pack_logo(images, header_template=None, tag=b"LOGO"):
    blocks = []                      # 每项 = zlib 压缩后的 bytes
    for (img, w, h, bpp) in images:
        img = img.convert("RGBA")
        assert img.size == (w, h)
        raw = encode_framebuffer(img, w, h, bpp)     # §5: BGRA 或 RGB565-LE
        blocks.append(zlib_compress_level9(raw))     # RFC1950; 样片等价于 C zlib level 9
    count = len(blocks)
    table_size = 8 + 4 * count
    total = table_size + sum(len(b) for b in blocks) # 总负载长度
    header = bytearray(header_template or b"\xff" * 512)
    header[0:4]   = struct.pack("<I", 0x58881688)    # magic
    header[4:8]   = struct.pack("<I", total)         # size = filesize - 512
    header[8:16]  = tag.ljust(8, b"\0")              # "LOGO\0\0\0\0"
    header[16:40] = b"\0" * 24                       # 24 个 0（保留）
    # header[40:512] 保持 0xFF 填充（若给了模板则整段保留）
    out = bytearray(header)                          # 512 字节
    out += struct.pack("<II", count, total)          # TOC: count, total
    pos = table_size
    for b in blocks:                                 # TOC: offsets（相对 0x200）
        out += struct.pack("<I", pos); pos += len(b)
    for b in blocks:                                 # 连续的 zlib 流，无填充
        out += b
    return bytes(out)                                # len(out) == 512 + total
```

要点：
* `offsets[0] = 8 + 4*count`；相邻 offset 差 = 上一块压缩后长度；最后一块到 EOF。
* 写入的 `size@4` 与 TOC 的 `total` 都等于 `len(out) - 512`；二者在样片中相同。
* 原程序打包时：header 的 `[0:4]`、`[8:512]` 来自项目保存的 header 模板，仅重算 `[4:8]`；
  块内容若来自项目图像（PNG）则重新编码+压缩；若来自 binary 块（`.dat`，即解压后原始字节）则
  重新压缩（level 9）。因此“解包→打包”可做到**解压数据无损**；压缩字节是否逐字节一致取决于 zlib 实现（§6）。
* 若某块解压长度不在 sizes 表内，它没有 (W,H,BPP) 信息，只能作为**不透明二进制块**原样保留。

---

## 8. `lk.bin` 分析结论：同一 MTK 头 + 裸 LK 引导程序，**不是** LOGO 容器 [Verified]

* 头：与 §2 相同（magic 0x58881688，`size@4=294132=filesize-512`，tag=`"LK\0\0\0\0\0\0"`，
  0x10–0x27 全 0，0x28–0x1FF 全 `FF`）。
* 0x200 起是**裸 ARM（32 位 little-endian）LK bootloader 镜像**，不是块表：
  * 0x200–0x21F 是 8 项 ARM 异常向量表，全部为 `B` 分支指令：
    `07 00 00 ea` (`b #0x21C`)、`f2 52 00 ea`、`f8 52 00 ea`、`fe 52 00 ea`、
    `04 53 00 ea`、`0a 53 00 ea`、`0a 53 00 ea`、`21 53 00 ea`；
  * 0x220 之后是典型 ARM 启动代码（`msr cpsr_*`、`ldr`、异常向量接管等），无 ELF 头；
  * 0x37C00 附近包含 MTK 分区表字符串：`PRELOADER / NULL / seccfg/SECCFG / uboot/UBOOT /
    lk/LK / boot/BOOT / bootimg/BOOTIMG / recovery/RECOVERY / secro/SECRO / logo/LOGO /
    system/SYSTEM / android/ANDROID / userdata/USERDATA ...`；
  * 全文件除偏移 0 外**没有**第二处 0x58881688；任意偏移都**没有**合法块表；**没有任何完整 zlib 流**。
* 原程序为什么拒绝它：`CreateProjectReadHeader` 读 512B 头后，取 `count=u32@0x200`（丢弃）、
  `dataLen=u32@0x204`；lk.bin 的 0x204 是 ARM 指令字节 `F2 52 00 EA` → `0xEA0052F2`（负数），
  随后 `CreateProjectExtractImages` 要求 `dataLen == filesize-512` → 失败，`ErrorCode=3`，
  `CreateProject()` 返回 False（反射调用实测）。所以拒绝原因是**块表/长度校验失败**，不是 tag。
* **重构实现建议**：解析到“magic 正确但 0x200 不是合法块表”的镜像，应作为
  **raw/opaque 容器**（一个不透明 BinaryBlock：512B 头 + 原始 payload）无损保存/还原，
  不要当成 LOGO 容器；构造 LOGO 容器时仍应写 tag `"LOGO"`。样片 `lk.bin` 的
  原样回打包（header + payload）**逐字节一致**（`parse_logo.py` 已实现，见 §12）。
  “lk.bin 是 tag=LK 的 LOGO 容器 / 第二屏”的说法对**本样片**不成立。

---

## 9. `sizes.txt`：外部尺寸表 (external size table) [Tool-verified]

原程序启动时读取本文件（`LogoProject.LoadSizes`，UTF-8 with BOM），为每行建立
`Dictionary[w*h*colorDepth] = (w, h, colorDepth)`（key 为**解压后缓冲长度**）：

```
每行格式:  WIDTH "x" HEIGHT [ "x" BPP ]         # BPP 缺省 = 2
例:        540x960x4  -> key = 540*960*4 = 2073600 -> (540, 960, 4)
           304x52x2   -> key = 304*52*2  = 31616   -> (304, 52, 2)
```

* 49 行；`ignored.txt`（可空）中的数字会进入 `ignoredSizes`，对应长度直接视为 binary 块。
* 同一个 key 重复出现时，后者覆盖前者（Dictionary 索引赋值语义）[Hypothesis: 按 IL 推断]。
* 映射并非总是一一对应（不同 `(W,H,BPP)` 可能有同一 `W*H*BPP`），所以文件本身**不足以**唯一恢复尺寸；
  必须依赖此外部表（或设备端硬编码）——这是 MTK logo 格式的固有设计。
* 样片 `logo.bin` 只用到 6 个 key（全部 bpp=4），但表里也列了 bpp=2 的分辨率（供其他机型/16bpp 镜像使用）。

完整表（49 行）：

| # | sizes.txt line | key = w*h*bpp | used by sample |
|---:|---|---:|---|
| 0 | 2560x800x2 | 4096000 |  |
| 1 | 1080x1920x2 | 4147200 |  |
| 2 | 800x1280x2 | 2048000 |  |
| 3 | 720x1280x2 | 1843200 |  |
| 4 | 600x1024x2 | 1228800 |  |
| 5 | 540x960x2 | 1036800 |  |
| 6 | 480x853x2 | 818880 |  |
| 7 | 480x854x2 | 819840 |  |
| 8 | 480x856x2 | 821760 |  |
| 9 | 480x800x2 | 768000 |  |
| 10 | 320x480x2 | 307200 |  |
| 11 | 240x320x2 | 153600 |  |
| 12 | 304x52x2 | 31616 |  |
| 13 | 304x1x2 | 608 |  |
| 14 | 218x51x2 | 22236 |  |
| 15 | 169x28x2 | 9464 |  |
| 16 | 169x1x2 | 338 |  |
| 17 | 163x29x2 | 9454 |  |
| 18 | 163x1x2 | 326 |  |
| 19 | 138x20x2 | 5520 |  |
| 20 | 138x2x2 | 552 |  |
| 21 | 135x24x2 | 6480 |  |
| 22 | 135x1x2 | 270 |  |
| 23 | 108x121x2 | 26136 |  |
| 24 | 102x1x2 | 204 |  |
| 25 | 84x121x2 | 20328 |  |
| 26 | 63x105x2 | 13230 |  |
| 27 | 57x64x2 | 7296 |  |
| 28 | 48x54x2 | 5184 |  |
| 29 | 45x139x2 | 12510 |  |
| 30 | 45x64x2 | 5760 |  |
| 31 | 38x54x2 | 4104 |  |
| 32 | 36x51x2 | 3672 |  |
| 33 | 32x105x2 | 6720 |  |
| 34 | 32x36x2 | 2304 |  |
| 35 | 30x27x2 | 1620 |  |
| 36 | 27x36x2 | 1944 |  |
| 37 | 15x27x2 | 810 |  |
| 38 | 480x854x4 | 1639680 |  |
| 39 | 38x54x4 | 8208 | yes |
| 40 | 48x54x4 | 10368 | yes |
| 41 | 135x24x4 | 12960 | yes |
| 42 | 135x1x4 | 540 | yes |
| 43 | 720x1280x4 | 3686400 | yes |
| 44 | 45x64x4 | 11520 |  |
| 45 | 57x64x4 | 14592 |  |
| 46 | 163x29x4 | 18908 |  |
| 47 | 163x1x4 | 652 |  |
| 48 | 540x960x4 | 2073600 | yes |

---

## 10. `logo.bin` 51 块完整清单 (complete block inventory) [Verified]

汇总：`count=51`，`total=2,005,959`，`table=0xD4`，`data_start=0x2D4`，
所有块 `zlib_header=78 DA`；解压后 `raw` 合计 63,582,588 字节。

| 尺寸/bpp | 块数 | 典型用途（人工目视，供参考） |
|---|---:|---|
| 540x960x4 | 27 | 开机 logo 各阶段 / 充电动画大图 / 运营商 logo |
| 720x1280x4 | 2 | 另一分辨率面板的开机 logo |
| 38x54x4 | 10 | 小数码管/进度动画帧 |
| 135x24x4 | 10 | 电池/充电动画帧 |
| 48x54x4 | 1 | 小图标帧 |
| 135x1x4 | 1 | 1 像素高进度条 |

| # | rel_off | file_off | stored | zlib | raw | WxHxBPP | PNG |
|---:|---|---:|---:|---|---:|---|---|
| 0 | 0x0000D4 | 0x0002D4 | 10068 | 78da | 2073600 | 540x960x4 | 00_540x960x4.png |
| 1 | 0x002828 | 0x002A28 | 413769 | 78da | 2073600 | 540x960x4 | 01_540x960x4.png |
| 2 | 0x067871 | 0x067A71 | 16963 | 78da | 2073600 | 540x960x4 | 02_540x960x4.png |
| 3 | 0x06BAB4 | 0x06BCB4 | 18096 | 78da | 2073600 | 540x960x4 | 03_540x960x4.png |
| 4 | 0x070164 | 0x070364 | 439 | 78da | 8208 | 38x54x4 | 04_38x54x4.png |
| 5 | 0x07031B | 0x07051B | 136 | 78da | 8208 | 38x54x4 | 05_38x54x4.png |
| 6 | 0x0703A3 | 0x0705A3 | 427 | 78da | 8208 | 38x54x4 | 06_38x54x4.png |
| 7 | 0x07054E | 0x07074E | 498 | 78da | 8208 | 38x54x4 | 07_38x54x4.png |
| 8 | 0x070740 | 0x070940 | 333 | 78da | 8208 | 38x54x4 | 08_38x54x4.png |
| 9 | 0x07088D | 0x070A8D | 467 | 78da | 8208 | 38x54x4 | 09_38x54x4.png |
| 10 | 0x070A60 | 0x070C60 | 537 | 78da | 8208 | 38x54x4 | 10_38x54x4.png |
| 11 | 0x070C79 | 0x070E79 | 264 | 78da | 8208 | 38x54x4 | 11_38x54x4.png |
| 12 | 0x070D81 | 0x070F81 | 559 | 78da | 8208 | 38x54x4 | 12_38x54x4.png |
| 13 | 0x070FB0 | 0x0711B0 | 534 | 78da | 8208 | 38x54x4 | 13_38x54x4.png |
| 14 | 0x0711C6 | 0x0713C6 | 628 | 78da | 10368 | 48x54x4 | 14_48x54x4.png |
| 15 | 0x07143A | 0x07163A | 1741 | 78da | 12960 | 135x24x4 | 15_135x24x4.png |
| 16 | 0x071B07 | 0x071D07 | 1878 | 78da | 12960 | 135x24x4 | 16_135x24x4.png |
| 17 | 0x07225D | 0x07245D | 1842 | 78da | 12960 | 135x24x4 | 17_135x24x4.png |
| 18 | 0x07298F | 0x072B8F | 1692 | 78da | 12960 | 135x24x4 | 18_135x24x4.png |
| 19 | 0x07302B | 0x07322B | 1616 | 78da | 12960 | 135x24x4 | 19_135x24x4.png |
| 20 | 0x07367B | 0x07387B | 1597 | 78da | 12960 | 135x24x4 | 20_135x24x4.png |
| 21 | 0x073CB8 | 0x073EB8 | 1669 | 78da | 12960 | 135x24x4 | 21_135x24x4.png |
| 22 | 0x07433D | 0x07453D | 1792 | 78da | 12960 | 135x24x4 | 22_135x24x4.png |
| 23 | 0x074A3D | 0x074C3D | 1830 | 78da | 12960 | 135x24x4 | 23_135x24x4.png |
| 24 | 0x075163 | 0x075363 | 1800 | 78da | 12960 | 135x24x4 | 24_135x24x4.png |
| 25 | 0x07586B | 0x075A6B | 11027 | 78da | 2073600 | 540x960x4 | 25_540x960x4.png |
| 26 | 0x07837E | 0x07857E | 11110 | 78da | 2073600 | 540x960x4 | 26_540x960x4.png |
| 27 | 0x07AEE4 | 0x07B0E4 | 11083 | 78da | 2073600 | 540x960x4 | 27_540x960x4.png |
| 28 | 0x07DA2F | 0x07DC2F | 10835 | 78da | 2073600 | 540x960x4 | 28_540x960x4.png |
| 29 | 0x080482 | 0x080682 | 10719 | 78da | 2073600 | 540x960x4 | 29_540x960x4.png |
| 30 | 0x082E61 | 0x083061 | 10690 | 78da | 2073600 | 540x960x4 | 30_540x960x4.png |
| 31 | 0x085823 | 0x085A23 | 10773 | 78da | 2073600 | 540x960x4 | 31_540x960x4.png |
| 32 | 0x088238 | 0x088438 | 10915 | 78da | 2073600 | 540x960x4 | 32_540x960x4.png |
| 33 | 0x08ACDB | 0x08AEDB | 11000 | 78da | 2073600 | 540x960x4 | 33_540x960x4.png |
| 34 | 0x08D7D3 | 0x08D9D3 | 11021 | 78da | 2073600 | 540x960x4 | 34_540x960x4.png |
| 35 | 0x0902E0 | 0x0904E0 | 10112 | 78da | 2073600 | 540x960x4 | 35_540x960x4.png |
| 36 | 0x092A60 | 0x092C60 | 401 | 78da | 540 | 135x1x4 | 36_135x1x4.png |
| 37 | 0x092BF1 | 0x092DF1 | 12048 | 78da | 2073600 | 540x960x4 | 37_540x960x4.png |
| 38 | 0x095B01 | 0x095D01 | 9940 | 78da | 2073600 | 540x960x4 | 38_540x960x4.png |
| 39 | 0x0981D5 | 0x0983D5 | 31127 | 78da | 2073600 | 540x960x4 | 39_540x960x4.png |
| 40 | 0x09FB6C | 0x09FD6C | 31237 | 78da | 2073600 | 540x960x4 | 40_540x960x4.png |
| 41 | 0x0A7571 | 0x0A7771 | 448311 | 78da | 2073600 | 540x960x4 | 41_540x960x4.png |
| 42 | 0x114CA8 | 0x114EA8 | 448447 | 78da | 2073600 | 540x960x4 | 42_540x960x4.png |
| 43 | 0x182467 | 0x182667 | 91408 | 78da | 2073600 | 540x960x4 | 43_540x960x4.png |
| 44 | 0x198977 | 0x198B77 | 90888 | 78da | 2073600 | 540x960x4 | 44_540x960x4.png |
| 45 | 0x1AEC7F | 0x1AEE7F | 31127 | 78da | 2073600 | 540x960x4 | 45_540x960x4.png |
| 46 | 0x1B6616 | 0x1B6816 | 31237 | 78da | 2073600 | 540x960x4 | 46_540x960x4.png |
| 47 | 0x1BE01B | 0x1BE21B | 31127 | 78da | 2073600 | 540x960x4 | 47_540x960x4.png |
| 48 | 0x1C59B2 | 0x1C5BB2 | 31237 | 78da | 2073600 | 540x960x4 | 48_540x960x4.png |
| 49 | 0x1CD3B7 | 0x1CD5B7 | 58376 | 78da | 3686400 | 720x1280x4 | 49_720x1280x4.png |
| 50 | 0x1DB7BF | 0x1DB9BF | 58376 | 78da | 3686400 | 720x1280x4 | 50_720x1280x4.png |

`rel_off` 为相对于 0x200 的 TOC 偏移；`file_off = 0x200 + rel_off`；`stored` 为压缩后长度；
`raw` 为解压后长度；`zlib` 为流头两字节（全部 `78da`）。

---

## 11. 提取出的图像与 SHA-256 (extracted images and hashes)

`analysis/format/extracted/` 下：51 个 PNG（命名 `NN_WxHxBpp.png`）、`manifest.json`、
`header.bin`（512B 原头）、`streams/NN.z`（原始压缩流，用于逐字节回打包）。
PNG 为 RGBA（4bpp 的 alpha 原样保留；2bpp 无 alpha → 255）。

| # | PNG sha256 |
|---:|---|
| 0 | `cb6fd6d8d5a525d59fb05b9f4537500d845f2bc38f3bb2c11d74a2314a6543a3` |
| 1 | `2c84f9556c610175a812c916e3052a832864e1b9d3355fbbc1ad0327c765db91` |
| 2 | `493dcb060597705803c9ba6cb3954998a25c40e8ac6e231522503f2202491e3a` |
| 3 | `14e10fde227992235e39a6fe5a5d0249607ff55cd0017332debf0bb13e1bc2f2` |
| 4 | `489635d3f8dec0a3bd4baf494434a857a7684dc9c03aae557f98ae667240af6f` |
| 5 | `e2098747d61740214bae497914f5ba8fceaa17ab18e585b6b7d7efb935a171e2` |
| 6 | `44c912cf943c26cd08b301427179d6ee5017a45b7f795761f35381723bb9e294` |
| 7 | `e9b0ff57c3dded51021a616b1de13e780dd81bc30cce681c3f0a83990c09c887` |
| 8 | `bba6b31421379496ee0f3b49b571cc0a19ab62b14423856beeb35c610bd0f3db` |
| 9 | `48b0201a4d24397c8d6a9d341c52f9a1a7edde5ea30b7144b2318ff97529504b` |
| 10 | `e9985344f4deef35127d04f904df691b5471fee7d8381a4c0f8c4cbc82dc6a66` |
| 11 | `484b1009c65b8030521a6870af86a4e61f295ae53fd21b5a7554efe92f19b433` |
| 12 | `ce7e0f958d9e771c07a5cb6616614c015f764acce5ef835779235ab5a1f9fa61` |
| 13 | `c03fe791ee35abfb5d42985e88627e5d78bb4fc76966b686b2db67d9d693a820` |
| 14 | `750c55aa82f8c492fddc871de6232367bb9c7022aaa773a3cc4eead233d01b11` |
| 15 | `c87bca607150652197bd78827b27a21a69a47fa77e20ea43c4fc6eef09114c99` |
| 16 | `4472bcfe4af639696c7d435fd95e7efdd36892bd40e83ba2a74a792d3a803566` |
| 17 | `345186bb1df28992dcae81acdc6c77a4bc5b68b7b521be7512386b1b82158e4e` |
| 18 | `a8ef54fb5860bd1393aa0331fad4d9b5f6a13e1a3c6265c7c775eb3077ca2cdf` |
| 19 | `d3019212605ab34683ddafc6a2e0605078dc0f860d3368de6faa9184c07398e2` |
| 20 | `e55da72d38d47c5ab97c2a92b051c234ad480a5fa292600aa1d0cb70b587ab9e` |
| 21 | `da3c600d57998fa1ae863787be789f9026397a0bc81c12a9351ad9e4e3ef3e72` |
| 22 | `904499034434004e021ba7f1ab2a986fbd5840e6159b9102860a7aefdb2acda5` |
| 23 | `65842cfc73e4c199083152a4fd704d3a125257d0d7819330e975613e1e31cdec` |
| 24 | `89ab038044926aefebd0f5eb42708280bb40efa261b63971350b634c910bcccf` |
| 25 | `be3eb4edcced2d5d2272b84b634cf400d9e8e7ecf6b1902a1b59fd4e10530e49` |
| 26 | `fe2eed969c5724817151b2fbbd9393662c0a42f7b89a969798967f38572a4c03` |
| 27 | `07399831bd4bcba60f82760205e314939c4ad290fb7baea9d2316129eb8e911f` |
| 28 | `8e0b54589110c015ee253a1f7dae69e8e2a870ba6505f008781c59f18084283f` |
| 29 | `7d5b6d6cee80c42c938e52b5688f6af8330af3cbf086d0acad19b7ffd02210c8` |
| 30 | `8ac7506aaa472a3c90f4e767ff8d18dafa58233c5082226eabd96f0641a28d41` |
| 31 | `e8d6c711db8d97268ca6a6107302469e318ddcb598b5327f94734c67134315c6` |
| 32 | `56743d8f5cee17b1cf1d892b12d7bd21efa7f3b8c62f819fd733b604e5f05c03` |
| 33 | `69c0154461f0f79bb0ee8515b53f32f35dbb76dfaa07cfbdd7b15365c1e60ec6` |
| 34 | `83127c9b489250d7a1f5f30c6f3d24db8d2c83fa22e4e65e5bb7ce7e544584c3` |
| 35 | `54982d9b28f3c0f24316b5b2622825580cfdbd8bb22211ec3a06613f1f054189` |
| 36 | `15f9dc2e1dfff5f77748cd07c850cd2e1f308c542176785e486662aff0202aac` |
| 37 | `9ad14d26edb2772ef95c2b01eab339a2c05fe6768d8cd792974d72e0e9c666a4` |
| 38 | `fe316719964730bcb5ad9bd6f074cf1c815a3f10de2edc820bd1ab08da12ef8d` |
| 39 | `93d0f2fa9050231d9dce8e70dc6c4190fe69f07c87528111c355858a8cf20bbd` |
| 40 | `4fecc7986bd67d29e66a9618c901aa75f35c81526c08b1f32f9a7f6448d9a203` |
| 41 | `95bc7431a03322db0ef648defc154c291ba6bdfae2241b30469a7f43a1d90c68` |
| 42 | `40b52b32112d96627497f089e818688dbf04c891b18d0d57ea8adf3b328281b6` |
| 43 | `90392e76d2045d673b1ebdea055acd2797760a82337b5f1977d790d89ef7c0da` |
| 44 | `24a6f0d91c2f6a035ab56e6de77941a1030587776ae3a3831e1ee5406d2f8617` |
| 45 | `93d0f2fa9050231d9dce8e70dc6c4190fe69f07c87528111c355858a8cf20bbd` |
| 46 | `4fecc7986bd67d29e66a9618c901aa75f35c81526c08b1f32f9a7f6448d9a203` |
| 47 | `93d0f2fa9050231d9dce8e70dc6c4190fe69f07c87528111c355858a8cf20bbd` |
| 48 | `4fecc7986bd67d29e66a9618c901aa75f35c81526c08b1f32f9a7f6448d9a203` |
| 49 | `7d2da1c8f1d3e5b11ccaeab4df4f4080adea9b1dfda02497b0d48d903b65e4a0` |
| 50 | `7d2da1c8f1d3e5b11ccaeab4df4f4080adea9b1dfda02497b0d48d903b65e4a0` |

---

## 12. 回打包验证 (repack verification) [Verified]

```
# 1) 提取 + 回打包（默认复用原始压缩流）
python analysis/format/parse_logo.py <logo.bin> analysis/format/extracted \
       --repack analysis/format/repacked_logo.bin
  -> repacked 51 block(s) ... reused=51 recompressed=0
  -> repack compared to input: BYTE-IDENTICAL (sha256=c321a9f1...655c)

# 2) 强制重新压缩（自动用 ctypes 加载标准 C zlib 1.3.2；实现 §7 算法）
python analysis/format/parse_logo.py <logo.bin> analysis/format/extracted \
       --repack analysis/format/repacked_logo_recompress.bin --recompress
  -> compressor: C zlib via ctypes (zlib 1.3.2)
  -> reused=0 recompressed=51
  -> BYTE-IDENTICAL (sha256=c321a9f1...655c)

# 3) lk.bin 作为 raw 容器
python analysis/format/parse_logo.py <lk.bin> analysis/format/extracted_lk \
       --repack analysis/format/repacked_lk.bin
  -> extracted 1 block(s) [kind=raw]
  -> BYTE-IDENTICAL (sha256=5bc6c099...ce55)
```

| 产物 | 大小 | SHA-256 |
|---|---:|---|
| 原 `logo.bin` | 2,006,471 | `c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c` |
| `repacked_logo.bin`（复用原始流） | 2,006,471 | 同上，**逐字节一致** |
| `repacked_logo_recompress.bin`（全部重压缩，C zlib 1.3.2 L9） | 2,006,471 | 同上，**逐字节一致** |
| 原 `lk.bin` | 294,644 | `5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55` |
| `repacked_lk.bin` | 294,644 | 同上，**逐字节一致** |
| 原程序（Ionic.Zlib L9）重打包 `logo.bin` | 2,007,537 | `edb69ede0613a6cfadee52ee21087eca30006fe0f3d37761c319bceb53e3207a`（仅 5 个流不同，解压数据相同） |

说明：`--recompress` 之所以能逐字节一致，是因为本机 Python 发行版附带标准 `zlib1.dll`，
脚本通过 `ctypes` 自动发现；若环境只有 zlib-ng 版 stdlib，则回打包仍可**结构一致、解压数据一致**，
但压缩流字节会不同（默认复用 `streams/NN.z` 仍可逐字节一致）。

---

## 13. 原程序项目文件（供重构参考，非 logo.bin 格式的一部分）
## 13. LogoBuilder project mapping (tool behaviour, not part of the on-disk logo format) [Tool-verified]

`CreateProject` 会建立项目目录，其中：

| 文件 | 内容 |
|---|---|
| `header` | 512 字节原始头（打包时原样写回，仅更新 size@4） |
| `img1.png … img51.png` | 每块的 PNG 重编码（尺寸/颜色由 sizes 表 + 解码器决定；不携带 bpp） |
| `block1.dat …` | 仅当某块解压长度不在 sizes 表时出现（不透明二进制块，存解压后字节） |
| `.project` | 纯文本（CRLF），每行：`image:<file> <W> <H> <BPP>` 或 `binary:<file>` |

打包（`PackProject`）按 `.project` 行序，把 image 行重新编码+压缩、binary 行压缩，
然后写 header(仅 size@4 更新) + TOC(count,total,offsets) + 连续 zlib 流。
这证明**设备端只依赖尺寸表 + 块数据**，文件名/顺序由项目侧维护。

---

## 14. 验证状态与未决问题 (verified vs hypothesis, open questions)

**已验证 [Verified]/[Tool-verified]**：§1–§13 中除下列条目外的全部内容（含字节级回打包一致）。

**[Hypothesis] 未验证 / 待确认**：
1. 头部 0x10–0x27 的 24 字节保留区是否有历史用途（样片全 0，原程序不读；改字节无影响）。
2. `count = 0` 的空容器是否合法（原程序未测试；按公式应得 512+8 字节文件）。
3. 真机 LK/内核是否校验 magic / tag（原程序不校验；MTK LK 源码通常会校验 magic，未在本仓库验证）。
4. 各块 index → 显示阶段的语义顺序（开机第几屏/充电动画帧序）是平台相关约定，文件里没有。
5. 样片 5 个大块的原始压缩器精确 zlib 版本（标准 zlib 1.3.1/1.3.2 可复现全部 51 流；未穷举 1.2.x 构建）。
6. `sizes.txt` 重复 key 的覆盖顺序按 IL 推断为“后者覆盖”，未构造实测。
7. bpp=2 的真机样片未获得；§5.2 的编解码由原程序**合成数据**验证（2bpp 合成容器经原程序
   `CreateProject`→`PackProject` 往返逐字节一致，验证了整条 2bpp 路径）。

**补充样片事实（内容层面）**：多个整屏 logo 块的内容以 180° 旋转存放（0, 41–44, 45–48 等），
而充电动画帧（4–35）是正常朝向；原程序提取结果与本节解码完全一致（不做翻转），
因此属于素材内容差异而不是格式 flip —— 该 logo.bin 很可能是由多个机型/多块面板的素材拼成的
测试集合（同分辨率下出现 alcatel / Samsung / MediaTek 等不同品牌画面），而非单一机型实机 dump。

---

## 15. 复现步骤 (reproduction)

```bash
cd <repo root>
# 1) 提取 + 逐块清单
python analysis/format/parse_logo.py \
    "LogoBuilder_jb51/LogoBuilder_v1.6中文版/logo.bin" analysis/format/extracted --table
# 2) 逐字节回打包（默认复用原始流）
python analysis/format/parse_logo.py \
    "LogoBuilder_jb51/LogoBuilder_v1.6中文版/logo.bin" analysis/format/extracted \
    --repack analysis/format/repacked_logo.bin
# 3) 全量重压缩回打包（需标准 C zlib；脚本自动 ctypes 发现，否则 stdlib）
python analysis/format/parse_logo.py \
    "LogoBuilder_jb51/LogoBuilder_v1.6中文版/logo.bin" analysis/format/extracted \
    --repack analysis/format/repacked_logo_recompress.bin --recompress
# 4) lk.bin（raw 容器）
python analysis/format/parse_logo.py \
    "LogoBuilder_jb51/LogoBuilder_v1.6中文版/lk.bin" analysis/format/extracted_lk \
    --repack analysis/format/repacked_lk.bin
```

`parse_logo.py` 仅依赖 Python 3.8+ 标准库与 Pillow；不依赖本仓库其他代码。
