"""Build the engine as a folder with its own Python, for the desktop installer.

PyInstaller collects the engine, its libraries and the text-reading models into
build/engine/tasador-engine/ (tasador-engine.exe plus an _internal folder). A folder is
used instead of one big .exe because a one-file build would unpack ~350 MB on every start.
Then a smoke test starts the built program and checks what most often breaks in a
bundle: the HTTP server, the settings page file, text reading (model files) and barcodes.

Usage:
    python scripts/build_engine_bundle.py            # build + smoke test
    python scripts/build_engine_bundle.py --test-only
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "services" / "tasador-engine"
OUT = ROOT / "build" / "engine"
WORK = ROOT / "build" / "pyinstaller"
NAME = "tasador-engine"
EXE = OUT / NAME / (NAME + (".exe" if os.name == "nt" else ""))
ICON = ROOT / "apps" / "desktop" / "src-tauri" / "icons" / "icon.ico"


def engine_python() -> str:
    """The engine's virtualenv locally; on CI the engine is installed into this Python."""
    for candidate in (ENGINE / ".venv" / "Scripts" / "python.exe", ENGINE / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def build() -> None:
    py = engine_python()
    probe = subprocess.run([py, "-m", "PyInstaller", "--version"], capture_output=True, text=True)
    if probe.returncode != 0:
        raise SystemExit("PyInstaller is missing: uv pip install -p services/tasador-engine/.venv pyinstaller")
    WORK.mkdir(parents=True, exist_ok=True)
    entry = WORK / "tasador_engine_entry.py"
    entry.write_text(
        "import multiprocessing\nimport sys\n\nfrom tasador_engine.__main__ import main\n\n"
        "if __name__ == '__main__':\n    multiprocessing.freeze_support()\n    sys.exit(main())\n",
        encoding="utf-8",
    )
    command = [
        py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", NAME, "--distpath", str(OUT), "--workpath", str(WORK), "--specpath", str(WORK),
        "--paths", str(ENGINE / "src"),
        "--collect-submodules", "tasador_engine",
        # The settings page file: added by path, --collect-data misses it for a source checkout
        "--add-data", f"{ENGINE / 'src' / 'tasador_engine' / 'static'}{os.pathsep}tasador_engine/static",
        "--collect-all", "rapidocr_onnxruntime",     # OCR models and config files
        "--collect-submodules", "uvicorn",           # uvicorn picks its loops and protocols by name
        "--collect-all", "pillow_heif",
        "--hidden-import", "zxingcpp",
        "--exclude-module", "pytest",
        "--exclude-module", "tkinter",
        str(entry),
    ]
    if ICON.exists():
        command[command.index("--paths"):command.index("--paths")] = ["--icon", str(ICON)]
    print("[build] PyInstaller (a few minutes) ...", flush=True)
    subprocess.run(command, check=True, cwd=ROOT)
    size = sum(f.stat().st_size for f in (OUT / NAME).rglob("*") if f.is_file())
    print(f"[build] {EXE} ({size / 2**20:.0f} MB in the folder)")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str, timeout: float = 5) -> tuple[int, bytes]:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _nameplate_png() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    draw.text((30, 40), "MODEL DJM-900NXS2", fill="black", font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _post_photo(url: str, png: bytes) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"images\"; filename=\"plate.png\"\r\n"
        "Content-Type: image/png\r\n\r\n"
    ).encode() + png
    body += f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"context\"\r\n\r\n".encode()
    body += json.dumps({"search_prices": False}).encode() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def smoke_test() -> None:
    if not EXE.exists():
        raise SystemExit(f"{EXE} does not exist: build first")
    port = _free_port()
    env = {**os.environ, "TASADOR_DEPLOYMENT": "local", "TASADOR_SETTINGS_UI": "1",
           "TASADOR_VISION_PROVIDER": "none", "TASADOR_SEARCH_PROVIDER": "none"}
    log = WORK / "smoke.log"
    with open(log, "w", encoding="utf-8") as out:
        process = subprocess.Popen([str(EXE), "--port", str(port)], env=env, stdout=out, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 90
        while True:
            if process.poll() is not None:
                raise SystemExit(f"the engine exited with {process.returncode}:\n{log.read_text(encoding='utf-8')[-2000:]}")
            try:
                if _get(base + "/health")[0] == 200:
                    break
            except OSError:
                pass
            if time.time() > deadline:
                raise SystemExit("the engine did not answer /health within 90 s")
            time.sleep(1)
        print("[smoke] /health ok")

        capabilities = json.loads(_get(base + "/v1/capabilities")[1])
        provider = capabilities["provider"]
        assert provider["ocr"] and provider["barcode"], f"text reading or barcodes missing: {provider}"
        assert capabilities["heic"], "HEIC support missing"
        print("[smoke] text reading, barcodes and HEIC available")

        status, page = _get(f"http://localhost:{port}/")
        assert status == 200 and b"UI_KEY" in page, "settings page missing"
        print("[smoke] settings page served")

        result = _post_photo(base + "/v1/identify", _nameplate_png())
        text = " ".join(result["extracted"]["ocr_text"]).replace(" ", "")
        assert "DJM-900NXS2" in text, f"text reading did not read the test label: {result['extracted']}"
        print("[smoke] text reading read the test label")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    print("[smoke] passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--test-only", action="store_true", help="only run the smoke test on an existing build")
    args = parser.parse_args()
    if not args.test_only:
        if (OUT / NAME).exists():
            shutil.rmtree(OUT / NAME)
        build()
    smoke_test()
