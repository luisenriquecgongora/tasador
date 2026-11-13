"""Run the engine: `python -m tasador_engine --port 8000`, or the bundled `tasador-engine.exe`.

The desktop app starts this program and stops it again; it is the same server that
`uvicorn tasador_engine.main:app_from_env --factory` starts.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tasador-engine", description="Tasador engine HTTP server")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)

    from . import __version__

    if args.version:
        print(__version__)
        return 0

    import uvicorn

    from .main import app_from_env

    uvicorn.run(app_from_env(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # needed when bundled with PyInstaller
    sys.exit(main())
