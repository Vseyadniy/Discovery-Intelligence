"""
Build a real, double-clickable macOS application bundle for the launcher — no
Terminal window, shows up in Finder / Launchpad / Dock like any other app.

This does NOT bundle Python (it's a personal tool, not a distributable): it creates
a `.app` whose launcher runs THIS repo's `app.py` with the SAME Python interpreter
you build with (so tkinter / openpyxl / pyyaml are guaranteed present). The repo
path and interpreter are baked in, so the app works even after you drag it to
/Applications.

Usage:
    python build_macos_app.py                 # → "Discovery Research.app" in the repo
    python build_macos_app.py --into /Applications
    python build_macos_app.py --name "My Research"

Rebuild after you move the repo or change Python environments.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP_ENTRY = REPO / "app.py"
ICON_PNG = REPO / "design" / "icon.png"


def _make_icns(png: Path, out_icns: Path) -> bool:
    """design/icon.png → .icns via the macOS built-ins (sips + iconutil)."""
    try:
        with tempfile.TemporaryDirectory() as td:
            iconset = Path(td) / "app.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):   # the canonical iconset set
                subprocess.run(["sips", "-z", str(size), str(size), str(png),
                                "--out", str(iconset / f"icon_{size}x{size}.png")],
                               check=True, capture_output=True)
                subprocess.run(["sips", "-z", str(size * 2), str(size * 2), str(png),
                                "--out", str(iconset / f"icon_{size}x{size}@2x.png")],
                               check=True, capture_output=True)
            subprocess.run(["iconutil", "-c", "icns", str(iconset),
                            "-o", str(out_icns)], check=True, capture_output=True)
        return out_icns.exists()
    except Exception as e:
        print(f"  (icon skipped: {e})")
        return False


def build(app_name: str, into: Path) -> Path:
    if sys.platform != "darwin":
        raise SystemExit("This builder targets macOS (.app bundles).")
    if not APP_ENTRY.exists():
        raise SystemExit(f"Cannot find {APP_ENTRY}")

    app = into / f"{app_name}.app"
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    if app.exists():
        shutil.rmtree(app)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    python = sys.executable  # the interpreter that has tkinter + the deps
    exec_name = "launcher"

    # Info.plist — makes Finder treat it as a foreground GUI app.
    info = {
        "CFBundleName": app_name,
        "CFBundleDisplayName": app_name,
        "CFBundleIdentifier": "com.discovery.research-launcher",
        "CFBundleExecutable": exec_name,
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "10.13",
        "NSHighResolutionCapable": True,
        "LSBackgroundOnly": False,
        # Tk apps are not Retina-native; keep it a normal windowed app.
        "NSPrincipalClass": "NSApplication",
    }
    if ICON_PNG.exists() and _make_icns(ICON_PNG, resources / "app.icns"):
        info["CFBundleIconFile"] = "app"
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)

    # Launcher script — the bundle's executable. GUI-launched apps get a minimal
    # PATH, so we hard-code absolute paths to both the interpreter and app.py.
    launcher = macos / exec_name
    launcher.write_text(
        "#!/bin/bash\n"
        f'cd "{REPO}"\n'
        f'exec "{python}" "{APP_ENTRY}" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # PkgInfo (optional but conventional)
    (contents / "PkgInfo").write_text("APPL????", encoding="utf-8")

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a macOS .app bundle for the launcher.")
    ap.add_argument("--name", default="Discovery Research", help='app name (default: "Discovery Research")')
    ap.add_argument("--into", type=Path, default=REPO,
                    help="where to create the .app (default: the repo; use /Applications to install)")
    args = ap.parse_args()

    app = build(args.name, args.into.expanduser().resolve())
    print(f"✓ Built {app}")
    print(f"  interpreter: {sys.executable}")
    print(f"  runs:        {APP_ENTRY}")
    if args.into.resolve() == REPO:
        print("\nNext: drag it into /Applications (or run: "
              f"python build_macos_app.py --into /Applications), then open from Launchpad.")
    print("First launch: right-click → Open once (Gatekeeper) if macOS blocks an unsigned app.")


if __name__ == "__main__":
    main()
