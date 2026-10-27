"""Fill policy A2: crop, fit and blur are three distinct graph shapes.

``fit`` is today's letterbox and stays the default; ``crop`` covers and
centre-crops without bands; ``blur`` puts a blurred cover plate under the whole
fitted frame. The declared upscale is pure Python arithmetic on the source and
target geometry, clamped to exactly 1.0 when the source already covers.

python tests/test_fill_policy.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, render  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0

TARGET = (1080, 1920)
SOURCE = (480, 848)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _plan(resolution=TARGET) -> EDLPlan:
    clip = ClipSegment("v0_0", "src", 0.0, 3.0, 0.0, 3.0, 0, "main_talking_head")
    return EDLPlan(
        fps=30.0,
        duration_sec=3.0,
        aspect_ratio="9:16",
        resolution=resolution,
        sources={"src": SourceRef("a.mp4", "a" * 64, 3.0)},
        video_tracks=(Track(0, (clip,), "a_roll"),),
        audio_tracks=(),
    )


def _graph(fill=None) -> str:
    plan = _plan()
    if fill is None:
        return render.build_edl_filtergraph(plan, {"src": 0}, has_audio=False)
    return render.build_edl_filtergraph(plan, {"src": 0}, has_audio=False, fill=fill)


def main() -> int:
    fit = _graph("fit")
    crop = _graph("crop")
    blur = _graph("blur")

    check("tre grafi distinti", len({fit, crop, blur}) == 3, (fit, crop, blur))
    check("fit: bande con pad", "pad=" in fit, fit[-90:])
    check(
        "fit: scale adattata (decrease)",
        "force_original_aspect_ratio=decrease" in fit,
    )
    check("crop: nessun pad", "pad=" not in crop, crop[-90:])
    check("crop: cover e taglio al centro", "crop=" in crop, crop[-90:])
    check(
        "crop: scale in aumento (increase)",
        "force_original_aspect_ratio=increase" in crop,
    )
    check(
        "crop: geometria del taglio",
        f"crop={TARGET[0]}:{TARGET[1]}:(iw-{TARGET[0]})/2:(ih-{TARGET[1]})/2" in crop,
        crop,
    )
    check("blur: fondo sfocato", "gblur" in blur, blur)
    check(
        "blur: sigma riusato dal trattamento fit",
        f"gblur=sigma={render.FIT_BLUR_SIGMA}" in blur,
        render.FIT_BLUR_SIGMA,
    )
    check(
        "blur: esattamente un overlay",
        blur.count("overlay") == 1,
        blur.count("overlay"),
    )
    check(
        "blur: primo piano integro (decrease + overlay centrato)",
        "force_original_aspect_ratio=decrease" in blur
        and "overlay=(W-w)/2:(H-h)/2" in blur,
        blur[-120:],
    )
    check("default = fit", _graph() == fit)

    try:
        _graph("stretch")
    except render.RenderFailure as exc:
        check("politica ignota rifiutata", True, str(exc))
    else:
        check("politica ignota rifiutata", False, "nessuna eccezione")

    check(
        "render_edl accetta fill con default fit",
        inspect.signature(render.render_edl).parameters["fill"].default == "fit",
    )
    check(
        "build_edl_filtergraph accetta fill con default fit",
        inspect.signature(render.build_edl_filtergraph).parameters["fill"].default
        == "fit",
    )

    upscaled = auto.fill_upscale(SOURCE, TARGET)
    check("upscale 480x848 -> 1080x1920", abs(upscaled - 2.264) <= 1e-9, upscaled)
    check("upscale oltre 2.2", upscaled > 2.2, upscaled)
    check(
        "upscale clampato a 1.0 quando la sorgente copre",
        auto.fill_upscale((1080, 1920), TARGET) == 1.0
        and auto.fill_upscale((2160, 3840), TARGET) == 1.0,
        (
            auto.fill_upscale((1080, 1920), TARGET),
            auto.fill_upscale((2160, 3840), TARGET),
        ),
    )
    check(
        "upscale arrotondato a 3 decimali",
        auto.fill_upscale((1000, 1000), TARGET) == 1.92,
        auto.fill_upscale((1000, 1000), TARGET),
    )

    declared = auto._fill_declaration(
        _plan(),
        {"src": {"width": SOURCE[0], "height": SOURCE[1], "duration": 3.0}},
        "blur",
    )
    check(
        "dichiarazione fill per ogni clip a_roll",
        declared
        == [
            {
                "clip_id": "v0_0",
                "mode": "blur",
                "src_size": list(SOURCE),
                "target_size": list(TARGET),
                "upscale": 2.264,
            }
        ],
        declared,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
