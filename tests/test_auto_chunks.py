"""D-12: a long document cannot fall into the unbounded single graph.

`auto_chunks` is the declared rule (over 120 s, chunks of 20 s), and `render()`
applies it when the caller does not choose. The second half of the test replaces
`edl3_chunks.render_chunked` with a recorder, so the assertion is about *which
path* the render took, not about spending a minute of ffmpeg.

python tests/test_auto_chunks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3_chunks, edl3_render  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


class FakeTimeline:
    def __init__(self, duration: float) -> None:
        self.duration = duration


def main() -> int:
    check(
        "a 60 s document keeps one graph",
        edl3_render.auto_chunks(60.0) is None,
        edl3_render.auto_chunks(60.0),
    )
    check(
        "exactly 120 s still keeps one graph",
        edl3_render.auto_chunks(120.0) is None,
        edl3_render.auto_chunks(120.0),
    )
    check(
        "120.1 s takes the chunk path",
        edl3_render.auto_chunks(120.1) == 7,
        edl3_render.auto_chunks(120.1),
    )
    check(
        "two hours become 360 chunks of 20 s",
        edl3_render.auto_chunks(7200.0) == 360,
        edl3_render.auto_chunks(7200.0),
    )

    calls = []

    def recorder(timeline, output, **kwargs):
        calls.append(kwargs)
        return {"chunks": kwargs.get("chunks"), "output": str(output), "path": "chunked"}

    real = edl3_chunks.render_chunked
    edl3_chunks.render_chunked = recorder
    try:
        report = edl3_render.render(
            FakeTimeline(300.0), ROOT / "tests" / "_out" / "auto_chunks.mp4"
        )
    finally:
        edl3_chunks.render_chunked = real

    check(
        "render() sends a 300 s document through the chunk path",
        calls and calls[0].get("chunks") == 15,
        calls[0].get("chunks") if calls else "not called",
    )
    check(
        "the report says the choice was automatic",
        report.get("chunks_auto") is True,
        report.get("chunks_auto"),
    )

    calls.clear()
    real = edl3_chunks.render_chunked
    edl3_chunks.render_chunked = recorder
    try:
        edl3_render.render(
            FakeTimeline(30.0),
            ROOT / "tests" / "_out" / "auto_chunks_short.mp4",
            chunks=None,
            settings={"chromium_available": False},
        )
    except Exception as exc:  # noqa: BLE001 - a short document reaches the real renderer
        print(f"    [INFO] short document reached the single graph: {type(exc).__name__}")
    finally:
        edl3_chunks.render_chunked = real

    check(
        "a 30 s document does not take the chunk path",
        not calls,
        f"{len(calls)} call(s)",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
