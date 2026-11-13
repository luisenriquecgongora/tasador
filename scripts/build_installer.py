"""Build the stand-alone Windows installer: dist/Tasador-v<version>-windows-x64-setup.exe

1. The engine with its own Python (scripts/build_engine_bundle.py, smoke-tested).
2. The desktop app, bundled by Tauri into an NSIS installer that also carries the engine
   folder (apps/desktop/src-tauri/tauri.installer.json adds it as a resource).
3. dist/version.json: version, file name, SHA-256 and the public download URLs, for the
   release page and a future in-app update check.

The installer needs no administrator rights (it installs for the current user) and no
Python. Ollama (local recognition) and Docker Desktop (local search) stay separate
installs: the app works without them and says what is missing.

Releases are built by .github/workflows/release.yml on GitHub's Windows machines, which
then publish to both the GitHub Release and Cloudflare R2. This script also runs locally.

Usage:
    python scripts/build_installer.py                       # version from tauri.conf.json
    python scripts/build_installer.py --version 0.2.0 --skip-engine
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "desktop" / "src-tauri"
ENGINE_EXE = ROOT / "build" / "engine" / "tasador-engine" / "tasador-engine.exe"
DIST = ROOT / "dist"
PRODUCT = "Tasador"
PUBLIC = os.environ.get("R2_CUSTOM_DOMAIN", "https://pub-fb9de4da4f48439ebb34b3795d05ab11.r2.dev").rstrip("/")
REPO = "https://github.com/luisenriquecgongora/tasador"


def engine_python() -> str:
    """The engine's virtualenv locally; on CI the engine is installed into this Python."""
    for candidate in (ROOT / "services/tasador-engine/.venv/Scripts/python.exe", ROOT / "services/tasador-engine/.venv/bin/python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("Building the installer needs Node.js (npx runs the Tauri bundler): https://nodejs.org")
    print("[installer] bundling the app with Tauri (compiles the app, then packs everything) ...", flush=True)
    subprocess.run(
        [npx, "--yes", "@tauri-apps/cli@2", "build", "--bundles", "nsis", "--config", "tauri.installer.json"],
        check=True,
        cwd=APP,
    )

    built = sorted((APP / "target" / "release" / "bundle" / "nsis").glob(f"*{version}*-setup.exe"), key=lambda p: p.stat().st_mtime)
    if not built:
        raise SystemExit("Tauri finished but no installer was found under target/release/bundle/nsis")
    DIST.mkdir(exist_ok=True)
    final = DIST / f"{PRODUCT}-v{version}-windows-x64-setup.exe"
    shutil.copy2(built[-1], final)

    manifest = {
        "product": PRODUCT,
        "version": version,
        "released_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "windows_x64": {
            "file": final.name,
            "size_bytes": final.stat().st_size,
            "sha256": sha256(final),
            "url": f"{PUBLIC}/tasador/v{version}/{final.name}",
            "latest_url": f"{PUBLIC}/tasador/latest/{PRODUCT}-windows-x64-setup.exe",
        },
        "release_page": f"{REPO}/releases/tag/v{version}",
    }
    (DIST / "version.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[installer] {final} ({final.stat().st_size / 2**20:.0f} MB), sha256 {manifest['windows_x64']['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
