#!/usr/bin/env python3
"""Independent headless GUI probe for the rebuilt LogoBuilder (QA scope).

Drives the real :class:`logobuilder.ui.app.LogoBuilderApp` in ``test_mode`` (no
blocking dialogs) through the acceptance workflow against the shipped sample:

    open real logo.bin
      -> GUI byte-identical export
      -> replace block 0 with solid red
      -> export logo.bin + logo.img (zero filled to a partition size) + update.zip
      -> new project in the GUI -> pack -> reopen project
      -> load lk.bin (raw container mode)
      -> corrupt-file error mapping (readable Chinese message via core_bridge)

The script is run under an external timeout by the QA harness::

    python analysis/qa/gui_probe.py --out-dir analysis/qa/artifacts/gui_probe

It never edits project source files; outputs are written below ``--out-dir`` and
the summary JSON is printed to stdout.  Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

REAL_LOGO = ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "logo.bin"
REAL_LK = ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "lk.bin"
TEMPLATE_ZIP = ROOT / "src" / "logobuilder" / "assets" / "update_template.zip"
ANDROID_META = "META-INF/com/google/android/"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
EXPECTED_LOGO_SHA256 = "c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c"
EXPECTED_LK_SHA256 = "5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256(Path(path).read_bytes())


class Probe:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        return bool(ok)

    @property
    def passed(self) -> bool:
        return all(check["ok"] for check in self.checks)


def template_update_binary_sha256() -> str:
    with zipfile.ZipFile(TEMPLATE_ZIP, "r") as archive:
        return sha256(archive.read(ANDROID_META + "update-binary"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logo", type=Path, default=REAL_LOGO)
    parser.add_argument("--lk", type=Path, default=REAL_LK)
    parser.add_argument("--out-dir", type=Path,
                        default=ROOT / "analysis" / "qa" / "artifacts" / "gui_probe")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = Probe()
    print("=" * 72)
    print("LogoBuilder GUI headless probe (test_mode)")
    print("=" * 72)

    from logobuilder.ui.theme import enable_high_dpi

    dpi_mode = enable_high_dpi()
    probe.check("dpi-awareness", dpi_mode != "unaware", dpi_mode)

    import ttkbootstrap as tb
    from logobuilder.ui.app import LogoBuilderApp
    from logobuilder.ui.screenshot import wait_ui
    from logobuilder.ui.settings import UiSettings

    root = None
    app = None
    config_dir = Path(tempfile.mkdtemp(prefix="logobuilder-qa-cfg-"))
    try:
        root = tb.Window(themename="cosmo")
        app = LogoBuilderApp(
            root,
            settings=UiSettings(),
            config_path=config_dir / "ui_settings.json",
            test_mode=True,
            repo_root=ROOT,
        )
        wait_ui(root, 250)
        probe.check("gui-window-created", True, f"geometry={root.winfo_geometry()}")

        from logobuilder import core

        logo_bytes = Path(args.logo).read_bytes()
        lk_bytes = Path(args.lk).read_bytes()

        # ---- 1. open the shipped sample through the GUI session ----------
        session = app.load_container_sync(args.logo)
        probe.check(
            "gui-open-real-logo",
            session.count == 51 and session.tag == "LOGO",
            {"count": session.count, "tag": session.tag, "mode": session.raw_mode},
        )

        # ---- 2. unmodified GUI export must be byte-identical --------------
        unmodified = out_dir / "logo_unmodified.bin"
        app.export_bytes_sync(unmodified)
        probe.check(
            "gui-export-byte-identical",
            sha256_file(unmodified) == EXPECTED_LOGO_SHA256,
            {"sha256": sha256_file(unmodified)},
        )

        # ---- 3. replace one image with solid red --------------------------
        from PIL import Image

        red = Image.new("RGBA", (540, 960), (255, 0, 0, 255))
        replaced = app.replace_image_direct(0, red, fit="stretch")
        view = app.session.block(0) if app.session is not None else None
        probe.check(
            "gui-replace-image",
            bool(replaced) and view is not None and bool(view.modified),
            {"accepted": replaced, "modified": None if view is None else view.modified},
        )

        # ---- 4. export modified bin --------------------------------------
        modified_bin = out_dir / "logo_replaced.bin"
        app.export_bytes_sync(modified_bin)
        rebuilt = core.LogoContainer.load(modified_bin)
        original = core.LogoContainer.load(args.logo)
        red_pixels = core.image_to_pixels(red, core.LogoSize(540, 960, 4))
        others_unchanged = all(
            rebuilt.blocks[i].raw_compressed == original.blocks[i].raw_compressed
            for i in range(1, rebuilt.count)
        )
        probe.check(
            "gui-export-replaced-bin",
            rebuilt.count == 51
            and isinstance(rebuilt.blocks[0], core.ImageBlock)
            and rebuilt.blocks[0].pixels == red_pixels
            and others_unchanged,
            {
                "count": rebuilt.count,
                "block0_red": rebuilt.blocks[0].pixels == red_pixels,
                "other_blocks_verbatim": others_unchanged,
            },
        )

        # ---- 5. export logo.img with partition zero fill ------------------
        bin_data = modified_bin.read_bytes()
        pad_target = max(len(bin_data) + 4096, 2_100_000)
        img_path = out_dir / "logo_replaced.img"
        app.export_bytes_sync(img_path, pad_target=pad_target)
        img_data = img_path.read_bytes()
        prefix_ok = img_data[: len(bin_data)] == bin_data
        tail_ok = set(img_data[len(bin_data):]) <= {0}
        probe.check(
            "gui-export-padded-img",
            len(img_data) == pad_target and prefix_ok and tail_ok,
            {"size": len(img_data), "target": pad_target,
             "prefix_matches_bin": prefix_ok, "tail_zero_filled": tail_ok},
        )

        # ---- 6. export update.zip ----------------------------------------
        zip_path = out_dir / "update.zip"
        app.export_zip_sync(zip_path)
        expected_binary_sha = template_update_binary_sha256()
        with zipfile.ZipFile(zip_path, "r") as archive:
            assert archive.testzip() is None
            names = archive.namelist()
            script = archive.read(ANDROID_META + "updater-script").decode("utf-8", "replace")
            binary_sha = sha256(archive.read(ANDROID_META + "update-binary"))
            zip_logo_ok = archive.read("logo.bin") == modified_bin.read_bytes()
        probe.check(
            "gui-export-update-zip",
            "logo.bin" in names
            and ANDROID_META + "update-binary" in names
            and ANDROID_META + "updater-script" in names
            and "/dev/logo" in script
            and binary_sha == expected_binary_sha
            and zip_logo_ok,
            {
                "files": len(names),
                "script_has_dev_logo": "/dev/logo" in script,
                "update_binary_sha256": binary_sha,
                "template_binary_sha256": expected_binary_sha,
                "logo_matches_export": zip_logo_ok,
            },
        )

        # ---- 7. new project in the GUI -> pack -> reopen -------------------
        project_dir = out_dir / "gui_project"
        if project_dir.exists():
            import shutil

            shutil.rmtree(project_dir)
        app.create_project_sync(args.logo, project_dir)
        project_json = project_dir / "project.json"
        probe.check(
            "gui-create-project",
            project_json.is_file() and (project_dir / "header.bin").is_file(),
            {"project_dir": str(project_dir)},
        )
        packed = out_dir / "project_pack.bin"
        app.export_bytes_sync(packed)
        probe.check(
            "gui-project-pack-byte-identical",
            sha256_file(packed) == EXPECTED_LOGO_SHA256,
            {"sha256": sha256_file(packed)},
        )
        reopened = app.open_project_sync(project_dir)
        probe.check(
            "gui-reopen-project",
            reopened.count == 51,
            {"count": reopened.count, "dirty": reopened.dirty},
        )

        # ---- 8. lk.bin raw container mode ---------------------------------
        app.load_container_sync(args.lk)
        lk_out = out_dir / "lk_roundtrip.bin"
        app.export_bytes_sync(lk_out)
        probe.check(
            "gui-lk-raw-mode",
            app.session is not None and app.session.raw_mode
            and sha256_file(lk_out) == EXPECTED_LK_SHA256,
            {"raw_mode": None if app.session is None else app.session.raw_mode,
             "sha256": sha256_file(lk_out)},
        )

        # ---- 9. corrupt input error mapping (readable Chinese dialog text) -
        corrupt = ROOT / "tests" / "fixtures" / "generated" / "corrupt" / "bad_magic.bin"
        from logobuilder.ui import core_bridge

        try:
            app.load_container_sync(corrupt)
        except Exception as exc:  # noqa: BLE001 - expected failure path
            info = core_bridge.format_error(exc)
            message = info.get("message", "") if isinstance(info, dict) else str(info)
            probe.check(
                "gui-error-mapping",
                isinstance(exc, core.LogoBuilderError) and bool(CJK_RE.search(message)),
                {"exc": type(exc).__name__, "message": message[:160]},
            )
        else:
            probe.check("gui-error-mapping", False, "corrupt file was accepted silently")

        # ---- 10. GUI sizing metrics for 100% / 150% ----------------------
        metrics_100 = app.metrics
        probe.check(
            "gui-metrics-default",
            metrics_100.scale > 0 and metrics_100.window_default_width > 600,
            {"scale": metrics_100.scale,
             "window": [metrics_100.window_default_width, metrics_100.window_default_height]},
        )
    except Exception as exc:  # noqa: BLE001 - report, don't crash the harness
        print(f"PROBE EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        probe.check("gui-probe-completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass

    summary = {
        "probe": "analysis/qa/gui_probe.py",
        "python": sys.version.split()[0],
        "dpi_mode": dpi_mode,
        "out_dir": str(out_dir),
        "passed": probe.passed,
        "checks": probe.checks,
    }
    (out_dir / "gui_probe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("-" * 72)
    print("GUI PROBE RESULT:", "PASS" if probe.passed else "FAIL")
    return 0 if probe.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
