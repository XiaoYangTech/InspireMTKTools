"""命令行工具（可脚本化，GUI 之外的高级用法）。

用法::

    python -m logobuilder.core.cli info <logo.bin> [--json]
    python -m logobuilder.core.cli extract <logo.bin> <项目目录>
    python -m logobuilder.core.cli pack <项目目录> <out.bin> [--pad-to N]
    python -m logobuilder.core.cli zip <logo.bin|项目目录> <out.zip>
    python -m logobuilder.core.cli bootlogo <图片> <out_boot_logo> [--size 540x960]
    python -m logobuilder.core.cli bl2img <boot_logo> <out.png>
    python -m logobuilder.core.cli sizes [--json]

LK 引导程序修补（去 Orange/Red 警告 + 5 秒等待 + dm-verity 警告）::

    python -m logobuilder.core.cli lk-info   <lk.img> [--json]
    python -m logobuilder.core.cli lk-patch  <lk.img> [-o out] [--in-place] [--dry-run]
                                            [--categories orange_state,dm_verity] [--clear-text]
                                            [--backup-dir DIR] [--rules custom.json]
    python -m logobuilder.core.cli lk-verify <orig.img> <patched.img>
    python -m logobuilder.core.cli lk-restore <backup> [-o target]

退出码：0 成功；1 业务错误（文件损坏/IO 失败等）；2 参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Sequence

from . import bootlogo, lkpatch, pixel, sizes as sizes_module, zipflash
from .container import LogoContainer
from .errors import LogoBuilderError
from .project import LogoProject
from .sizes import LogoSize, SizeDB

__all__ = ["main", "build_parser"]

PROG = "python -m logobuilder.core.cli"


def _categories_arg(value: str) -> list[str]:
    """argparse 类型：逗号分隔的补丁类别列表。"""
    items = [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("补丁类别不能为空（如 orange_state,dm_verity）")
    valid = set(lkpatch.CATEGORIES)
    unknown = [item for item in items if item not in valid]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"未知补丁类别：{', '.join(unknown)}（可选 {', '.join(lkpatch.CATEGORIES)}）"
        )
    return items


def build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器（测试可直接复用）。"""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="LogoBuilder 核心命令行：解析/解包/打包 MTK logo.bin、生成卡刷包与第二屏。",
    )
    parser.add_argument("--debug", action="store_true", help="出错时打印完整 traceback（排障用）")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<命令>")

    # info -------------------------------------------------------------
    info = subparsers.add_parser("info", help="显示镜像信息与逐块尺寸")
    info.add_argument("image", help="logo.bin / logo.img / lk.bin 路径")
    info.add_argument("--json", action="store_true", help="以 JSON 输出")

    # extract ----------------------------------------------------------
    extract = subparsers.add_parser(
        "extract",
        aliases=["unpack"],
        help="解包为工程目录（含 project.json；unpack 为原版命令名）",
    )
    extract.add_argument("image", help="logo.bin 路径")
    extract.add_argument("directory", help="输出工程目录（必须为空/不存在）")
    extract.add_argument("--overwrite", action="store_true", help="允许覆盖非空目录")

    # pack -------------------------------------------------------------
    pack = subparsers.add_parser("pack", help="从工程目录打包 logo.bin")
    pack.add_argument("project", help="工程目录")
    pack.add_argument("output", help="输出 logo.bin 路径")
    pack.add_argument("--pad-to", type=int, default=None, metavar="N", help="用 0x00 填充到 N 字节（导出分区镜像）")

    # zip --------------------------------------------------------------
    flash = subparsers.add_parser("zip", help="生成卡刷包 update.zip")
    flash.add_argument("source", help="logo.bin 文件或工程目录")
    flash.add_argument("output", help="输出 update.zip 路径")
    flash.add_argument("--name", default="logo.bin", help="zip 内 logo 文件名（默认 logo.bin）")
    flash.add_argument("--template", default=None, help="自定义模板 zip")
    flash.add_argument("--script", default=None, help="自定义 updater-script 文件")
    flash.add_argument("--binary", default=None, help="自定义 update-binary 文件")
    flash.add_argument("--sign-stub", action="store_true", help="模板缺少 MANIFEST 时补充占位清单")

    # bootlogo ---------------------------------------------------------
    boot = subparsers.add_parser(
        "bootlogo",
        aliases=["img2bl"],
        help="由图片生成裸第二屏 boot_logo（img2bl 为原版命令名）",
    )
    boot.add_argument("image", help="输入图片（png/jpg/bmp）")
    boot.add_argument("output", help="输出 boot_logo 文件")
    boot.add_argument("--size", default=None, help="输出分辨率，如 540x960（缺省用图片尺寸）")

    # bl2img -----------------------------------------------------------
    restore = subparsers.add_parser(
        "bl2img",
        help="把裸第二屏 boot_logo 转回 PNG（原版命令名）",
    )
    restore.add_argument("input", help="boot_logo 文件")
    restore.add_argument("output", help="输出 PNG 路径")
    restore.add_argument("--size", default=None, help="按指定分辨率解析，如 540x960（缺省自动推断）")

    # sizes ------------------------------------------------------------
    sizes_cmd = subparsers.add_parser("sizes", help="列出内置/用户分辨率表")
    sizes_cmd.add_argument("--json", action="store_true", help="以 JSON 输出")

    # LK 修补 ----------------------------------------------------------
    lk_info = subparsers.add_parser("lk-info", help="分析 LK 镜像：架构/警告文本/可补丁候选")
    lk_info.add_argument("image", help="lk.img / lk.bin（或裸 LK payload）")
    lk_info.add_argument("--json", action="store_true", help="以 JSON 输出")
    lk_info.add_argument("--rules", default=None, help="自定义 ARM32 规则 JSON")

    lk_patch = subparsers.add_parser(
        "lk-patch", help="给 LK 打警告补丁（默认生成 <name>_patched.img，写前自动备份）"
    )
    lk_patch.add_argument("image", help="lk.img / lk.bin（或裸 LK payload）")
    output_group = lk_patch.add_mutually_exclusive_group()
    output_group.add_argument("-o", "--output", default=None, help="输出文件（默认 <name>_patched.img）")
    output_group.add_argument("--in-place", action="store_true", help="原地修改（必须先成功备份）")
    lk_patch.add_argument("--dry-run", action="store_true", help="只预览补丁与校验，不写任何文件")
    lk_patch.add_argument(
        "--categories",
        type=_categories_arg,
        default=None,
        metavar="LIST",
        help="只应用指定类别，逗号分隔：orange_state,red_state,dm_verity",
    )
    lk_patch.add_argument("--clear-text", action="store_true", help="同时清空警告文本（不影响内核参数）")
    lk_patch.add_argument("--backup-dir", default=None, help="备份目录（默认与原文件同目录）")
    lk_patch.add_argument("--rules", default=None, help="自定义 ARM32 规则 JSON")

    lk_verify = subparsers.add_parser("lk-verify", help="校验补丁结果（头/内核参数/差异区间/反汇编）")
    lk_verify.add_argument("original", help="原始 LK 镜像")
    lk_verify.add_argument("patched", help="已打补丁的 LK 镜像")
    lk_verify.add_argument("--json", action="store_true", help="以 JSON 输出")

    lk_restore = subparsers.add_parser("lk-restore", help="从备份还原 LK 镜像")
    lk_restore.add_argument("backup", help="备份文件（<name>_original_backup.img）")
    lk_restore.add_argument("-o", "--output", default=None, help="还原目标（默认去掉 _original_backup 后缀）")
    return parser


