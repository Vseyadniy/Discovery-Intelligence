"""
Build a shareable installer for the Discovery Research app (macOS + Windows).

Produces  dist/DiscoveryResearch-<date>.zip  containing the app code, configs,
prompts and docs — WITHOUT your data: no logs/, no outputs/, no db/kb.sqlite,
no versions/, and crucially no .env (API keys never leave your machine; the
recipient enters their own in the app's Settings tab).

The recipient unzips and double-clicks ONE file:
  macOS   → install.command : python3 check, local .venv, deps, builds an
            icon'd double-clickable "Discovery Research.app".
  Windows → install.bat     : Python check, local .venv, deps, creates
            "Discovery Research.bat" + a Desktop shortcut with the app icon.

Usage:  python make_installer.py
"""
from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

INCLUDE_FILES = [
    "app.py", "build_macos_app.py", "requirements.txt", "README.md",
    ".env.example", "db/schema.sql", "design/icon.png", "design/icon.ico",
]
INCLUDE_TREES = ["src", "config", "prompts", "templates"]
INCLUDE_DOCS = ["docs/researcher_codex_manual.md", "docs/test_run_plan.md",
                "docs/feature_qual_onepager.md"]

INSTALL_CMD = """#!/bin/bash
# Discovery Research — one-time installer (macOS). Double-click me.
set -e
cd "$(dirname "$0")"
echo "── Discovery Research installer ──"
if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install it from https://www.python.org/downloads/ and re-run."
  exit 1
fi
echo "1/3 creating local Python environment (.venv)…"
python3 -m venv .venv
echo "2/3 installing dependencies…"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
echo "3/3 building the app bundle…"
./.venv/bin/python build_macos_app.py --name "Discovery Research"
mkdir -p logs inputs outputs
echo ""
echo "Done. Open «Discovery Research.app» (or run ./launch_app.command)."
echo "API keys are optional — enter them in the «Settings» tab to enable ⚡ API mode."
"""

LAUNCH_CMD = """#!/bin/bash
cd "$(dirname "$0")"
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" app.py
"""

INSTALL_BAT = """@echo off
rem Discovery Research — one-time installer (Windows). Double-click me.
setlocal
cd /d "%~dp0"
echo == Discovery Research installer ==

set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3 not found. Install it from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" in the installer, then re-run me.
  pause
  exit /b 1
)

echo 1/3 creating local Python environment (.venv)...
%PY% -m venv .venv || (pause & exit /b 1)
echo 2/3 installing dependencies...
".venv\\Scripts\\python" -m pip install --quiet --upgrade pip
".venv\\Scripts\\python" -m pip install --quiet -r requirements.txt || (pause & exit /b 1)

echo 3/3 creating launcher and Desktop shortcut...
> "Discovery Research.bat" (
  echo @echo off
  echo start "" "%%~dp0.venv\\Scripts\\pythonw.exe" "%%~dp0app.py"
)
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\Discovery Research.lnk');" ^
  "$s.TargetPath=(Resolve-Path '.venv\\Scripts\\pythonw.exe').Path;" ^
  "$s.Arguments='\"'+(Resolve-Path 'app.py').Path+'\"';" ^
  "$s.WorkingDirectory=(Get-Location).Path;" ^
  "$s.IconLocation=(Resolve-Path 'design\\icon.ico').Path;" ^
  "$s.Save()"
if not exist logs mkdir logs
if not exist inputs mkdir inputs
if not exist outputs mkdir outputs

echo.
echo Done. Double-click "Discovery Research" on your Desktop
echo (or "Discovery Research.bat" in this folder).
echo API keys are optional - enter them in the Settings tab to enable API mode.
pause
"""

LAUNCH_BAT = """@echo off
cd /d "%~dp0"
if exist ".venv\\Scripts\\pythonw.exe" (
  start "" ".venv\\Scripts\\pythonw.exe" app.py
) else (
  python app.py
)
"""

README_INSTALL = """# Discovery Research — install

macOS:    double-click `install.command`  →  then open «Discovery Research.app».
Windows:  double-click `install.bat`      →  then the «Discovery Research»
          shortcut on your Desktop (or `Discovery Research.bat` in this folder).
Linux:    python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
          && ./.venv/bin/python app.py

The app works without any API key (Prompt mode). To enable the automatic
⚡ API mode, enter an OpenAI and/or Anthropic key in the «Settings» tab.

Note: the «.app» / shortcut is created BY the installer on each machine — it is
a launcher for this folder, so keep the folder where you unzipped it (or move
it first, then re-run the installer).
"""


def build() -> Path:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"DiscoveryResearch-{date.today().isoformat()}.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def put(path: Path, arc: str):
            nonlocal n
            z.write(path, arc)
            n += 1

        for rel in INCLUDE_FILES + INCLUDE_DOCS:
            p = ROOT / rel
            if p.exists():
                put(p, rel)
        for tree in INCLUDE_TREES:
            for p in sorted((ROOT / tree).rglob("*")):
                if p.is_dir() or "__pycache__" in p.parts or p.name == ".DS_Store":
                    continue
                put(p, str(p.relative_to(ROOT)))
        # generated helper scripts (executable bit set via external_attr)
        for name, body in (("install.command", INSTALL_CMD),
                           ("launch_app.command", LAUNCH_CMD),
                           ("install.bat", INSTALL_BAT.replace("\n", "\r\n")),
                           ("launch_app.bat", LAUNCH_BAT.replace("\n", "\r\n")),
                           ("README_INSTALL.md", README_INSTALL)):
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o755 << 16
            z.writestr(info, body)
            n += 1
    print(f"[installer] {n} files → {out}")
    return out


if __name__ == "__main__":
    build()
