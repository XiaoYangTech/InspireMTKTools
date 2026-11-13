# 核心库 API 契约（core-dev 实现 / gui-dev 调用）

> 本文件是 GUI 与核心库之间的唯一契约。core-dev 必须严格按此签名实现；gui-dev 只按此调用。
> 若实现中发现必须调整签名，先在此文件更新（Lead 审批），再同步代码。

## 包结构

```
app.py                          # GUI 入口：python app.py
src/logobuilder/
  __init__.py                   # __version__
  __main__.py                   # python -m logobuilder -> GUI
  core/
    __init__.py                 # 以本文件签名 re-export 全部公共 API
    errors.py
    sizes.py
    pixel.py
    container.py
    project.py
    zipflash.py
    bootlogo.py
    cli.py                      # python -m logobuilder.core.cli ...
  assets/
    sizes.txt                   # 内置分辨率表（来自原版 sizes.txt，含 WxHxD）
    update_template.zip         # 原版 update.zip 模板（CWM）
  ui/                           # gui-dev
tests/
  test_core/
  test_ui/
```

## errors.py

```python
class LogoBuilderError(Exception):
    """所有核心异常的基类。message 为中文可读信息。"""
    def __init__(self, message: str, *, hint: str | None = None, context: dict | None = None): ...

class NotLogoFile(LogoBuilderError): ...      # 不是 MTK LOGO 镜像（magic 不符）
class CorruptContainer(LogoBuilderError): ...  # 头/偏移/长度自洽性校验失败
class UnknownImageSize(LogoBuilderError): ...  # 无法推断块尺寸
class ProjectError(LogoBuilderError): ...      # 项目目录/文件缺失或损坏
class ExportError(LogoBuilderError): ...       # 写盘/打包失败
```

## sizes.py

```python
@dataclass(frozen=True)
class LogoSize:
    width: int
    height: int
    color_depth: int = 2          # 2 = RGB565; 4 = BGRA8888
    def __post_init__(self): ...  # 校验 width/height > 0, color_depth in (2,4)，非法抛 ValueError
    @property
    def key(self) -> int: ...     # width*height*color_depth（= 解压后块字节数）
    @property
    def bpp(self) -> int: ...     # = color_depth（别名，GUI 用）
    def __str__(self) -> str: ... # "540x960x4"
    @staticmethod
    def parse(text: str) -> "LogoSize": ...   # 接受 "540x960x4" / "540x960"(默认2)
    @staticmethod
    def from_key(key: int) -> list["LogoSize"]: ...  # 反解所有候选（不保证唯一）

@dataclass(frozen=True)
class SizeCandidate:
    size: LogoSize
    score: float                  # 0..1，越大越可信
    reason: str                   # 中文原因，如 "内置尺寸表精确匹配"

class SizeDB:
    def __init__(self, sizes: Iterable[LogoSize] = ()): ...
    @classmethod
    def default(cls) -> "SizeDB": ...         # 载入 assets/sizes.txt + 用户配置
    @classmethod
    def load_user(cls, path: Path | None = None) -> "SizeDB": ...  # 用户增量
    def add(self, size: LogoSize) -> None: ...
    def save_user(self, path: Path | None = None) -> Path: ...
    def exact(self, decompressed_len: int) -> list[LogoSize]: ...
    def candidates(self, decompressed_len: int, *,
                   aspect_hint: float | None = None,
                   neighbors: Sequence[LogoSize] = ()) -> list[SizeCandidate]: ...
        # 排序：内置/用户精确 key 匹配(1.0) > 邻近块一致性(0.9) > 宽高比匹配(0.6-0.85) > 其它同 key 候选(0.4)
        # 无候选返回 []，由 UI 让用户自定义
    def __contains__(self, size: LogoSize) -> bool: ...

def guess_sizes_for_blocks(blocks_len: Sequence[int],
                           db: SizeDB | None = None) -> list[list[SizeCandidate]]: ...
    # 批量推断：利用相邻块（同一机型常见同分辨率）交叉投票
```

## pixel.py

```python
def decode_pixels(pixels: bytes, size: LogoSize) -> bytes:
    """块像素数据 -> 紧凑 BGRA8888（len = W*H*4，行=W*4，无 padding）。"""
def pixels_to_image(pixels: bytes, size: LogoSize) -> "PIL.Image.Image":
    """块像素 -> Pillow RGBA 图。size.key 必须等于 len(pixels)。"""
def image_to_pixels(img: "PIL.Image.Image", size: LogoSize,
                    fit: str = "stretch") -> bytes:
    """Pillow 图 -> 块像素（W*H*bpp）。
    fit: "stretch"(拉伸) | "contain"(等比留白，背景透明/黑) | "cover"(等比裁剪)"""
def encode_pixels(img, size, fit="stretch") -> bytes: ...  # image_to_pixels 别名
def resize_image(img, size: LogoSize, fit: str = "stretch") -> "PIL.Image.Image": ...
```

- RGB565 转换必须用位运算直式，float 公式仅作注释；bpp4 小端 BGRA。
- Pillow `Image.Resampling`（3.9+）优先，兼容旧版 fallback。