# ----------------------------------------------------------------------
# 子命令实现
# ----------------------------------------------------------------------
def _emit_warnings(warnings: Sequence[str]) -> None:
    """把解析/工程警告打到 stderr（退出码不受影响；重复内容自动去重）。"""
    for warning in dict.fromkeys(str(item) for item in warnings if item):
        print(f"警告: {warning}", file=sys.stderr)


def _cmd_info(args: argparse.Namespace) -> int:
    container = LogoContainer.load(args.image)
    summary = container.summary()
    _emit_warnings(container.warnings)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print(f"文件: {summary['path']}")
    print(f"模式: {summary['mode']}")
    print(f"magic: {summary['magic']}    tag: {summary['tag']!r}")
    print(f"块数量: {summary['count']}    dataLen: {summary['data_len']}")
    print(f"头部 dataLen: {summary['header_data_len']}    尾部多余字节: {summary['trailing_len']}")
    if summary["pending_image_blocks"]:
        print(f"待定尺寸块: {summary['pending_image_blocks']}")
    print("块列表:")
    for block in summary["blocks"]:
        if block["kind"] == "image":
            raw_len = block["compressed_len"]
            print(
                f"  [{block['index']:>3}] 图像 {block['size']:<12} "
                f"压缩 {raw_len if raw_len is not None else '?':>8} B -> {block['pixels_len']:>9} B"
                + ("  (已修改)" if block["modified"] else "")
            )
        else:
            note = "解压失败/不透明" if not block["decompressed_ok"] else "解压成功但尺寸未知"
            if block["pending"]:
                note = "尺寸未知/疑似现代变体，仅原样保留"
            print(
                f"  [{block['index']:>3}] 二进制 {note:<18} "
                f"数据 {block['data_len']:>9} B 压缩 {block['compressed_len'] if block['compressed_len'] is not None else '?':>8} B"
            )
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    container = LogoContainer.load(args.image)
    _emit_warnings(container.warnings)
    project = LogoProject.new(args.directory, container, overwrite=args.overwrite)
    _emit_warnings(project.warnings)
    image_blocks = sum(1 for block in project.blocks if block.kind == "image")
    binary_blocks = project.count - image_blocks
    print(f"已解包到: {project.project_dir}")
    print(f"块数量: {project.count}（图像 {image_blocks}，二进制 {binary_blocks}）")
    if project.pending:
        print(f"待定尺寸块: {project.pending}")
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    project = LogoProject.open(args.project)
    _emit_warnings(project.warnings)
    container = project.to_container()
    _emit_warnings(container.warnings)
    data = container.to_bytes(pad_to_size=args.pad_to)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(f"已打包: {output}")
    print(f"大小: {len(data)} 字节，块数量: {container.count}，dataLen: {container.data_len}")
    print(f"SHA-256: {container.sha256(pad_to_size=args.pad_to)}")
    return 0


