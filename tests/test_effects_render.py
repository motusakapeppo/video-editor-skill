"""The composed-effect bridge executes transform/grade/blur/blend and curves.

    python tests/test_effects_render.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass. It runs real ffmpeg on a synthetic clip (``testsrc2``), never the
network.

What it proves:

R1. a three-primitive spec (transform + grade + blur) renders to a file whose
    duration and resolution are the declared ones, and two extracted frames are
    measurably different from the source (mean absolute difference reported);
R2. a historical name (``zoom``) goes through ``expand()`` and produces the very
    same graph and file as the hand-written spec (sha256 equal, i.e. PSNR inf);
R3. a spec containing ``particles`` is refused with a declared Italian error and
    no file is produced;
R4. a spec whose ``cost`` exceeds the budget of 8 is refused with the two
    numbers in the sentence, and no file is produced;
R5. ``blend`` executes against a second source and the mix curve changes the
    picture across time;
R6. a bezier curve is refused instead of silently approximated;
R7. ``/api/state`` exposes ``estimate`` and ``plan_choice`` from the report, and
    returns empty mappings when there is nothing to show;
R9. ``mask`` renders through ``maskedmerge`` with a generated ellipse mask:
    the centre keeps the source and the corners go black, ``invert`` swaps the
    two regions (positive/negative difference reported);
R10. ``time`` keeps the declared duration: ``freeze`` makes 0.2 s and 1.8 s
    identical while the source differs, ``speed``/``reverse`` still render;
R11. ``text`` draws bright pixels on a dark clip with the job's font,
    ``tracking`` widens the line, and a text spec without a font is refused with
    ``FONT_MISSING`` and no file.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl3, edl3_render, ffmpeg, serve  # noqa: E402
from vx.effects import cost, expand  # noqa: E402
from vx.effects.ffmpeg import RenderSpecError  # noqa: E402

WIDTH = 320
HEIGHT = 240
FPS = 30
SECONDS = 2.0
PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:66} {detail}")


def run(command: list[str]) -> None:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:])


def make_source(path: Path, seconds: float, pattern: str = "testsrc2") -> None:
    graph = (
        f"{pattern}:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}"
        if pattern.startswith("color=")
        else f"{pattern}=s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}"
    )
    run(
        [
            ffmpeg.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            graph,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(path),
        ]
    )


def frame(path: Path, at: float, destination: Path) -> np.ndarray:
    run(
        [
            ffmpeg.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{at:.3f}",
            "-frames:v",
            "1",
            "-y",
            str(destination),
        ]
    )
    return np.asarray(Image.open(destination).convert("RGB"), dtype=np.float32)


def mean_abs(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.abs(left - right).mean())


def probe(path: Path) -> dict:
    from vx import probe as probe_module

    found = probe_module.probe(str(path))
    return {
        "duration": float(found.duration),
        "width": int(found.width),
        "height": int(found.height),
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx_effects_render_") as temporary:
        root = Path(temporary)
        source = root / "source.mp4"
        make_source(source, SECONDS)
        measured = probe(source)
        print("\nR1 -- il clip sintetico e' la base della prova")
        check(
            "R1 sorgente testsrc2 320x240, 2 s",
            abs(measured["duration"] - SECONDS) < 0.1
            and (measured["width"], measured["height"]) == (WIDTH, HEIGHT),
            measured,
        )

        trio = {
            "id": "trio",
            "duration": SECONDS,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"scale": [1.0, 1.18], "x": [0, 20]},
                    "curve": {"easing": "out"},
                },
                {
                    "kind": "grade",
                    "params": {
                        "contrast": [1.0, 1.25],
                        "saturation": 1.15,
                        "lut": "warm",
                    },
                    "curve": {"easing": "in_out"},
                },
                {
                    "kind": "blur",
                    "params": {"sigma": [0.0, 3.0]},
                    "curve": {"easing": "out"},
                },
            ],
        }
        trio_out = root / "trio" / "trio.mp4"
        trio_report = edl3_render.render_effect_spec(
            trio,
            source,
            trio_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "trio",
        )
        trio_probe = probe(trio_out)
        print("\nR1b -- tre primitive: il file esce con la geometria dichiarata")
        check(
            "R1b file prodotto e non vuoto",
            trio_out.is_file() and trio_out.stat().st_size > 0,
            trio_out.stat().st_size if trio_out.is_file() else 0,
        )
        check(
            "R1b durata 2 s e risoluzione 320x240",
            abs(trio_probe["duration"] - SECONDS) < 0.1
            and (trio_probe["width"], trio_probe["height"]) == (WIDTH, HEIGHT),
            trio_probe,
        )
        check(
            "R1b il costo resta sotto il budget",
            trio_report["cost"] <= 8.0,
            f"cost={trio_report['cost']}",
        )
        check(
            "R1b il grafo nomina zoompan, eq e gblur",
            all(
                token in trio_report["filter_script"]
                for token in ("zoompan", "eq@fxgrade", "gblur@fxblur")
            ),
        )

        src_early = frame(source, 0.2, root / "src_early.png")
        src_late = frame(source, 1.8, root / "src_late.png")
        out_early = frame(trio_out, 0.2, root / "out_early.png")
        out_late = frame(trio_out, 1.8, root / "out_late.png")
        early_diff = mean_abs(src_early, out_early)
        late_diff = mean_abs(src_late, out_late)
        motion_diff = mean_abs(out_early, out_late)
        print("\nR1c -- i fotogrammi sono visibilmente diversi dal sorgente")
        check(
            "R1c fotogramma 0.2 s: differenza media > 8/255",
            early_diff > 8.0,
            f"diff={early_diff:.3f}",
        )
        check(
            "R1c fotogramma 1.8 s: differenza media > 8/255",
            late_diff > 8.0,
            f"diff={late_diff:.3f}",
        )
        check(
            "R1c animazione: i due fotogrammi d'uscita differiscono > 8/255",
            motion_diff > 8.0,
            f"diff={motion_diff:.3f}",
        )

        print("\nR2 -- 'zoom' storico via expand() = specifica esplicita")
        zoom_name = root / "zoom_name" / "zoom.mp4"
        zoom_spec = root / "zoom_spec" / "zoom.mp4"
        name_report = edl3_render.render_effect_spec(
            "zoom",
            source,
            zoom_name,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "zoom_name",
        )
        spec_report = edl3_render.render_effect_spec(
            expand("zoom"),
            source,
            zoom_spec,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "zoom_spec",
        )
        check(
            "R2 lo stesso grafo per nome storico e specifica",
            name_report["filter_script"] == spec_report["filter_script"],
        )
        check(
            "R2 sha256 uguale (PSNR infinito, stesso risultato)",
            sha(zoom_name) == sha(zoom_spec),
            sha(zoom_name)[:16],
        )

        print("\nR8 -- il percorso timeline di edl3_render legge la specifica")
        timeline = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, HEIGHT),
            clips=[
                edl3.Clip3(
                    id="spec_clip",
                    source=str(source),
                    src_in=0.0,
                    src_out=SECONDS,
                )
            ],
        )
        compiled = edl3_render.compile_timeline(
            timeline, effect_specs={"spec_clip": trio}
        )
        spec_entry = compiled["clips"][0]["effect_spec"]
        check(
            "R8 compile_timeline applica la specifica alla clip",
            isinstance(spec_entry, dict)
            and spec_entry.get("id") == "trio"
            and spec_entry.get("primitives") == ["transform", "grade", "blur"],
            spec_entry,
        )
        check(
            "R8 il grafo della clip contiene i tre filtri della specifica",
            all(
                token in compiled["filter_script"]
                for token in ("zoompan", "eq@fxgrade", "gblur@fxblur")
            ),
        )
        integrated_out = root / "integrated" / "master.mp4"
        integrated = edl3_render.render(
            timeline,
            integrated_out,
            workdir=root / "integrated",
            effect_specs={"spec_clip": trio},
        )
        check(
            "R8 render() produce il file con la specifica",
            integrated_out.is_file()
            and integrated_out.stat().st_size > 0
            and abs(float(integrated["duration"]) - SECONDS) < 0.1,
            integrated.get("duration"),
        )

        print("\nR3 -- particles: errore dichiarato, nessun file")
        particles_out = root / "particles" / "particles.mp4"
        particles_error = None
        try:
            edl3_render.render_effect_spec(
                {"id": "p", "duration": SECONDS, "primitives": [{"kind": "particles"}]},
                source,
                particles_out,
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                duration=SECONDS,
            )
        except edl3_render.CompileError as exc:
            particles_error = exc
        check(
            "R3 codice PRIMITIVE_NOT_RENDERED",
            particles_error is not None
            and particles_error.code == "PRIMITIVE_NOT_RENDERED",
            "" if particles_error is None else particles_error.code,
        )
        check(
            "R3 il messaggio italiano nomina particles e il rifiuto",
            particles_error is not None
            and "particles" in particles_error.detail
            and "non la esegue" in particles_error.detail,
            "" if particles_error is None else particles_error.detail[:110],
        )
        check("R3 nessun file prodotto", not particles_out.exists())

        print("\nR4 -- costo > 8: rifiuto dichiarato, nessun file")
        heavy_out = root / "heavy" / "heavy.mp4"
        heavy = {
            "id": "heavy",
            "duration": 60.0,
            "primitives": [
                {"kind": "blur", "params": {"sigma": 100.0}} for _ in range(16)
            ],
        }
        heavy_error = None
        try:
            edl3_render.render_effect_spec(
                heavy,
                source,
                heavy_out,
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                duration=60.0,
            )
        except edl3_render.CompileError as exc:
            heavy_error = exc
        check(
            "R4 codice BUDGET_EXCEEDED",
            heavy_error is not None and heavy_error.code == "BUDGET_EXCEEDED",
            "" if heavy_error is None else heavy_error.code,
        )
        check(
            "R4 il messaggio dichiara costo e budget",
            heavy_error is not None
            and "10.000" in heavy_error.detail
            and "8.000" in heavy_error.detail,
            "" if heavy_error is None else heavy_error.detail[:110],
        )
        check(
            "R4 la stima e' davvero sopra il tetto",
            cost(heavy) > 8.0,
            f"cost={cost(heavy)}",
        )
        check("R4 nessun file prodotto", not heavy_out.exists())

        print("\nR5 -- blend con la seconda sorgente")
        second = root / "second.mp4"
        make_source(second, SECONDS, pattern="color=c=red")
        blend_out = root / "blend" / "blend.mp4"
        blend = {
            "id": "blend",
            "duration": SECONDS,
            "primitives": [
                {
                    "kind": "blend",
                    "params": {"mode": "normal", "mix": [0.0, 1.0], "source": "bg"},
                    "curve": {"easing": "in_out"},
                }
            ],
        }
        blend_report = edl3_render.render_effect_spec(
            blend,
            source,
            blend_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "blend",
            extra_inputs={"bg": second},
        )
        blend_probe = probe(blend_out)
        blend_early = frame(blend_out, 0.1, root / "blend_early.png")
        blend_late = frame(blend_out, 1.9, root / "blend_late.png")
        check(
            "R5 file blend prodotto, durata e risoluzione corrette",
            blend_out.is_file()
            and abs(blend_probe["duration"] - SECONDS) < 0.1
            and (blend_probe["width"], blend_probe["height"]) == (WIDTH, HEIGHT),
            blend_probe,
        )
        check(
            "R5 il grafo contiene blend e la curva di mix",
            "blend@fxblend0" in blend_report["filter_script"]
            and "all_opacity" in blend_report["filter_script"],
        )
        check(
            "R5 mix 0 -> 1: inizio e fine differiscono > 8/255",
            mean_abs(blend_early, blend_late) > 8.0,
            f"diff={mean_abs(blend_early, blend_late):.3f}",
        )

        print("\nR6 -- bezier: rifiuto dichiarato, non approssimazione")
        bezier_out = root / "bezier" / "bezier.mp4"
        bezier_error = None
        try:
            edl3_render.render_effect_spec(
                {
                    "id": "bezier",
                    "duration": SECONDS,
                    "primitives": [
                        {
                            "kind": "blur",
                            "params": {"sigma": [0.0, 4.0]},
                            "curve": {
                                "easing": "bezier",
                                "points": [[0.42, 0.0], [0.58, 1.0]],
                            },
                        }
                    ],
                },
                source,
                bezier_out,
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                duration=SECONDS,
            )
        except (edl3_render.CompileError, RenderSpecError) as exc:
            bezier_error = exc
        check(
            "R6 codice CURVE_NOT_REPRESENTABLE",
            bezier_error is not None
            and getattr(bezier_error, "code", "") == "CURVE_NOT_REPRESENTABLE",
            "" if bezier_error is None else getattr(bezier_error, "code", ""),
        )
        check("R6 nessun file prodotto", not bezier_out.exists())

        print("\nR7 -- /api/state espone estimate e plan_choice")
        job = root / "job"
        job.mkdir()
        (job / "report.json").write_text(
            json.dumps(
                {
                    "estimate": {"cost": 2.188, "budget": 8.0},
                    "plan_choice": {"choice": "fast", "values": ["full", "fast"]},
                }
            ),
            encoding="utf-8",
        )
        payload = serve.state(job)
        check(
            "R7 estimate arriva dal report",
            payload.get("estimate") == {"cost": 2.188, "budget": 8.0},
            payload.get("estimate"),
        )
        check(
            "R7 plan_choice arriva dal report",
            payload.get("plan_choice", {}).get("choice") == "fast"
            and payload.get("plan_choice", {}).get("values") == ["full", "fast"],
            payload.get("plan_choice"),
        )
        empty = serve.state(root / "nessun_lavoro")
        check(
            "R7 senza lavoro i due campi sono vuoti",
            empty.get("estimate") == {} and empty.get("plan_choice") == {},
            {key: empty.get(key) for key in ("estimate", "plan_choice")},
        )
        bare = root / "bare_job"
        bare.mkdir()
        (bare / "report.json").write_text("{}", encoding="utf-8")
        bare_payload = serve.state(bare)
        check(
            "R7 un lavoro senza i campi li espone vuoti",
            bare_payload.get("estimate") == {}
            and bare_payload.get("plan_choice") == {},
            {
                key: bare_payload.get(key)
                for key in ("estimate", "plan_choice")
            },
        )

        print("\nR9 -- mask: maskedmerge con maschera generata da geq")
        mask_out = root / "mask" / "mask.mp4"
        mask_spec = {
            "id": "mask",
            "duration": SECONDS,
            "primitives": [
                {
                    "kind": "mask",
                    "params": {
                        "shape": "ellipse",
                        "cx": 0.0,
                        "cy": 0.0,
                        "width": 0.6,
                        "height": 0.6,
                        "feather": 20.0,
                    },
                }
            ],
        }
        mask_report = edl3_render.render_effect_spec(
            mask_spec,
            source,
            mask_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "mask",
        )
        mask_probe = probe(mask_out)
        check(
            "R9 file mask prodotto, durata 2 s, 320x240",
            mask_out.is_file()
            and abs(mask_probe["duration"] - SECONDS) < 0.1
            and (mask_probe["width"], mask_probe["height"]) == (WIDTH, HEIGHT),
            mask_probe,
        )
        check(
            "R9 il grafo usa maskedmerge con una maschera geq",
            "maskedmerge" in mask_report["filter_script"]
            and "geq=" in mask_report["filter_script"],
        )
        src_mid = frame(source, 0.9, root / "mask_src.png")
        mask_mid = frame(mask_out, 0.9, root / "mask_mid.png")
        mask_diff = mean_abs(src_mid, mask_mid)
        mask_centre = mean_abs(src_mid[110:130, 150:170], mask_mid[110:130, 150:170])
        mask_corner = mean_abs(src_mid[:40, :40], mask_mid[:40, :40])
        check(
            "R9 differenza media vs sorgente > 8/255",
            mask_diff > 8.0,
            f"diff={mask_diff:.3f}",
        )
        check(
            "R9 il centro resta il sorgente (diff < 6/255)",
            mask_centre < 6.0,
            f"centre={mask_centre:.3f}",
        )
        check(
            "R9 gli angoli sono neri (diff > 30/255)",
            mask_corner > 30.0,
            f"corner={mask_corner:.3f}",
        )

        inverted_out = root / "mask_invert" / "invert.mp4"
        inverted_spec = {
            "id": "mask_invert",
            "duration": SECONDS,
            "primitives": [
                {
                    "kind": "mask",
                    "params": {
                        "shape": "ellipse",
                        "width": 0.6,
                        "height": 0.6,
                        "feather": 20.0,
                        "invert": True,
                    },
                }
            ],
        }
        edl3_render.render_effect_spec(
            inverted_spec,
            source,
            inverted_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "mask_invert",
        )
        inverted_mid = frame(inverted_out, 0.9, root / "mask_inv.png")
        inverted_centre = mean_abs(
            src_mid[110:130, 150:170], inverted_mid[110:130, 150:170]
        )
        inverted_corner = mean_abs(src_mid[:40, :40], inverted_mid[:40, :40])
        check(
            "R9 invert: il centro e' nero (diff > 30/255)",
            inverted_centre > 30.0,
            f"centre={inverted_centre:.3f}",
        )
        check(
            "R9 invert: gli angoli restano il sorgente (diff < 6/255)",
            inverted_corner < 6.0,
            f"corner={inverted_corner:.3f}",
        )
        signed_centre = float(
            (mask_mid[110:130, 150:170] - inverted_mid[110:130, 150:170]).mean()
        )
        signed_corner = float(
            (mask_mid[:40, :40] - inverted_mid[:40, :40]).mean()
        )
        check(
            "R9 invert nei due sensi: centro positivo, angolo negativo",
            signed_centre > 0.0 > signed_corner,
            f"centre={signed_centre:.2f} corner={signed_corner:.2f}",
        )

        print("\nR10 -- time: freeze identico, durata dichiarata rispettata")
        freeze_out = root / "freeze" / "freeze.mp4"
        freeze_spec = {
            "id": "freeze",
            "duration": SECONDS,
            "primitives": [
                {"kind": "time", "params": {"mode": "freeze", "at": 0.0, "hold": 2.0}}
            ],
        }
        freeze_report = edl3_render.render_effect_spec(
            freeze_spec,
            source,
            freeze_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "freeze",
        )
        freeze_probe = probe(freeze_out)
        check(
            "R10 freeze prodotto, durata 2 s, 320x240",
            freeze_out.is_file()
            and abs(freeze_probe["duration"] - SECONDS) < 0.1
            and (freeze_probe["width"], freeze_probe["height"]) == (WIDTH, HEIGHT),
            freeze_probe,
        )
        check(
            "R10 il grafo usa trim e tpad (durata tenuta)",
            "tpad=" in freeze_report["filter_script"]
            and "trim=" in freeze_report["filter_script"],
        )
        frozen_early = frame(freeze_out, 0.2, root / "freeze_early.png")
        frozen_late = frame(freeze_out, 1.8, root / "freeze_late.png")
        source_early = frame(source, 0.2, root / "freeze_src_early.png")
        source_late = frame(source, 1.8, root / "freeze_src_late.png")
        frozen_delta = mean_abs(frozen_early, frozen_late)
        source_delta = mean_abs(source_early, source_late)
        check(
            "R10 freeze: 0.2 s e 1.8 s identici (diff ~0)",
            frozen_delta < 1.0,
            f"diff={frozen_delta:.4f}",
        )
        check(
            "R10 il sorgente invece differisce (> 8/255)",
            source_delta > 8.0,
            f"diff={source_delta:.3f}",
        )

        mid_freeze_out = root / "freeze_mid" / "freeze_mid.mp4"
        edl3_render.render_effect_spec(
            {
                "id": "freeze_mid",
                "duration": SECONDS,
                "primitives": [
                    {"kind": "time", "params": {"mode": "freeze", "at": 0.6, "hold": 0.4}}
                ],
            },
            source,
            mid_freeze_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "freeze_mid",
        )
        mid_a = frame(mid_freeze_out, 0.75, root / "freeze_mid_a.png")
        mid_b = frame(mid_freeze_out, 0.95, root / "freeze_mid_b.png")
        mid_delta = mean_abs(mid_a, mid_b)
        check(
            "R10 freeze con at=0.6, hold=0.4: la finestra e' ferma",
            mid_delta < 1.0,
            f"diff={mid_delta:.4f}",
        )

        speed_out = root / "speed" / "speed.mp4"
        speed_report = edl3_render.render_effect_spec(
            {
                "id": "speed",
                "duration": SECONDS,
                "primitives": [{"kind": "time", "params": {"mode": "speed", "rate": 2.0}}],
            },
            source,
            speed_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "speed",
        )
        speed_probe = probe(speed_out)
        speed_mid = frame(speed_out, 0.9, root / "speed_mid.png")
        check(
            "R10 speed 2x: durata resta 2 s e il quadro cambia",
            abs(speed_probe["duration"] - SECONDS) < 0.1
            and mean_abs(src_mid, speed_mid) > 8.0,
            f"durata={speed_probe['duration']:.3f} diff={mean_abs(src_mid, speed_mid):.3f}",
        )
        check(
            "R10 speed usa setpts",
            "setpts=" in speed_report["filter_script"],
        )

        reverse_out = root / "reverse" / "reverse.mp4"
        edl3_render.render_effect_spec(
            {
                "id": "reverse",
                "duration": SECONDS,
                "primitives": [{"kind": "time", "params": {"mode": "reverse"}}],
            },
            source,
            reverse_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "reverse",
        )
        reverse_probe = probe(reverse_out)
        reverse_mid = frame(reverse_out, 0.9, root / "reverse_mid.png")
        check(
            "R10 reverse: durata 2 s e il quadro e' invertito",
            abs(reverse_probe["duration"] - SECONDS) < 0.1
            and mean_abs(src_mid, reverse_mid) > 8.0,
            f"durata={reverse_probe['duration']:.3f} diff={mean_abs(src_mid, reverse_mid):.3f}",
        )

        print("\nR11 -- text: font del lavoro, pixel chiari, tracking")
        dark = root / "dark.mp4"
        make_source(dark, SECONDS, pattern="color=c=black")
        job_font_dir = root / "text" / "fonts"
        job_font_dir.mkdir(parents=True, exist_ok=True)
        job_font = job_font_dir / "Antonio-Bold.ttf"
        shutil.copy2(ROOT / "assets" / "fonts" / "Antonio-Bold.ttf", job_font)

        def text_spec(content: str, tracking: float) -> dict:
            return {
                "id": "text",
                "duration": SECONDS,
                "primitives": [
                    {
                        "kind": "text",
                        "params": {
                            "content": content,
                            "size": 64.0,
                            "color": "#FFFFFF",
                            "align": "center",
                            "x": 0.0,
                            "y": 0.0,
                            "tracking": tracking,
                            "font": str(job_font),
                        },
                    }
                ],
            }

        text_out = root / "text" / "text.mp4"
        text_report = edl3_render.render_effect_spec(
            text_spec("TITOLO", 0.0),
            dark,
            text_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "text",
        )
        text_probe = probe(text_out)
        dark_frame = frame(dark, 0.9, root / "text_dark.png")
        text_frame = frame(text_out, 0.9, root / "text_frame.png")
        before = int((dark_frame.max(axis=2) > 200).sum())
        after = int((text_frame.max(axis=2) > 200).sum())
        check(
            "R11 file text prodotto, durata 2 s",
            text_out.is_file() and abs(text_probe["duration"] - SECONDS) < 0.1,
            text_probe,
        )
        check(
            "R11 pixel chiari dove prima non c'erano",
            before == 0 and after > 1000,
            f"before={before} after={after}",
        )
        check(
            "R11 il grafo usa drawtext con il fontfile del lavoro",
            "drawtext=" in text_report["filter_script"]
            and "fontfile=" in text_report["filter_script"],
        )

        tracked_out = root / "text_tracked" / "tracked.mp4"
        edl3_render.render_effect_spec(
            text_spec("TITOLO", 8.0),
            dark,
            tracked_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "text_tracked",
        )
        tracked_frame = frame(tracked_out, 0.9, root / "text_tracked.png")
        plain_columns = np.where((text_frame.max(axis=2) > 200).any(axis=0))[0]
        tracked_columns = np.where((tracked_frame.max(axis=2) > 200).any(axis=0))[0]
        plain_width = int(plain_columns.max() - plain_columns.min() + 1)
        tracked_width = int(tracked_columns.max() - tracked_columns.min() + 1)
        check(
            "R11 tracking 8 px allarga la riga di ~5*8 px",
            tracked_width > plain_width + 20,
            f"plain={plain_width} tracked={tracked_width}",
        )

        no_font_out = root / "text_nofont" / "nofont.mp4"
        no_font_error = None
        try:
            edl3_render.render_effect_spec(
                {
                    "id": "nofont",
                    "duration": SECONDS,
                    "primitives": [
                        {"kind": "text", "params": {"content": "Titolo", "size": 48.0}}
                    ],
                },
                dark,
                no_font_out,
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                duration=SECONDS,
                workdir=root / "text_nofont",
            )
        except edl3_render.CompileError as exc:
            no_font_error = exc
        check(
            "R11 senza font: codice FONT_MISSING",
            no_font_error is not None and no_font_error.code == "FONT_MISSING",
            "" if no_font_error is None else no_font_error.code,
        )
        check(
            "R11 senza font: nessun file prodotto",
            not no_font_out.exists(),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