## container.py

```python
HEADER_SIZE = 512
MAGIC = 0x58881688

@dataclass
class ImageBlock:
    index: int
    size: LogoSize
    pixels: bytes                 # 解压后的像素（W*H*bpp）
    raw_compressed: bytes | None = None   # 原文件中的 zlib 字节（存在则可无损回包）
    def to_image(self) -> "PIL.Image.Image": ...

@dataclass
class BinaryBlock:
    index: int
    data: bytes                   # 解压后数据（可能不可解压，见 raw_compressed）
    raw_compressed: bytes | None = None   # 原 zlib 字节，优先用于回包（无损）
    decompressed_ok: bool = True

Block = ImageBlock | BinaryBlock

@dataclass
class LogoContainer:
    header: bytes                 # 恰好 512 字节
    tag: str                      # "LOGO" / "LK" / 其它 8 字节 tag 去 NUL
    blocks: list[Block]
    source_path: Path | None = None
    @property
    def count(self) -> int: ...
    @property
    def data_len(self) -> int: ...        # 8 + 4*count + sum(compressed_len)
    def to_image_map(self) -> list[tuple[int, "PIL.Image.Image"]]: ...
    def to_bytes(self, *, preserve_binary: bool = True) -> bytes: ...
    def save(self, path: str | Path, **kw) -> Path: ...
    @classmethod
    def parse(cls, data: bytes, *, path: Path | None = None,
              db: SizeDB | None = None,
              block_sizes: Sequence[LogoSize | None] | None = None) -> "LogoContainer": ...
    @classmethod
    def load(cls, path: str | Path, *, db: SizeDB | None = None) -> "LogoContainer": ...

def sniff_format(data: bytes) -> str: ...
    # "mtk-logo-block"(magic 0x58881688 且块表自洽) | "mtk-logo-raw"(magic 有效但 payload 非块表，如 LK)
    # | "unknown"(无 magic / 太短)
def is_mtk_logo(data: bytes) -> bool: ...
```

解析规则（必须实现）：
- `magic == 0x58881688`，`len>=512`，`dataLen = u32@4`，`dataLen <= len-512`（否则 `CorruptContainer`）。
- payload 从 512 开始：`count=u32`, `dataLen2=u32`（作为 TOC 边界与完整性校验；与 header@4 不一致时 **记录 warning 并尽量恢复**，不直接报错——修复版要求对真实损坏样本宽容且可诊断；`offsets` 非单调/越界/表超长才抛 `CorruptContainer`）；`offsets=count*u32`；
- `LogoContainer.warnings: list[str]` 暴露解析期可恢复问题（GUI 显示在日志面板，CLI 打到 stderr）；`to_bytes()` 始终写出自洽的 header@4 / TOC total / offsets。
  `count==0` 允许（空容器）但有上限保护（如 count > (dataLen-8)//4 报错防 OOM）。
- 校验 `offsets[0] == 8+4*count`、单调递增、`offsets[i] < dataLen`，否则 `CorruptContainer`。
- 每块：`zlib.decompress`；失败则视为 BinaryBlock（`decompressed_ok=False`，只保留 raw）。
- 尺寸推断：`db.candidates(len(decompressed))`；恰好 1 个高置信（score≥0.9）→ ImageBlock；
  否则若 `block_sizes` 提供则用；否则 ImageBlock.size=None 由 UI 决定 → 实现上用 `BinaryBlock` 暂存但
  提供 `pending_sizes` 补充接口？→ **约定**：`parse(..., db, block_sizes=None)` 时未知尺寸块返回
  `BinaryBlock`，并额外暴露 `container.pending_image_blocks: list[int]`（解压成功且看起来像图像但尺寸未知的块索引）。
  提供 `container.assign_size(block_index, size: LogoSize) -> ImageBlock` 把 BinaryBlock 转为 ImageBlock。
  `to_bytes()` 对仍为 BinaryBlock 的块使用 raw_compressed 无损回包（若 raw 缺失则用 `data` 原样）。
- 打包：header 前 4 字节写 MAGIC，u32@4 写 dataLen，`header[8:512]` 原样；payload 结构严格按 spec。
  图像块：`zlib.compress(pixels, 9)`（与 Ionic.Zlib BestCompression 对齐；Python zlib 与 Ionic zlib 输出不保证逐字节一致，
  但**针对未修改块必须优先复用 raw_compressed** 以保证无损；仅修改过的块才重新压缩，测试允许此时不 byte-identical）。
- `to_bytes()` 返回完整文件字节；`save()` 原子写（临时文件 + replace）。

### 容器模式（raw / block-table）