def _cmd_zip(args: argparse.Namespace) -> int:
    source = Path(args.source)
    if source.is_dir():
        container = LogoProject.open(source).to_container()
    else:
        container = LogoContainer.load(source)
    _emit_warnings(container.warnings)
    logo_bytes = container.to_bytes()
    script_text = None
    if args.script:
        script_text = Path(args.script).read_text(encoding="utf-8")
    zip_path = zipflash.make_update_zip(
        logo_bytes,
        args.output,
        template=args.template,
        logo_filename=args.name,
        updater_script=script_text,
        update_binary=args.binary,
        sign_stub=args.sign_stub,
    )
    info = zipflash.inspect_update_zip(zip_path)
    print(f"已生成卡刷包: {zip_path}")
    print(f"文件数: {len(info['files'])}，包含: {', '.join(info['files'])}")
    return 0


def _cmd_bootlogo(args: argparse.Namespace) -> int:
    from PIL import Image as _Image

    try:
        with _Image.open(args.image) as handle:
            handle.load()
            image = handle.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise LogoBuilderError(
            f"无法读取图片：{args.image}（{exc}）",
            hint="请确认输入是 png/jpg/bmp 图片。",
        ) from exc
    size = LogoSize.parse(args.size) if args.size else None
    used = bootlogo.write_boot_logo(image, args.output, size)
    print(f"已生成第二屏: {args.output}")
    print(f"分辨率: {used}，文件大小: {used.key} 字节")
    return 0


