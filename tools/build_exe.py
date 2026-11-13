#!/usr/bin/env python3
"""Build the Windows one-file executable for LogoBuilder 2 with PyInstaller.

Usage
-----
::

    python tools/build_exe.py --check-only     # validate prerequisites, build nothing
    python tools/build_exe.py                 # one-file windowed build -> dist/InspireMTKTools.exe
    python tools/build_exe.py --name LogoBuilder2   # legacy-name build (compatibility)
    python tools/build_exe.py --onedir         # directory build (faster, easier debugging)
    python tools/build_exe.py --smoke-test     # build, then run the exe with --self-test
    python tools/build_exe.py --check-only --json build/qa-build-check.json

What the build embeds
---------------------
* entry script   : ``app.py`` (repository root)
* import path    : ``src/`` (``--paths``) so ``import logobuilder`` works
* package data   : ``src/logobuilder/assets`` -> ``logobuilder/assets`` inside the
                   bundle.  ``logobuilder.core.sizes.assets_dir()`` resolves the
                   directory as ``Path(__file__).parent.parent / "assets"``; in a
                   frozen one-file app ``__file__`` lives under ``sys._MEIPASS``,
                   so the destination above is exactly what it expects.
* ttkbootstrap   : ``--collect-all ttkbootstrap`` (themes are JSON data files that
                   PyInstaller does not pick up from imports alone).
* logobuilder    : ``--collect-submodules logobuilder`` (GUI imports lazily).

Smoke-test procedure (manual verification of the frozen build)
--------------------------------------------------------------
1. ``python tools/build_exe.py --smoke-test``
2. ``dist\\LogoBuilder2.exe --self-test`` -> exit code 0 (windowed exe has no
   console; check ``%ERRORLEVEL%`` in cmd / ``$LASTEXITCODE`` in PowerShell).
3. Launch ``dist\\LogoBuilder2.exe``: main window opens, no missing-theme or
   missing-asset traceback in ``%LOCALAPPDATA%\\LogoBuilder2`` logs (if any).
4. Open the shipped ``LogoBuilder_jb51\\LogoBuilder_v1.6中文版\\logo.bin``, extract
   to a new project, pack and compare ``sha256`` with the original
   (``c321a9f1...655c``) -> byte-identical.
5. Generate ``update.zip`` from the pack output and open it in 7-Zip/``unzip -l``:
   ``logo.bin`` + ``META-INF/com/google/android/{update-binary,updater-script}``.

Notes
-----
* ``--windowed`` is Windows/macOS-only and is applied only on those platforms;
  on Linux the script builds a console bundle (useful for CI debugging).
* The CWM ``update-binary`` embedded in ``update_template.zip`` is GPL-licensed
  third-party software (see docs/qa.md "License notes"); it is redistributed
  verbatim inside the generated flashable zip.
* Cross-compiling for Windows must run on Windows; PyInstaller does not
  cross-compile. The QA matrix records the actual build host.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ENTRY = ROOT / "app.py"
SRC_DIR = ROOT / "src"
ASSETS_DIR = SRC_DIR / "logobuilder" / "assets"
#: v3.0.0 product name (exe output ``dist/InspireMTKTools.exe``).
DEFAULT_NAME = "InspireMTKTools"
#: Old v2 artifact name kept for build/check compatibility with existing scripts.
LEGACY_NAMES = ("LogoBuilder2",)
#: Product-brand markers that must appear in the visible layer (v3.0.0 rename).
BRAND_MARKERS = ("InspireMTKTools", "亿方联发科工具箱")
DEFAULT_DIST = ROOT / "dist"
DEFAULT_BUILD = ROOT / "build"
ASSET_DEST = "logobuilder/assets"
ICON_CANDIDATES = (
    ASSETS_DIR / "app.ico",
    ASSETS_DIR / "icon.ico",
    ASSETS_DIR / "LogoBuilder.ico",
    ASSETS_DIR / "app.icns",
)

REQUIRED_ASSETS = ("sizes.txt", "update_template.zip")
REQUIRED_MODULES = ("logobuilder", "logobuilder.core", "logobuilder.ui")
RUNTIME_IMPORTS = ("PIL", "PIL.Image", "PIL.ImageTk", "ttkbootstrap")
#: v3.0 LK-warning-patch feature must be present for a release build.
LK_CORE_MODULE = SRC_DIR / "logobuilder" / "core" / "lkpatch.py"
BRANDING_FILES = (
    APP_ENTRY,
    SRC_DIR / "logobuilder" / "i18n" / "catalog.py",
    SRC_DIR / "logobuilder" / "ui" / "main.py",
    SRC_DIR / "logobuilder" / "ui" / "app.py",
)


@dataclass
class Check:
    name: str
    ok: bool
    required: bool
    detail: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
        }


@dataclass
class CheckReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        self.checks.append(Check(name=name, ok=bool(ok), required=required, detail=detail))

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.required)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.required and not check.ok]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "failures": [check.name for check in self.failures],
            "checks": [check.as_dict() for check in self.checks],
            "host": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "machine": platform.machine(),
                "prefix": sysconfig.get_config_var("prefix") or "",
            },
        }


def _module_available(name: str) -> tuple[bool, str]:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError) as exc:  # pragma: no cover - broken installs
        return False, str(exc)
    if spec is None:
        return False, "not importable"
    return True, spec.origin or "namespace package"


def _package_version(dist_name: str) -> str | None:
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version(dist_name)
    except Exception:  # noqa: BLE001 - metadata can be missing in frozen hosts
        return None


def collect_checks(*, icon: Path | None = None) -> CheckReport:
    report = CheckReport()

    # --- Python / entry script ----------------------------------------
    report.add(
        "python-version",
        sys.version_info >= (3, 10),
        f"Python {sys.version.split()[0]} (requires >= 3.10)",
    )
    report.add(
        "entry-script",
        APP_ENTRY.is_file(),
        str(APP_ENTRY) if APP_ENTRY.is_file() else f"MISSING: {APP_ENTRY}",
    )
    if APP_ENTRY.is_file():
        text = APP_ENTRY.read_text(encoding="utf-8", errors="replace")
        report.add(
            "self-test-flag",
            "--self-test" in text,
            "app.py implements --self-test (needed by frozen smoke test)"
            if "--self-test" in text
            else "app.py does not mention --self-test; frozen smoke test cannot run",
            required=False,
        )

    # --- product branding (v3.0.0: 亿方联发科工具箱 / InspireMTKTools) --
    branding_hits: list[str] = []
    for brand_file in BRANDING_FILES:
        if not brand_file.is_file():
            continue
        try:
            content = brand_file.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable source file
            continue
        if any(marker in content for marker in BRAND_MARKERS):
            branding_hits.append(str(brand_file.relative_to(ROOT)))
    report.add(
        "branding",
        bool(branding_hits),
        "product name markers found in: " + ", ".join(branding_hits)
        if branding_hits
        else "no 'InspireMTKTools'/'亿方联发科工具箱' marker in app.py/i18n/UI",
    )

    # --- v3.0 LK warning-patch feature ---------------------------------
    report.add(
        "feature:lkpatch-core",
        LK_CORE_MODULE.is_file(),
        str(LK_CORE_MODULE.relative_to(ROOT)) if LK_CORE_MODULE.is_file()
        else f"MISSING: {LK_CORE_MODULE.relative_to(ROOT)} (v3.0 LK patch feature)",
    )
    ui_lk_modules = sorted(
        path.name for path in (SRC_DIR / "logobuilder" / "ui").glob("*lk*.py")
    ) if (SRC_DIR / "logobuilder" / "ui").is_dir() else []
    report.add(
        "feature:lkpatch-ui",
        bool(ui_lk_modules),
        ", ".join(ui_lk_modules) if ui_lk_modules else "no LK-patch UI module found yet",
        required=False,
    )

    # --- legacy v2 artifact compatibility -------------------------------
    suffix = ".exe" if sys.platform.startswith("win") else ""
    for legacy_name in LEGACY_NAMES:
        legacy_artifact = DEFAULT_DIST / f"{legacy_name}{suffix}"
        if legacy_artifact.is_file():
            report.add(
                "legacy-artifact",
                True,
                f"legacy {legacy_artifact.name} present in dist/; "
                f"the v3 build writes {DEFAULT_NAME}{suffix}",
                required=False,
            )
    report.add(
        "product-name",
        True,
        f"build output: dist/{DEFAULT_NAME}{suffix} "
        f"(legacy names accepted via --name: {', '.join(LEGACY_NAMES)})",
        required=False,
    )

    # --- package source tree ------------------------------------------
    for module in REQUIRED_MODULES:
        module_path = SRC_DIR.joinpath(*module.split("."))
        source_hint = str(module_path.relative_to(ROOT))
        has_init = (module_path / "__init__.py").is_file()
        report.add(
            f"package:{module}",
            has_init,
            source_hint if has_init else f"MISSING: {source_hint}/__init__.py",
        )

    # --- bundled data files -------------------------------------------
    for asset_name in REQUIRED_ASSETS:
        asset = ASSETS_DIR / asset_name
        report.add(
            f"asset:{asset_name}",
            asset.is_file(),
            f"{asset.relative_to(ROOT)} ({asset.stat().st_size} B)"
            if asset.is_file()
            else f"MISSING: {asset.relative_to(ROOT)}",
        )
    report.add(
        "assets-dir",
        ASSETS_DIR.is_dir(),
        f"{ASSETS_DIR.relative_to(ROOT)} -> bundle destination '{ASSET_DEST}'",
    )

    # --- third-party runtime dependencies -----------------------------
    for module in RUNTIME_IMPORTS:
        ok, detail = _module_available(module)
        report.add(f"runtime-import:{module}", ok, detail, required=module != "PIL.ImageTk")

    # --- PyInstaller ---------------------------------------------------
    pyinstaller_available, pyinstaller_detail = _module_available("PyInstaller")
    pyinstaller_version = _package_version("pyinstaller") or _package_version("PyInstaller")
    report.add(
        "pyinstaller",
        pyinstaller_available,
        f"PyInstaller {pyinstaller_version or '?'} ({pyinstaller_detail})"
        if pyinstaller_available
        else "PyInstaller not importable - run: python -m pip install pyinstaller",
    )
    if not pyinstaller_available:
        report.add(
            "pyinstaller-hint",
            True,
            "install with: python -m pip install pyinstaller",
            required=False,
        )

    # --- icon (optional) ----------------------------------------------
    chosen_icon = icon
    if chosen_icon is None:
        for candidate in ICON_CANDIDATES:
            if candidate.is_file():
                chosen_icon = candidate
                break
    if icon is not None and not Path(icon).is_file():
        report.add("icon", False, f"MISSING: {icon}", required=False)
    elif chosen_icon is not None:
        report.add("icon", True, str(chosen_icon))
    else:
        report.add(
            "icon",
            True,
            "no icon found (optional); add src/logobuilder/assets/app.ico to brand the exe",
            required=False,
        )

    # --- Windows windowed build sanity ---------------------------------
    report.add(
        "host-platform",
        True,
        f"{platform.system()} {platform.release()} ({platform.machine()})"
        + ("" if sys.platform.startswith("win") else " - not Windows: bundle will be a console build"),
        required=False,
    )
    report.add(
        "windowed-supported",
        True,
        "--windowed applied" if sys.platform in ("win32", "darwin")
        else "--windowed skipped (Linux)",
        required=False,
    )
    return report


def add_data_arg() -> str:
    """PyInstaller ``--add-data`` value for the bundled assets directory."""
    return f"{ASSETS_DIR}{os.pathsep}{ASSET_DEST}"


def build_command(
    *,
    name: str,
    dist_dir: Path,
    build_dir: Path,
    onefile: bool,
    clean: bool,
    icon: Path | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
    ]
    if clean:
        command.append("--clean")
    command += [
        "--name",
        name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--paths",
        str(SRC_DIR),
        "--add-data",
        add_data_arg(),
        "--collect-all",
        "ttkbootstrap",
        "--collect-submodules",
        "logobuilder",
        "--hidden-import",
        "PIL.ImageTk",
        "--hidden-import",
        "PIL._tkinter_finder",
    ]
    if onefile:
        command.append("--onefile")
    if sys.platform in ("win32", "darwin"):
        command.append("--windowed")
    if icon is not None:
        command += ["--icon", str(icon)]
    command.append(str(APP_ENTRY))
    return command


def print_report(report: CheckReport) -> None:
    print("=" * 76)
    print("InspireMTKTools packaging pre-flight check")
    print("=" * 76)
    for check in report.checks:
        if check.ok:
            mark = "PASS"
        elif check.required:
            mark = "FAIL"
        else:
            mark = "WARN"
        print(f"[{mark}] {check.name:32s} {check.detail}")
    print("-" * 76)
    failures = report.failures
    if failures:
        print(f"RESULT: FAIL - {len(failures)} required check(s) failed:")
        for check in failures:
            print(f"  - {check.name}: {check.detail}")
    else:
        print("RESULT: OK - build prerequisites satisfied")
    print("=" * 76)


def run_check_only(*, icon: Path | None, json_out: Path | None) -> int:
    report = collect_checks(icon=icon)
    print_report(report)
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"json report: {json_out}")
    return 0 if report.ok else 1


def run_build(args: argparse.Namespace) -> int:
    report = collect_checks(icon=args.icon)
    print_report(report)
    if not report.ok:
        print("refusing to build: fix the FAIL checks above first", file=sys.stderr)
        return 1

    dist_dir = Path(args.dist_dir)
    build_dir = Path(args.build_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(
        name=args.name,
        dist_dir=dist_dir,
        build_dir=build_dir,
        onefile=not args.onedir,
        clean=not args.no_clean,
        icon=args.icon,
    )
    print(">>> " + subprocess.list2cmdline(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        print(f"BUILD FAILED: PyInstaller exited {completed.returncode}", file=sys.stderr)
        return completed.returncode

    suffix = ".exe" if sys.platform.startswith("win") else ""
    if args.onedir:
        bundle = dist_dir / args.name
        target = bundle / f"{args.name}{suffix}"
    else:
        target = dist_dir / f"{args.name}{suffix}"
    if not target.is_file():
        print(f"BUILD FAILED: expected artifact not found: {target}", file=sys.stderr)
        return 1
    data = target.read_bytes()
    print("-" * 76)
    print(f"BUILD OK: {target}")
    print(f"  size   : {len(data):,} bytes")
    print(f"  sha256 : {hashlib.sha256(data).hexdigest()}")
    print(f"  build  : {platform.platform()} / Python {sys.version.split()[0]}")

    if args.smoke_test:
        return run_smoke_test(target, timeout=args.smoke_timeout)
    print("next     : python tools/build_exe.py --smoke-test")
    return 0


def run_smoke_test(exe: Path, *, timeout: float) -> int:
    """Run the frozen exe with ``--self-test`` and report its exit code.

    The frozen exe is a windowed (no console) build; its stdout/stderr may hold
    arbitrary UTF-8.  The raw output is always written to ``<exe>.smoke.log`` so
    the console never raises ``UnicodeEncodeError`` on a GBK terminal.
    """
    print("-" * 76)
    print(f"SMOKE TEST: {exe} --self-test (timeout {timeout:.0f}s)")
    try:
        smoke_env = os.environ.copy()
        smoke_env.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            [str(exe), "--self-test"], cwd=ROOT, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=smoke_env,
        )
    except subprocess.TimeoutExpired:
        print(f"SMOKE TEST FAILED: timed out after {timeout:.0f}s", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        return 1
    log_path = exe.with_suffix(".smoke.log")
    combined = ""
    if completed.stdout:
        combined += "--- stdout ---\n" + completed.stdout
    if completed.stderr:
        combined += "--- stderr ---\n" + completed.stderr
    try:
        log_path.write_text(combined, encoding="utf-8", errors="replace")
    except OSError:
        pass
    # Never let a GBK console encoding error mask the real result.
    safe = combined.encode("ascii", "backslashreplace").decode("ascii")
    if safe.strip():
        print(safe[-4000:])
    print(f"full output: {log_path}")
    ok = completed.returncode == 0
    print(f"SMOKE TEST {'PASSED' if ok else 'FAILED'}: exit code {completed.returncode}")
    return 0 if ok else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build InspireMTKTools with PyInstaller (or validate the configuration).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--check-only", action="store_true",
                        help="validate pyinstaller availability and build config, build nothing")
    parser.add_argument("--json", type=Path, default=None,
                        help="with --check-only: write the machine-readable report here")
    parser.add_argument("--name", default=DEFAULT_NAME, help="executable name")
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--onedir", action="store_true", help="build a directory bundle")
    parser.add_argument("--no-clean", action="store_true", help="do not run PyInstaller --clean")
    parser.add_argument("--icon", type=Path, default=None, help="custom .ico/.icns")
    parser.add_argument("--smoke-test", action="store_true",
                        help="after building, run the exe with --self-test")
    parser.add_argument("--smoke-timeout", type=float, default=180.0,
                        help="frozen self-test timeout in seconds (default 180)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # GBK consoles must not crash on unencodable self-test output
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    if args.check_only:
        return run_check_only(icon=args.icon, json_out=args.json)
    return run_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
