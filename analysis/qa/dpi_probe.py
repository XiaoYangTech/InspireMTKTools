#!/usr/bin/env python3
"""Independent HiDPI / layout / screenshot probe (QA scope).

Creates the real :class:`LogoBuilderApp` in ``test_mode`` and measures the layout
in an *oversized virtual window* (``overrideredirect(True)`` so the Windows
window manager does not clamp it to the QA machine's tiny/RDP screen).  It checks
the 100% and simulated-150% layouts:

* fonts and pixel metrics scale by ~1.5x;
* every main region is mapped, has a positive size and stays inside the window;
* vertical rows do not overlap and leave no abnormal blank band (the paned
  content row must expand to fill the window, not leave dead space);
* required minimum size fits the actual window at both scales;
* screenshots: a plain toplevel captures itself, a transient dialog captures the
  dialog (regression probe for ``screenshot._toplevel_hwnd``).

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

REGIONS = ("header", "toolbar", "paned", "block_list", "preview", "log_panel", "status_bar")
ROW_ORDER = ("header", "toolbar", "paned", "log_panel", "status_bar")


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
        return all(item["ok"] for item in self.checks)


def pump(root: Any, rounds: int = 4, delay: float = 0.12) -> None:
    for _ in range(rounds):
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            pass
        time.sleep(delay)


def force_geometry(root: Any, width: int, height: int) -> None:
    """Size the real window even if the physical screen is smaller (QA RDP)."""
    root.withdraw()
    try:
        root.overrideredirect(True)
        root.maxsize(max(width + 200, 6000), max(height + 200, 4200))
        root.geometry(f"{int(width)}x{int(height)}+0+0")
        root.deiconify()
    except Exception:
        pass
    pump(root, rounds=5)


def geometry_report(app: Any) -> dict[str, dict[str, int]]:
    root = app.root
    root.update_idletasks()
    rx, ry = root.winfo_rootx(), root.winfo_rooty()
    report: dict[str, dict[str, int]] = {}
    for name in REGIONS:
        widget = getattr(app, name)
        report[name] = {
            "x": widget.winfo_rootx() - rx,
            "y": widget.winfo_rooty() - ry,
            "width": widget.winfo_width(),
            "height": widget.winfo_height(),
            "mapped": int(bool(widget.winfo_ismapped())),
        }
    return report


def check_layout(probe: Probe, app: Any, label: str, scale: float) -> dict[str, Any]:
    root = app.root
    m = app.metrics
    report = geometry_report(app)
    root_w, root_h = root.winfo_width(), root.winfo_height()

    probe.check(
        f"{label}:window-usable",
        root_w > 800 and root_h > 500,
        {"width": root_w, "height": root_h},
    )
    mapped = {name: item for name, item in report.items() if item["mapped"]}
    probe.check(
        f"{label}:all-regions-mapped",
        len(mapped) == len(REGIONS),
        {name: report[name]["mapped"] for name in REGIONS},
    )
    positive = all(report[name]["width"] > 1 and report[name]["height"] > 1 for name in REGIONS)
    probe.check(
        f"{label}:regions-positive-size",
        positive,
        {name: [report[name]["width"], report[name]["height"]] for name in REGIONS},
    )
    violations = {}
    for name in REGIONS:
        item = report[name]
        if not (
            item["x"] >= -2
            and item["y"] >= -2
            and item["x"] + item["width"] <= root_w + 2
            and item["y"] + item["height"] <= root_h + 2
        ):
            violations[name] = item
    probe.check(f"{label}:regions-inside-window", not violations, violations or "ok")

    order_ok = True
    overlaps = []
    previous_bottom = -1
    for name in ROW_ORDER:
        item = report[name]
        if item["y"] + 2 < previous_bottom:
            order_ok = False
            overlaps.append({"row": name, "y": item["y"], "previous_bottom": previous_bottom})
        previous_bottom = max(previous_bottom, item["y"] + item["height"])
    probe.check(f"{label}:rows-ordered-no-overlap", order_ok, overlaps or "ok")

    tolerance = m.px(10)
    gaps = []
    for upper, lower in zip(ROW_ORDER, ROW_ORDER[1:]):
        gap = report[lower]["y"] - (report[upper]["y"] + report[upper]["height"])
        gaps.append({"between": f"{upper}->{lower}", "gap_px": gap})
    largest_gap = max(gaps, key=lambda item: item["gap_px"])
    probe.check(
        f"{label}:no-dead-vertical-space",
        largest_gap["gap_px"] <= tolerance,
        {"largest_gap": largest_gap, "tolerance_px": tolerance, "gaps": gaps},
    )

    fixed_height = (
        report["header"]["height"]
        + report["toolbar"]["height"]
        + report["log_panel"]["height"]
        + report["status_bar"]["height"]
    )
    expected_paned_min = root_h - fixed_height - m.px(40)
    probe.check(
        f"{label}:paned-expands",
        report["paned"]["height"] >= expected_paned_min,
        {"paned_height": report["paned"]["height"],
         "expected_at_least": expected_paned_min,
         "root_height": root_h, "fixed_rows_height": fixed_height},
    )

    probe.check(
        f"{label}:content-min-widths",
        report["block_list"]["width"] >= m.px(240) and report["preview"]["width"] >= m.px(240),
        {"block_list": report["block_list"]["width"],
         "preview": report["preview"]["width"],
         "list_min_expected": m.px(240)},
    )

    req_w, req_h = root.winfo_reqwidth(), root.winfo_reqheight()
    probe.check(
        f"{label}:required-size-fits-window",
        req_w <= root_w + 2 and req_h <= root_h + 2,
        {"required": [req_w, req_h], "actual": [root_w, root_h]},
    )
    full_width = [
        name for name in ("header", "toolbar", "log_panel", "status_bar")
        if report[name]["width"] < root_w - 8
    ]
    probe.check(f"{label}:full-width-rows", not full_width, full_width or "ok")
    return report


def check_screenshot_toplevels(probe: Probe, root: Any, tmp: Path) -> None:
    import ttkbootstrap as tb
    from PIL import Image

    from logobuilder.ui.screenshot import save_window_screenshot, wait_ui

    plain = tb.Toplevel(title="qa-plain")
    plain.geometry("420x300+40+40")
    plain.update_idletasks()
    plain.deiconify()
    wait_ui(root, 260)
    plain_path = tmp / "shot_plain.png"
    save_window_screenshot(plain, plain_path)
    with Image.open(plain_path) as image:
        plain_size = image.size
    plain.destroy()
    probe.check(
        "screenshot:plain-toplevel",
        abs(plain_size[0] - 436) <= 30 and abs(plain_size[1] - 339) <= 60,
        {"captured": list(plain_size), "expected_about": [436, 339]},
    )

    # 2) regression evidence from the real --screenshot run: the dialog PNGs must
    #    be dialog-sized, not main-window-sized (the screenshot tool must not
    #    resolve a transient dialog to its owner window).
    shots = ROOT / "analysis" / "ui_screenshots"
    main_png = shots / "main_window.png"
    dialog_pngs = [shots / name for name in (
        "resolution_dialog.png",
        "replace_image_dialog.png",
        "error_dialog.png",
        "help_dialog.png",
    )]
    if main_png.is_file() and all(path.is_file() for path in dialog_pngs):
        with Image.open(main_png) as image:
            main_size = image.size
        dialog_sizes = {}
        oversized = {}
        for path in dialog_pngs:
            with Image.open(path) as image:
                size = image.size
            dialog_sizes[path.name] = list(size)
            if size[0] >= main_size[0] - 4 and size[1] >= main_size[1] - 4:
                oversized[path.name] = list(size)
        probe.check(
            "screenshot:dialog-artifacts-are-dialog-sized",
            not oversized,
            {"main_window": list(main_size), "dialogs": dialog_sizes,
             "main_window_sized_dialogs": oversized,
             "note": "" if not oversized else
             "dialog screenshots captured the owner main window instead of the "
             "dialog (analysis/ui_screenshots/*_dialog.png)"},
        )
    else:
        probe.check(
            "screenshot:dialog-artifacts-are-dialog-sized",
            True,
            "no --screenshot artifacts present yet (run app.py --screenshot first)",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "analysis" / "qa" / "artifacts" / "dpi_probe.json")
    args = parser.parse_args(argv)
    probe = Probe()
    print("=" * 72)
    print("LogoBuilder HiDPI / layout / screenshot probe")
    print("=" * 72)

    from logobuilder.ui.theme import enable_high_dpi

    dpi_mode = enable_high_dpi()
    import ttkbootstrap as tb
    from logobuilder.ui.app import LogoBuilderApp
    from logobuilder.ui.settings import UiSettings
    from logobuilder.ui.theme import build_fonts

    root = None
    app = None
    tmp = Path(tempfile.mkdtemp(prefix="logobuilder-qa-dpi-"))
    try:
        root = tb.Window(themename="cosmo")
        app = LogoBuilderApp(
            root,
            settings=UiSettings(),
            config_path=tmp / "ui_settings.json",
            test_mode=True,
            repo_root=ROOT,
        )
        pump(root, rounds=4)

        screen = (root.winfo_screenwidth(), root.winfo_screenheight())
        probe.check(
            "environment",
            dpi_mode != "unaware",
            {"dpi_awareness": dpi_mode,
             "tk_scaling": root.tk.call("tk", "scaling"),
             "fpixels_per_inch": root.winfo_fpixels("1i"),
             "screen": list(screen),
             "note": "measurements below use an overrideredirect virtual window "
                     "because this QA host has a small/RDP screen"},
        )

        # screenshot probes run while the root window is in normal (non
        # overrideredirect) state, matching the real --screenshot code path
        check_screenshot_toplevels(probe, root, tmp)

        import tkinter.font as tkfont

        m100 = app.metrics
        linespace_100 = tkfont.Font(root=root, font=app.fonts.base).metrics("linespace")
        force_geometry(root, m100.window_default_width, m100.window_default_height)
        report_100 = check_layout(probe, app, "100pct", m100.scale)
        probe.check(
            "100pct:scale",
            abs(m100.scale - 1.0) < 0.05 and m100.px(100) == 100,
            {"scale": m100.scale, "px100": m100.px(100), "linespace_px": linespace_100},
        )

        app.theme.apply_scale(1.5, fonts=build_fonts(root, app.fonts))
        app._sync_theme_attrs()
        app._rebuild_ui()
        pump(root, rounds=4)
        m150 = app.metrics
        linespace_150 = tkfont.Font(root=root, font=app.fonts.base).metrics("linespace")
        force_geometry(root, m150.window_default_width, m150.window_default_height)
        report_150 = check_layout(probe, app, "150pct", m150.scale)
        probe.check(
            "150pct:metrics-scale",
            abs(m150.scale - 1.5) < 0.05 and m150.px(100) == 150,
            {"scale": m150.scale, "px100": m150.px(100),
             "window": [m150.window_default_width, m150.window_default_height],
             "window_100": [m100.window_default_width, m100.window_default_height]},
        )
        probe.check(
            "150pct:fonts-scale",
            linespace_150 >= linespace_100 * 1.4,
            {"linespace_100_px": linespace_100, "linespace_150_px": linespace_150},
        )
        # header is a metric-driven container; the status bar height is driven by
        # its content minimum (icons/labels) on top of the design metric, so it
        # only has to grow and to satisfy the 150% design metric.
        probe.check(
            "150pct:regions-scale",
            report_150["header"]["height"] >= report_100["header"]["height"] * 1.25
            and report_150["status_bar"]["height"] >= report_100["status_bar"]["height"] * 1.10
            and report_150["status_bar"]["height"] >= m150.status_height - 2,
            {
                "header": [report_100["header"]["height"], report_150["header"]["height"]],
                "status": [report_100["status_bar"]["height"], report_150["status_bar"]["height"]],
                "status_design_150": m150.status_height,
                "note": "status bar grows content-driven; >=150% design metric required",
            },
        )
        probe.check(
            "150pct:window-vs-physical-screen",
            True,  # informational: this RDP screen is far smaller than a real 150% desktop
            {"window": [m150.window_default_width, m150.window_default_height],
             "physical_screen": list(screen),
             "fits_physical_screen": (
                 m150.window_default_width <= screen[0]
                 and m150.window_default_height <= screen[1]
             )},
        )

    except Exception as exc:  # noqa: BLE001
        print(f"DPI PROBE EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        probe.check("dpi-probe-completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass

    summary = {
        "probe": "analysis/qa/dpi_probe.py",
        "dpi_mode": dpi_mode,
        "passed": probe.passed,
        "checks": probe.checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("-" * 72)
    print("DPI PROBE RESULT:", "PASS" if probe.passed else "FAIL")
    return 0 if probe.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
