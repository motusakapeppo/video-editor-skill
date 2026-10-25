"""Launcher for the video-editor toolkit.

    D:\\video_env\\Scripts\\python.exe scripts\\vx.py <command> [options]

Kept deliberately thin: all logic lives in the `vx` package next to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from vx.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