def _cmd_bl2img(args: argparse.Namespace) -> int:
    """bl2img：裸 boot_logo -> PNG（原版命令名）。"""
    size = LogoSize.parse(args.size) if args.size else None
    image, used = bootlogo.read_boot_logo(args.input, size)
    output = Path(args.output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG")
    except (OSError, ValueError) as exc:
        raise LogoBuilderError(
            f"无法写入 PNG：{output}（{exc}）",
            hint="请检查输出路径是否存在、是否有写权限。",
        ) from exc
    print(f"已转换第二屏: {args.input} -> {output}")
    print(f"分辨率: {used}（{used.width}x{used.height}，RGB565 解码）")
    return 0


def _cmd_sizes(args: argparse.Namespace) -> int:
    database = SizeDB.default()
    if args.json:
        print(json.dumps([str(size) for size in database], ensure_ascii=False, indent=2))
        return 0
    for size in database:
        print(size)
    print(f"共 {len(database)} 项（内置 {len(sizes_module.builtin_sizes())} 项）")
    return 0



# ----------------------------------------------------------------------
# LK 修补子命令
# ----------------------------------------------------------------------
def _write_atomic(path: Path, data: bytes) -> Path:
    """原子写文件（同目录临时文件 + os.replace）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:  # pragma: no cover
                pass
            raise
    except OSError as exc:
        from .errors import ExportError

        raise ExportError(
            f"无法写入文件：{path}（{exc}）",
            hint="请检查目标目录是否存在、是否有写权限，以及文件是否被占用。",
            context={"path": str(path)},
        ) from exc
    return path


def _load_lk(args: argparse.Namespace, attribute: str = "image") -> "lkpatch.LkImage":
    from .errors import ExportError

    path = Path(getattr(args, attribute))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExportError(
            f"无法读取 LK 镜像：{path}（{exc}）",
            hint="请检查文件是否存在、路径是否正确、是否有读取权限。",
            context={"path": str(path)},
        ) from exc
    return lkpatch.parse_lk_image(data, path)


def _lk_report_to_dict(report: "lkpatch.LkScanReport") -> dict[str, Any]:
    image = report.image
    header_info: dict[str, Any] | None = None
    if image.is_container and image.header is not None:
        header_info = {
            "magic": f"0x{int.from_bytes(image.header[0:4], 'little'):08X}",
            "size": image.size_field,
            "name": image.name,
            "ext_magic": (f"0x{image.ext_magic:08X}" if image.ext_magic is not None else None),
        }
    return {
        "path": str(image.path) if image.path is not None else None,
        "file_size": image.file_size,
        "sha256": image.sha256,
        "is_container": image.is_container,
        "payload_offset": image.payload_offset,
        "name": image.name,
        "header": header_info,
        "arch": report.arch,
        "verdict": report.verdict,
        "already_patched": report.already_patched,
        "findings": [
            {
                "category": finding.category,
                "arch": finding.arch,
                "kind": finding.kind,
                "offset": finding.offset,
                "offset_hex": f"0x{finding.offset:x}",
                "function_start": finding.function_start,
                "confidence": finding.confidence,
                "selected": finding.selected,
                "before": finding.before.hex(),
                "after": finding.after.hex(),
                "evidence": list(finding.evidence),
            }
            for finding in report.findings
        ],
        "warnings": [
            {"offset": warning.offset, "offset_hex": f"0x{warning.offset:x}", "text": warning.text}
            for warning in report.warnings
        ],
        "guard_params": report.guard_params,
        "notes": list(report.notes),
    }


def _print_lk_report(report: "lkpatch.LkScanReport") -> None:
    image = report.image
    print(f"文件: {image.path if image.path is not None else '<内存>'}")
    print(f"大小: {image.file_size} 字节")
    print(f"SHA-256: {image.sha256}")
    if image.is_container and image.header is not None:
        ext = f"0x{image.ext_magic:08X}" if image.ext_magic is not None else "无"
        print(
            f"类型: MTK LK 分区镜像（512 字节头）；"
            f"magic=0x{int.from_bytes(image.header[0:4], 'little'):08X}"
            f" size={image.size_field} name={image.name!r} ext_magic={ext}"
        )
    else:
        print("类型: 裸 LK payload（无 512 字节头）")
    print(f"架构: {report.arch}    结论: {report.verdict}")
    if report.already_patched:
        print("状态: 镜像似乎已经打过 LK 警告补丁（无需重复处理）")
    print(f"警告文本: {len(report.warnings)} 处")
    for warning in report.warnings[:12]:
        print(f"  @0x{warning.offset:x}  {warning.text}")
    if len(report.warnings) > 12:
        print(f"  ……共 {len(report.warnings)} 处")
    guard_count = sum(len(items) for items in report.guard_params.values())
    print(f"内核启动参数保护: {len(report.guard_params)} 个参数 / {guard_count} 处出现（补丁前后必须一致）")
    if report.findings:
        print(f"候选补丁（{len(report.findings)} 个）:")
        for index, finding in enumerate(report.findings, start=1):
            flag = "OK" if finding.selected else "?"
            print(
                f"  {index}. [{flag}] {finding.category} @ 文件 0x{finding.offset:x}"
                f"（payload +0x{finding.offset - image.payload_offset:x}）"
                f"  置信度 {finding.confidence:.2f}  {finding.kind}"
            )
            for line in finding.evidence[:6]:
                print(f"       - {line}")
    else:
        print("候选补丁: 无（fail-safe：本工具不会修改该镜像）")
    for note in report.notes:
        print(f"提示: {note}")


def _cmd_lk_info(args: argparse.Namespace) -> int:
    image = _load_lk(args)
    rules = lkpatch.load_custom_rules(args.rules) if args.rules else None
    report = lkpatch.scan(image, rules=rules)
    if args.json:
        print(json.dumps(_lk_report_to_dict(report), ensure_ascii=False, indent=2))
    else:
        _print_lk_report(report)
    if report.verdict == "unsupported":
        print("错误：该镜像没有可用的自动补丁候选（不支持/无候选，fail-safe 不写文件）", file=sys.stderr)
        return 1
    return 0


def _cmd_lk_patch(args: argparse.Namespace) -> int:
    image = _load_lk(args)
    rules = lkpatch.load_custom_rules(args.rules) if args.rules else None
    report = lkpatch.scan(image, rules=rules)
    for note in report.notes:
        print(f"提示: {note}", file=sys.stderr)
    if report.already_patched:
        print("该镜像似乎已经打过 LK 警告补丁，无需重复处理（未写文件）")
        return 1
    if not report.findings:
        print("错误：未找到可修补的候选（fail-safe：不写文件）", file=sys.stderr)
        print("原因：", file=sys.stderr)
        for note in report.notes:
            print(f"  - {note}", file=sys.stderr)
        return 1
    categories = args.categories
    selected = [
        finding
        for finding in report.findings
        if finding.selected and (categories is None or finding.category in categories)
    ]
    if not selected and not args.clear_text:
        if args.categories:
            print("错误：所选类别没有可应用的补丁候选，已中止（fail-safe 不写文件）", file=sys.stderr)
        else:
            print(
                "错误：候选补丁置信度不足或未选中，已中止（fail-safe 不写文件）；"
                "可在报告中确认候选后使用 --categories 指定类别",
                file=sys.stderr,
            )
        return 1
    try:
        new_bytes, changes = lkpatch.apply_patches(
            image,
            report,
            categories=categories,
            clear_warning_text=args.clear_text,
        )
    except lkpatch.LkPatchError as exc:
        print(f"错误：{exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"提示：{exc.hint}", file=sys.stderr)
        return 1
    verification = lkpatch.verify_patch(image, new_bytes, report)
    if not verification["patch_ok"]:
        print("错误：补丁后校验未通过，已中止写入（fail-safe）", file=sys.stderr)
        print(
            f"  size_same={verification['size_same']} header_same={verification['header_same']} "
            f"guard_counts_same={verification['guard_counts_same']} "
            f"unexpected_changes={verification['unexpected_changes']}",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("预览（dry-run：不写任何文件）")
        print(f"文件: {image.path}")
        print("计划变更:")
        for change in changes:
            print(f"  - {change}")
        ranges = [f"0x{start:x}..0x{end:x}" for start, end in verification["changed_ranges"]]
        print(f"差异区间: {ranges}")
        before = lkpatch.disassemble_patch_points(image, report)
        if before:
            print("补丁前反汇编:")
            for line in before:
                print(f"  {line}")
        print("补丁后反汇编:")
        for line in verification["disasm_after"]:
            print(f"  {line}")
        print(
            f"校验: size_same={verification['size_same']} header_same={verification['header_same']} "
            f"guard_counts_same={verification['guard_counts_same']} "
            f"residual_warnings={verification['residual_warnings']} patch_ok={verification['patch_ok']}"
        )
        return 0

    backup_file = lkpatch.save_backup(image, args.backup_dir)
    if args.in_place:
        target = Path(args.image)
    elif args.output:
        target = Path(args.output)
    else:
        target = lkpatch.patched_path(args.image)
    _write_atomic(target, new_bytes)
    print(f"已应用补丁: {target}")
    print(f"原始备份: {backup_file}")
    print("变更:")
    for change in changes:
        print(f"  - {change}")
    print("补丁后反汇编:")
    for line in verification["disasm_after"]:
        print(f"  {line}")
    print(f"SHA-256: {verification['sha256_before']} -> {verification['sha256_after']}")
    print(
        f"校验: size_same={verification['size_same']} header_same={verification['header_same']} "
        f"guard_counts_same={verification['guard_counts_same']} "
        f"residual_warnings={verification['residual_warnings']} patch_ok={verification['patch_ok']}"
    )
    print(
        "风险提示：刷写 LK 有变砖风险，请先备份；优先使用 fastboot flash lk 刷写；"
        "A/B 设备请两个槽位都刷；部分机型需先解锁 seccfg（mtkclient）才能启动修改后的 LK。"
    )
    if args.clear_text:
        print("说明：已同时清空警告文本（androidboot.* 等内核启动参数未改动）。")
    return 0


def _cmd_lk_verify(args: argparse.Namespace) -> int:
    image = _load_lk(args, attribute="original")
    report = lkpatch.scan(image)
    patched_path_obj = Path(args.patched)
    try:
        patched_bytes = patched_path_obj.read_bytes()
    except OSError as exc:
        from .errors import ExportError

        raise ExportError(
            f"无法读取补丁文件：{patched_path_obj}（{exc}）",
            context={"path": str(patched_path_obj)},
        ) from exc
    verification = lkpatch.verify_patch(image, patched_bytes, report)
    if args.json:
        print(json.dumps(verification, ensure_ascii=False, indent=2))
    else:
        print(f"原始文件: {image.path}")
        print(f"补丁文件: {patched_path_obj}（{len(patched_bytes)} 字节）")
        print(f"SHA-256: {verification['sha256_before']} -> {verification['sha256_after']}")
        print(f"文件大小不变: {verification['size_same']}")
        print(f"0x200 头不变: {verification['header_same']}")
        print(f"内核启动参数不变: {verification['guard_counts_same']}")
        print("差异区间:")
        for start, end in verification["changed_ranges"]:
            print(f"  - 0x{start:x}..0x{end:x}")
        if verification["unexpected_changes"]:
            print("意外改动（不在候选补丁/警告文本区间内）:")
            for start, end in verification["unexpected_changes"]:
                print(f"  - 0x{start:x}..0x{end:x}")
        print(f"残余警告文本: {verification['residual_warnings']} 处")
        print("补丁后反汇编:")
        for line in verification["disasm_after"]:
            print(f"  {line}")
        conclusion = "PASS（补丁校验通过）" if verification["patch_ok"] else "FAIL（补丁校验未通过）"
        print(f"结论: {conclusion}")
    return 0 if verification["patch_ok"] else 1


def _cmd_lk_restore(args: argparse.Namespace) -> int:
    backup_file = Path(args.backup)
    if args.output:
        target = Path(args.output)
    else:
        stem = backup_file.stem
        if "_original_backup" in stem:
            stem = stem.split("_original_backup", 1)[0]
        target = backup_file.with_name(stem + (backup_file.suffix or ".img"))
    restored = lkpatch.restore_from_backup(backup_file, target)
    import hashlib

    digest = hashlib.sha256(restored.read_bytes()).hexdigest()
    print(f"已从备份还原: {backup_file} -> {restored}")
    print(f"大小: {restored.stat().st_size} 字节，SHA-256: {digest}")
    print("提示：还原后如需再次刷写，请确认设备槽位并备份当前状态。")
    return 0


_HANDLERS = {
    "info": _cmd_info,
    "extract": _cmd_extract,
    "unpack": _cmd_extract,
    "pack": _cmd_pack,
    "zip": _cmd_zip,
    "bootlogo": _cmd_bootlogo,
    "img2bl": _cmd_bootlogo,
    "bl2img": _cmd_bl2img,
    "sizes": _cmd_sizes,
    "lk-info": _cmd_lk_info,
    "lk-patch": _cmd_lk_patch,
    "lk-verify": _cmd_lk_verify,
    "lk-restore": _cmd_lk_restore,
}


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回退出码（不直接 ``sys.exit``，方便测试）。"""
    # Windows 控制台默认可能是 GBK；尽量切到 UTF-8 以正确显示中文路径/提示
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):  # pragma: no cover - 非文本流/被占用
            pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 的参数错误/--help
        code = exc.code
        return int(code) if isinstance(code, int) else 0

    handler = _HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse required 已保证
        parser.print_help()
        return 2
    debug = bool(getattr(args, "debug", False))
    try:
        return handler(args)
    except LogoBuilderError as exc:
        if debug:
            traceback.print_exc()
        print(f"错误：{exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"提示：{exc.hint}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        if debug:
            traceback.print_exc()
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - 交互中断
        print("已取消", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 兜底：给出可读信息并提示 --debug
        if debug:
            traceback.print_exc()
        print(f"错误：内部错误 {type(exc).__name__}: {exc}", file=sys.stderr)
        print("提示：加 --debug 参数可查看完整 traceback。", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - 模块执行入口
    sys.exit(main())