真实样本 `lk.bin` 证明：magic=0x58881688 + 512B 头是 MTK 多个分区共用的**通用头**，并不保证 payload 是 LOGO 块表。
- `container.mode: str` = `"block-table"`（tag 通常为 `LOGO`，payload 通过块表校验）或 `"raw"`（payload 无块表，如 `LK` bootloader 原始二进制）。
- raw 模式：`blocks = [BinaryBlock(data=payload, raw_compressed=None, decompressed_ok=False)]`，另有 `container.raw_payload: bytes`；`to_bytes()` 必须逐字节无损还原原文件。
- tag == "LOGO" 但块表校验失败 → 抛 `CorruptContainer`（这是真损坏）；tag != "LOGO" 且块表校验失败 → raw 模式（合法的不透明容器）。
- `sniff_format(data)` 返回 `"mtk-logo-block"` / `"mtk-logo-raw"` / `"unknown"`。
- raw 模式也允许修改 header/tag 后重新导出（`to_bytes()` = header + payload 原样）。

## project.py

```python
PROJECT_FILE = "project.json"
HEADER_FILE = "header.bin"

@dataclass
class ProjectBlock:
    index: int
    kind: str                     # "image" | "binary"
    size: LogoSize | None = None
    image_path: Path | None = None      # 相对项目目录，如 "img1.png"
    binary_path: Path | None = None     # 如 "binary/5.dat"
    raw_compressed_path: Path | None = None  # 无损回包用原始压缩字节（可选）
    modified: bool = False

class LogoProject:
    def __init__(self, path: Path, header: bytes, blocks: list[ProjectBlock]): ...
    @classmethod
    def new(cls, project_dir: str | Path, source: LogoContainer) -> "LogoProject": ...
    @classmethod
    def open(cls, project_dir: str | Path) -> "LogoProject": ...
    @classmethod
    def import_legacy(cls, project_dir: str | Path, *, source_container: LogoContainer | None = None) -> "LogoProject": ...
        # 兼容原版 header.bin + .project + imgN.png/blockN.dat
    @property
    def project_dir(self) -> Path: ...
    def save(self) -> None: ...                       # 写 header.bin/project.json/图片/二进制块
    def image_file(self, index: int) -> Path: ...
    def replace_image(self, index: int, image: "PIL.Image.Image", *, fit: str = "stretch") -> None: ...
    def set_block_size(self, index: int, size: LogoSize) -> None: ...
    def to_container(self) -> LogoContainer: ...      # 按 project.json 顺序组包
    def validate(self) -> list["ProjectIssue"]: ...
    def repair(self, *, repair: bool = False) -> list[str]: ...  # 返回问题列表；repair=True 自动修复
    @property
    def dirty(self) -> bool: ...

@dataclass
class ProjectIssue:
    level: str                    # "error" | "warning"
    block_index: int | None
    message: str
```

## zipflash.py

```python
DEFAULT_UPDATER_SCRIPT = """ui_print("Flashing logo...");
show_progress(0.200000, 0);
package_extract_file("logo.bin", "/dev/logo");
ui_print("Patched!");
ui_print("");
ui_print("Now you can reboot your phone");
"""

def make_update_zip(logo_bytes: bytes, out_zip: str | Path, *,
                    template: str | Path | None = None,
                    logo_filename: str = "logo.bin",
                    updater_script: str | None = None,
                    update_binary: str | Path | None = None,
                    sign_stub: bool = False) -> Path: ...
    # template 默认 assets/update_template.zip；把 logo_bytes 以 logo_filename 加到根；
    # updater_script/update_binary 覆盖 META-INF/com/google/android/ 下同名文件；
    # 写失败抛 ExportError；zip 内条目顺序确定（可复现）。返回 out_zip。
def inspect_update_zip(path) -> dict: ...   # {files:[...], script:str, binary_sha256:str}
```

## bootlogo.py

```python
def read_boot_logo(path: str | Path, size: LogoSize | None = None) -> tuple["PIL.Image.Image", LogoSize]: ...
def write_boot_logo(image, path: str | Path, size: LogoSize | None = None) -> LogoSize: ...
    # 裸 RGB565（bpp=2）像素，无任何头；size 缺省由图像尺寸/长度推断
```

## cli.py

```python
def main(argv: list[str] | None = None) -> int
# 子命令：
#   info <img>                       打印 magic/tag/count/dataLen/逐块尺寸与 bpp
#   extract <img> <dir>              解包为项目目录（含 project.json）
#   pack <project_dir> <out.bin>     从项目目录打包
#   zip <bin_or_project> <out.zip>   生成卡刷包
#   bootlogo <img> <out_boot_logo>   生成裸第二屏
#   sizes                            列出内置尺寸
# 退出码 0 成功 / 2 参数错误 / 1 业务错误；错误输出中文提示到 stderr。
```

## 版本与依赖

```python
# src/logobuilder/__init__.py
__version__ = "2.0.0"
```

- 核心库依赖：**Pillow（>=10）+ capstone（>=5，仅 LK/AArch64 反汇编需要；缺失时 LK 核心模块须优雅降级并给出明确提示）** + 标准库；不得 import ttkbootstrap/tkinter。
- 说明：v3.0.0 新增 `lkpatch.py` 后 capstone 成为核心库正式依赖（见 `requirements.txt` / `pyproject.toml`）；`tests/test_core/test_api_contract.py` 的依赖 allowlist 已同步批准 capstone。
- Python >= 3.10。
