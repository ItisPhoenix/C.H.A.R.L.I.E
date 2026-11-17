"""Canonical Charlie runtime launcher."""

# ruff: noqa: E402, I001

import argparse
import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Start Charlie's Windows runtime", allow_abbrev=False)
    parser.add_argument(
        "--headless-companion",
        "--companion",
        dest="companion_mode",
        action="store_true",
        help="start the Windows voice and device-control companion without the dashboard",
    )
    parser.add_argument(
        "--device-gateway",
        dest="device_gateway",
        action="store_true",
        help="start only the native Windows device gateway (execution endpoint, no Brain)",
    )
    return parser.parse_args(argv)

if __name__ == "__main__":
    try:
        _args = _parse_args()
        if sys.platform == "win32":
            _root = Path(__file__).resolve().parent
            _venv_python = _root / ".venv" / "Scripts" / "python.exe"
            if not _venv_python.is_file():
                print(f"Charlie requires its project interpreter: {_venv_python}", file=sys.stderr)
                sys.exit(2)
            if str(Path(sys.executable).resolve()).casefold() != str(_venv_python.resolve()).casefold():
                sys.exit(subprocess.call(
                    [str(_venv_python), "-B", str(Path(__file__).resolve()), *sys.argv[1:]],
                    cwd=_root,
                ))
            if _args.companion_mode:
                _required = ("sounddevice", "faster_whisper", "kokoro_onnx", "uiautomation", "comtypes")
                _missing = [name for name in _required if importlib.util.find_spec(name) is None]
                if _missing:
                    print(
                        "Charlie companion dependencies are missing from .venv: " + ", ".join(_missing),
                        file=sys.stderr,
                    )
                    sys.exit(2)
        from charlie.runtime import configure as _configure_platform

        _configure_platform()
        if _args.device_gateway:
            from charlie.device_gateway import run_device_gateway

            sys.exit(asyncio.run(run_device_gateway()))
        from main import main

        if _args.companion_mode:
            sys.exit(asyncio.run(main(companion_mode=True)))
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
else:
    from charlie.runtime import configure as _configure_platform

    _configure_platform()
    from main import main
