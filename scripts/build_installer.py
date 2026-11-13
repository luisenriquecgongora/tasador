"""Build the stand-alone installer for this computer's system:
  Windows: dist/Tasador-v<version>-windows-x64-setup.exe  (NSIS, installs for the current user)
  macOS:   dist/Tasador-v<version>-macos-arm64.dmg         (Apple Silicon; ad-hoc signed)

1. The engine with its own Python (scripts/build_engine_bundle.py, smoke-tested).
2. The desktop app, bundled by Tauri with the engine folder as a resource
   (apps/desktop/src-tauri/tauri.installer.json adds it).
3. macOS only: the .dmg is mounted and the engine inside the app is smoke-tested again, so a
   bundling problem (missing files, broken links) fails the build instead of the user.

The installer needs no administrator rights and no Python. Ollama (local recognition) and
Docker Desktop (local search) stay separate installs: the app works without them and says
what is missing. PyInstaller only builds for the system it runs on, so each installer is
built on its own system.

Releases are built by .github/workflows/release.yml on GitHub's Windows and macOS machines;
scripts/publish_release_to_r2.py then writes version.json and publishes both installers.
This script also runs locally.

Usage:
    python scripts/build_installer.py                       # version from tauri.conf.json
    python scripts/build_installer.py --version 0.2.0 --skip-engine
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "desktop" / "src-tauri"
ENGINE_NAME = "tasador-engine" + (".exe" if os.name == "nt" else "")
ENGINE_EXE = ROOT / "build" / "engine" / "tasador-engine" / ENGINE_NAME
DIST = ROOT / "dist"
PRODUCT = "Tasador"


def target() -> tuple[str, str, str, str]:
    """(platform name, Tauri bundle, Tauri output glob, file suffix) for this computer."""
    if sys.platform == "win32":
        return "windows-x64", "nsis", "*{version}*-setup.exe", "-setup.exe"
    if sys.platform == "darwin":
        arch = "arm64" if platform.machine() == "arm64" else "x64"
        return f"macos-{arch}", "dmg", "*{version}*.dmg", ".dmg"
    raise SystemExit("Installers are built on Windows and macOS only")


def engine_python() -> str:
    """The engine's virtualenv locally; on CI the engine is installed into this Python."""
    for candidate in (ROOT / "services/tasador-engine/.venv/Scripts/python.exe", ROOT / "services/tasador-engine/.venv/bin/python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def smoke_test_dmg(dmg: Path) -> None:
    """Mounts the .dmg read-only and smoke-tests the engine as the installed app has it."""
    mount = Path(tempfile.mkdtemp(prefix="tasador-dmg-"))
    subprocess.run(["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-mountpoint", str(mount)], check=True)
    try:
        engines = sorted(mount.glob(f"*.app/Contents/Resources/engine/{ENGINE_NAME}"))
        if len(engines) != 1:
            raise SystemExit(f"Expected the engine inside the app in {dmg.name}, found {[str(e) for e in engines]}")
        print(f"[installer] smoke-testing the engine inside {dmg.name} ...", flush=True)
        subprocess.run([engine_python(), str(ROOT / "scripts" / "build_engine_bundle.py"), "--test-only", "--exe", str(engines[0])], check=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help="release version; must match tauri.conf.json (default: its version)")
    parser.add_argument("--skip-engine", action="store_true", help="reuse the engine bundle from an earlier build")
    args = parser.parse_args()

    app_version = json.loads((APP / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    version = (args.version or app_version).lstrip("v")
    if version != app_version:
        raise SystemExit(f"--version {version} does not match tauri.conf.json ({app_version})")

    if not args.skip_engine:
        subprocess.run([engine_python(), str(ROOT / "scripts" / "build_engine_bundle.py")], check=True)
    if not ENGINE_EXE.exists():
        raise SystemExit(f"{ENGINE_EXE} is missing: run without --skip-engine")

    name, bundle, pattern, suffix = target()
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("Building the installer needs Node.js (npx runs the Tauri bundler): https://nodejs.org")
    print("[installer] bundling the app with Tauri (compiles the app, then packs everything) ...", flush=True)
    subprocess.run(
        [npx, "--yes", "@tauri-apps/cli@2", "build", "--bundles", bundle, "--config", "tauri.installer.json"],
        check=True,
        cwd=APP,
    )

    out_dir = APP / "target" / "release" / "bundle" / bundle
    built = sorted(out_dir.glob(pattern.format(version=version)), key=lambda p: p.stat().st_mtime)
    if not built:
        raise SystemExit(f"Tauri finished but no installer was found under {out_dir}")
    DIST.mkdir(exist_ok=True)
    final = DIST / f"{PRODUCT}-v{version}-{name}{suffix}"
    shutil.copy2(built[-1], final)
    if bundle == "dmg":
        smoke_test_dmg(final)
    print(f"[installer] {final} ({final.stat().st_size / 2**20:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
