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
R6. a ``bezier`` curve is sampled at 24 points into a ``t``-dependent expression,
    and the measured difference from the exact curve is under the declared 1%
    (R6 also renders the curve and compares the frame at a node with the frame a
    static transform at the expected value produces);
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
R12. ``mask`` composes inside the *timeline* path (a multi-input stage spliced
    into one clip's chain): two frames differ, the corners are black;
R13. ``freeze`` composes inside the timeline path, both as a single branch and
    as a ``split``/``concat`` fan-out, and two held frames are identical;
R14. what is still refused is refused by name: a timeline ``blend`` with a
    second source (``BLEND_INPUT_UNAVAILABLE``), a rotation
    (``PARAM_NOT_RENDERED`` on ``params.rotate``) and a bezier without points
    (``CURVE_NOT_REPRESENTABLE``);
R15. two sources on different clocks (30 fps and 29.97) meet in a crossfade:
    every input is normalised to the document's clock, the render finishes and
    the dissolve lasts 3.5 s; a document without a dissolve keeps the graph it
    had (no normalisation is inserted);
R15b. ``tests/fixtures/edl3_minimal/doc.json`` — the documented example — renders
    as written: audio bus, crossfade, clips without ``dst_in``/``dst_out``.
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
from vx.effects import cost, expand, sample_curve  # noqa: E402
from vx.effects import ffmpeg as bridge  # noqa: E402
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
    make_source_at(path, seconds, FPS, pattern)


def make_source_at(path: Path, seconds: float, rate: object, pattern: str = "testsrc2") -> None:
    graph = (
        f"{pattern}:s={WIDTH}x{HEIGHT}:r={rate}:d={seconds}"
        if pattern.startswith("color=")
        else f"{pattern}=s={WIDTH}x{HEIGHT}:r={rate}:d={seconds}"
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


def audio_bed(path: Path, seconds: float, frequency: float = 440.0) -> None:
    """A continuous bed already at the delivery loudness (-14 LUFS)."""
    run(
        [
            ffmpeg.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
            "-af",
            "loudnorm=I=-14:TP=-1:LRA=11",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-y",
            str(path),
        ]
    )


def frame_rate(path: Path) -> str:
    """``r_frame_rate`` exactly as ffprobe reports it (a rational)."""
    proc = subprocess.run(
        [
            ffmpeg.ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip()


def audio_seconds(path: Path) -> float:
    proc = subprocess.run(
        [
            ffmpeg.ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


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
                for token in ("zoompan", "eq@", "gblur@")
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

        print("\nR6 -- bezier: campionata a 24 punti, errore misurato < 1%")
        bezier_curve = {"easing": "bezier", "points": [[0.42, 0.0], [0.58, 1.0]]}
        sampling = bridge.bezier_error(bezier_curve)
        check(
            "R6 errore massimo del campionamento < 1% del tratto",
            sampling["max_relative"] < sampling["tolerance"],
            f"max={sampling['max_relative'] * 100:.4f}% "
            f"(tolleranza {sampling['tolerance'] * 100:.1f}%, "
            f"{sampling['samples']} campioni)",
        )
        stops = bridge.bezier_samples(bezier_curve)
        node_error = max(
            abs(bridge.sampled_bezier(stops, u) - sample_curve(bezier_curve, u))
            for u, _ in stops
        )
        check(
            "R6 ai nodi la curva campionata coincide con quella esatta",
            node_error < 1e-12,
            f"errore nodi={node_error:.2e}",
        )

        bezier_spec = {
            "id": "bezier",
            "duration": SECONDS,
            "primitives": [
                {
                    "kind": "transform",
                    "params": {"scale": [1.0, 1.4]},
                    "curve": bezier_curve,
                }
            ],
        }
        bezier_out = root / "bezier" / "bezier.mp4"
        bezier_report = edl3_render.render_effect_spec(
            bezier_spec,
            source,
            bezier_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "bezier",
        )
        check(
            "R6 bezier non e' piu' rifiutata: il file esce",
            bezier_out.is_file() and bezier_out.stat().st_size > 0,
            bezier_out.stat().st_size if bezier_out.is_file() else 0,
        )
        check(
            "R6 il grafo campiona la curva come funzione del tempo",
            "if(lt(" in bezier_report["filter_script"]
            and "zoompan" in bezier_report["filter_script"],
        )
        bezier_early = frame(bezier_out, 0.1, root / "bezier_early.png")
        bezier_late = frame(bezier_out, 1.9, root / "bezier_late.png")
        check(
            "R6 la curva anima il quadro (> 8/255 fra 0.1 e 1.9 s)",
            mean_abs(bezier_early, bezier_late) > 8.0,
            f"diff={mean_abs(bezier_early, bezier_late):.3f}",
        )

        expected_scale = 1.0 + 0.4 * sample_curve(bezier_curve, 0.5)
        node_static_spec = {
            "id": "bezier_node",
            "duration": SECONDS,
            "primitives": [
                {"kind": "transform", "params": {"scale": expected_scale}}
            ],
        }
        node_static_out = root / "bezier_node" / "node.mp4"
        edl3_render.render_effect_spec(
            node_static_spec,
            source,
            node_static_out,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            duration=SECONDS,
            workdir=root / "bezier_node",
        )
        node_rendered = frame(bezier_out, 1.0, root / "bezier_node.png")
        node_expected = frame(node_static_out, 1.0, root / "bezier_node_ref.png")
        node_diff = mean_abs(node_rendered, node_expected)
        check(
            "R6 al nodo u=0.5 la resa differisce < 1% da quella attesa",
            node_diff < 255.0 * 0.01,
            f"diff={node_diff:.3f}/255 = {node_diff / 255 * 100:.3f}% "
            f"(scale attesa {expected_scale:.4f})",
        )

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

        print("\nR12 -- mask dentro il percorso timeline (stadio a piu' ingressi)")
        timeline_mask_spec = {
            "id": "mask_timeline",
            "duration": SECONDS,
            "primitives": [
                {
                    "kind": "mask",
                    "params": {
                        "shape": "ellipse",
                        "width": 1.0,
                        "height": 1.0,
                        "feather": 8.0,
                    },
                }
            ],
        }
        timeline_mask = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, HEIGHT),
            clips=[
                edl3.Clip3(
                    id="masked",
                    source=str(source),
                    src_in=0.0,
                    src_out=SECONDS,
                )
            ],
        )
        mask_planned = edl3_render.compile_timeline(
            timeline_mask, effect_specs={"masked": timeline_mask_spec}
        )
        mask_entry = mask_planned["clips"][0]["effect_spec"]
        check(
            "R12 il compilatore timeline accetta la mask",
            "maskedmerge" in mask_planned["filter_script"]
            and "geq=" in mask_planned["filter_script"],
            None,
        )
        check(
            "R12 la clip dichiara lo stadio multi-ingresso",
            isinstance(mask_entry, dict) and mask_entry.get("multi_input") is True,
            mask_entry,
        )
        timeline_mask_out = root / "timeline_mask" / "mask.mp4"
        edl3_render.render(
            timeline_mask,
            timeline_mask_out,
            workdir=root / "timeline_mask",
            effect_specs={"masked": timeline_mask_spec},
        )
        tm_early = frame(timeline_mask_out, 0.1, root / "tl_mask_a.png")
        tm_late = frame(timeline_mask_out, 1.9, root / "tl_mask_b.png")
        tm_source = frame(source, 0.1, root / "tl_mask_src.png")
        tm_delta = mean_abs(tm_early, tm_late)
        tm_corner = mean_abs(tm_source[:30, :30], tm_early[:30, :30])
        check(
            "R12 due fotogrammi della clip mascherata differiscono > 8/255",
            tm_delta > 8.0,
            f"diff={tm_delta:.3f}",
        )
        check(
            "R12 la mask taglia davvero: angoli neri (diff > 30/255)",
            tm_corner > 30.0,
            f"corner={tm_corner:.3f}",
        )

        print("\nR13 -- freeze dentro il percorso timeline")
        timeline_freeze = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, HEIGHT),
            clips=[
                edl3.Clip3(
                    id="frozen",
                    source=str(source),
                    src_in=0.0,
                    src_out=SECONDS,
                )
            ],
        )
        freeze_spec_tl = {
            "id": "freeze_timeline",
            "duration": SECONDS,
            "primitives": [
                {"kind": "time", "params": {"mode": "freeze", "at": 0.0, "hold": 2.0}}
            ],
        }
        freeze_planned = edl3_render.compile_timeline(
            timeline_freeze, effect_specs={"frozen": freeze_spec_tl}
        )
        check(
            "R13 il compilatore timeline accetta il freeze",
            "tpad=" in freeze_planned["filter_script"]
            and "trim=" in freeze_planned["filter_script"],
        )
        timeline_freeze_out = root / "timeline_freeze" / "freeze.mp4"
        edl3_render.render(
            timeline_freeze,
            timeline_freeze_out,
            workdir=root / "timeline_freeze",
            effect_specs={"frozen": freeze_spec_tl},
        )
        tl_frozen_early = frame(timeline_freeze_out, 0.2, root / "tl_frz_a.png")
        tl_frozen_late = frame(timeline_freeze_out, 1.8, root / "tl_frz_b.png")
        tl_frozen_delta = mean_abs(tl_frozen_early, tl_frozen_late)
        check(
            "R13 freeze nella timeline: 0.2 s e 1.8 s identici (diff < 1)",
            tl_frozen_delta < 1.0,
            f"diff={tl_frozen_delta:.4f}",
        )
        check(
            "R13 il sorgente invece differisce (> 8/255)",
            source_delta > 8.0,
            f"diff={source_delta:.3f}",
        )

        freeze_split_spec = {
            "id": "freeze_split",
            "duration": SECONDS,
            "primitives": [
                {"kind": "time", "params": {"mode": "freeze", "at": 0.4, "hold": 0.9}}
            ],
        }
        split_planned = edl3_render.compile_timeline(
            timeline_freeze, effect_specs={"frozen": freeze_split_spec}
        )
        split_out = root / "timeline_freeze_split" / "freeze.mp4"
        edl3_render.render(
            timeline_freeze,
            split_out,
            workdir=root / "timeline_freeze_split",
            effect_specs={"frozen": freeze_split_spec},
        )
        split_a = frame(split_out, 0.6, root / "tl_frz_split_a.png")
        split_b = frame(split_out, 1.1, root / "tl_frz_split_b.png")
        split_delta = mean_abs(split_a, split_b)
        check(
            "R13 freeze centrale: il grafo si apre in split + concat",
            "split=3" in split_planned["filter_script"]
            and "concat=n=3" in split_planned["filter_script"],
            None,
        )
        check(
            "R13 la finestra ferma e' identica (diff < 1)",
            split_delta < 1.0,
            f"diff={split_delta:.4f}",
        )

        print("\nR14 -- cio' che resta impossibile, con il suo codice")
        blend_error = None
        try:
            edl3_render.compile_timeline(
                timeline_freeze,
                effect_specs={
                    "frozen": {
                        "id": "blend_tl",
                        "duration": SECONDS,
                        "primitives": [
                            {
                                "kind": "blend",
                                "params": {"mode": "screen", "source": "bg"},
                            }
                        ],
                    }
                },
            )
        except edl3_render.CompileError as exc:
            blend_error = exc
        check(
            "R14 blend con seconda sorgente nella timeline: BLEND_INPUT_UNAVAILABLE",
            blend_error is not None
            and blend_error.code == "BLEND_INPUT_UNAVAILABLE",
            "" if blend_error is None else blend_error.code,
        )

        rotate_error = None
        try:
            edl3_render.render_effect_spec(
                {
                    "id": "rotate",
                    "duration": SECONDS,
                    "primitives": [
                        {
                            "kind": "transform",
                            "params": {"rotate": 12.0},
                            "curve": bezier_curve,
                        }
                    ],
                },
                source,
                root / "rotate" / "rotate.mp4",
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                duration=SECONDS,
            )
        except (edl3_render.CompileError, RenderSpecError) as exc:
            rotate_error = exc
        check(
            "R14 rotate resta PARAM_NOT_RENDERED, sul parametro preciso",
            rotate_error is not None
            and getattr(rotate_error, "code", "") == "PARAM_NOT_RENDERED"
            and "params.rotate" in getattr(rotate_error, "where", ""),
            "" if rotate_error is None else f"{rotate_error.code} @ {rotate_error.where}",
        )

        points_error = None
        try:
            bridge.curve_expression({"easing": "bezier"}, "T")
        except RenderSpecError as exc:
            points_error = exc
        check(
            "R14 bezier senza punti: CURVE_NOT_REPRESENTABLE su curve.points",
            points_error is not None
            and points_error.code == "CURVE_NOT_REPRESENTABLE"
            and points_error.where == "curve.points",
            "" if points_error is None else f"{points_error.code} @ {points_error.where}",
        )

        print("\nR15 -- due clock diversi e una dissolvenza: il timebase si normalizza")
        clock30 = root / "clock30.mp4"
        clock2997 = root / "clock2997.mp4"
        make_source_at(clock30, SECONDS, 30)
        make_source_at(clock2997, SECONDS, "30000/1001")
        clock_doc = edl3.Timeline3(
            fps=30,
            size=(WIDTH, HEIGHT),
            clips=[
                edl3.Clip3(id="a", source=str(clock30), src_in=0.0, src_out=SECONDS),
                edl3.Clip3(
                    id="b",
                    source=str(clock2997),
                    src_in=0.0,
                    src_out=SECONDS,
                    transition_in=edl3.Transition(
                        kind="crossfade", duration=0.5, style="fade"
                    ),
                ),
            ],
        )
        clock_plan = edl3_render.compile_timeline(clock_doc)
        check(
            "R15 ogni ingresso porta fps=30 e settb=1/30",
            clock_plan["filter_script"].count("fps=30,settb=1/30") == 2,
            [line for line in clock_plan["filter_script"].splitlines() if "settb" in line],
        )
        clock_out = root / "clock" / "master.mp4"
        clock_error = None
        try:
            edl3_render.render(clock_doc, clock_out, workdir=root / "clock")
        except edl3_render.CompileError as exc:  # pragma: no cover - the defect
            clock_error = exc
        check(
            "R15 30 fps + 29.97 + crossfade renderizza (niente 'timebase do not match')",
            clock_error is None and clock_out.is_file() and clock_out.stat().st_size > 0,
            "" if clock_error is None else str(clock_error)[:120],
        )
        check(
            "R15 la dissolvenza dura 3.5 s, non 4",
            clock_out.is_file()
            and abs(probe(clock_out)["duration"] - (2 * SECONDS - 0.5))
            <= 1.0 / 30 + 1e-6,
            probe(clock_out)["duration"] if clock_out.is_file() else "no file",
        )
        plain_doc = edl3.Timeline3(
            fps=30,
            size=(WIDTH, HEIGHT),
            clips=[edl3.Clip3(id="only", source=str(clock30), src_in=0.0, src_out=SECONDS)],
        )
        check(
            "R15 un documento senza dissolvenza resta senza normalizzazione (byte identici)",
            "settb" not in edl3_render.compile_timeline(plain_doc)["filter_script"],
        )

        print("\nR15b -- il fixture edl3_minimal renderizza com'e' scritto")
        fixture_dir = ROOT / "tests" / "fixtures" / "edl3_minimal"
        portable = json.loads((fixture_dir / "doc.json").read_text(encoding="utf-8"))
        base = root / "fixture"
        base.mkdir()
        shutil.copyfile(clock30, base / "a.mp4")
        shutil.copyfile(clock2997, base / "b.mp4")
        subprocess.run(
            [
                ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f",
                "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3.5",
                "-c:a", "aac", "-b:a", "128k", "-y", str(base / "bed.m4a"),
            ],
            check=True,
        )
        resolved = json.loads(
            json.dumps(portable).replace("<base>", str(base).replace("\\", "/"))
        )
        fixture_doc = edl3.Timeline3.from_dict(resolved)
        fixture_error = None
        try:
            fixture_report = edl3_render.render(
                fixture_doc,
                base / "master.mp4",
                workdir=base / "wd",
                audio="mix",
            )
        except edl3_render.CompileError as exc:  # pragma: no cover - the defect
            fixture_error = exc
        check(
            "R15b il fixture con audio, crossfade e clip senza dst arriva in fondo",
            fixture_error is None and (base / "master.mp4").is_file(),
            "" if fixture_error is None else str(fixture_error)[:120],
        )
        check(
            "R15b dura 3.5 s e porta il bus audio",
            fixture_error is None
            and (base / "master.mp4").is_file()
            and abs(float(fixture_report["duration"]) - 3.5) < 1e-9
            and fixture_report["audio_map"] == "[aout]"
            and abs(probe(base / "master.mp4")["duration"] - 3.5) <= 1.0 / 30 + 1e-6,
            (base / "master.mp4").stat().st_size if (base / "master.mp4").is_file() else 0,
        )

        print("\nR16 -- i difetti silenziosi: una politica di riempimento, un crop, un gate")
        # D8: the crop rect is cut from the source geometry, then the document's
        # fill policy stages it. A crop of the left-centre 32% of a 16:9 frame in
        # a 16:9 canvas must show no side band and must keep what it framed.
        d8_source = root / "d8_source.mp4"
        run(
            [
                ffmpeg.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=0x2e8b57:s=640x360:r=30:d=2,"
                "drawbox=x=280:y=140:w=80:h=80:color=white:t=fill",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-an", "-y", str(d8_source),
            ]
        )
        d8_doc = edl3.Timeline3(
            fps=FPS,
            size=(640, 360),
            clips=[
                edl3.Clip3(
                    id="c",
                    source=str(d8_source),
                    src_in=0.0,
                    src_out=1.0,
                    effects=[
                        edl3.Effect(
                            "crop",
                            {"x": 0.34, "y": 0.0, "width": 0.32, "height": 1.0},
                        )
                    ],
                )
            ],
        )
        d8_script = edl3_render.compile_timeline(d8_doc, audio="drop")["filter_script"]
        check(
            "R16/D8 il crop e' sulla geometria sorgente, prima del resize finale",
            d8_script.index("crop=w=iw*") < d8_script.index("scale=640:360"),
            [line[:110] for line in d8_script.splitlines() if line.startswith("[0:v]")],
        )
        check(
            "R16/D8 un documento con crop sceglie la politica crop (nessun pad)",
            "pad=640:360" not in d8_script
            and "force_original_aspect_ratio=increase" in d8_script,
            [line[:110] for line in d8_script.splitlines() if line.startswith("[0:v]")],
        )
        explicit_fit = edl3_render.compile_timeline(
            edl3.Timeline3(
                fps=FPS,
                size=(640, 360),
                fill="fit",
                clips=d8_doc.clips,
            ),
            audio="drop",
        )["filter_script"]
        check(
            "R16/D8 fill='fit' esplicito: la stessa clip resta nelle bande",
            "pad=640:360" in explicit_fit
            and "force_original_aspect_ratio=decrease" in explicit_fit,
            [line[:110] for line in explicit_fit.splitlines() if line.startswith("[0:v]")],
        )
        d8_out = root / "d8" / "master.mp4"
        edl3_render.render(d8_doc, d8_out, workdir=root / "d8", audio="drop")
        d8_image = frame(d8_out, 0.4, root / "d8_frame.png")
        d8_height, d8_width = d8_image.shape[:2]
        d8_left = float(d8_image[:, :8].mean())
        d8_right = float(d8_image[:, -8:].mean())
        d8_centre = float(
            d8_image[
                d8_height // 2 - 10 : d8_height // 2 + 10,
                d8_width // 2 - 10 : d8_width // 2 + 10,
            ].mean()
        )
        check(
            "R16/D8 crop 0.32: nessuna banda laterale (i bordi sono immagine)",
            d8_left > 5.0 and d8_right > 5.0,
            f"left {d8_left:.1f} right {d8_right:.1f}",
        )
        check(
            "R16/D8 crop 0.32: il centro e' conservato (riquadro bianco)",
            d8_centre > 200.0,
            f"centre {d8_centre:.1f}",
        )

        # D9: a dissolve on an absolutely placed clip is refused by render, with
        # the reason, instead of vanishing from the graph without a trace.
        placed_doc = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, HEIGHT),
            clips=[
                edl3.Clip3(
                    id="a", source=str(source), src_in=0.0, src_out=1.0,
                    dst_in=0.0, dst_out=1.0,
                ),
                edl3.Clip3(
                    id="b", source=str(source), src_in=0.0, src_out=1.0,
                    dst_in=1.0, dst_out=2.0,
                    transition_in=edl3.Transition("crossfade", 0.25),
                ),
            ],
        )
        placed_out = root / "placed" / "master.mp4"
        placed_error = None
        try:
            edl3_render.render(placed_doc, placed_out, workdir=root / "placed", audio="drop")
        except edl3_render.CompileError as exc:
            placed_error = exc
        check(
            "R16/D9 una crossfade su clip posizionata e' rifiutata, non ignorata",
            placed_error is not None
            and placed_error.code == "TRANSITION_DST"
            and "dst_in" in placed_error.detail
            and not placed_out.exists(),
            "" if placed_error is None else f"{placed_error.code}: {placed_error.detail[:80]}",
        )

        # D7: one fill policy for the whole document. A landscape clip in a
        # portrait canvas must be staged the same way whether or not it carries a
        # declarative filter: black bands on both frames, or on neither.
        def mixed_document(policy: str) -> edl3.Timeline3:
            return edl3.Timeline3(
                fps=FPS,
                size=(WIDTH, 568),
                fill=policy,
                clips=[
                    edl3.Clip3(id="plain", source=str(source), src_in=0.0, src_out=1.0),
                    edl3.Clip3(
                        id="fx",
                        source=str(source),
                        src_in=0.0,
                        src_out=1.0,
                        effects=[edl3.Effect("vignette", {"angle": 0.6})],
                    ),
                ],
            )

        def border_means(path: Path, at: float, label: str) -> tuple:
            image = frame(path, at, root / f"d7_{label}.png")
            return float(image[:8].mean()), float(image[-8:].mean())

        fit_out = root / "d7_fit" / "master.mp4"
        edl3_render.render(
            mixed_document("fit"), fit_out, workdir=root / "d7_fit", audio="drop"
        )
        fit_plain = border_means(fit_out, 0.4, "fit_plain")
        fit_fx = border_means(fit_out, 1.4, "fit_fx")
        check(
            "R16/D7 fill=fit: entrambe le clip hanno le bande (stessa politica)",
            all(value < 5.0 for value in fit_plain + fit_fx),
            f"plain {fit_plain[0]:.1f}/{fit_plain[1]:.1f} "
            f"fx {fit_fx[0]:.1f}/{fit_fx[1]:.1f}",
        )
        crop_out = root / "d7_crop" / "master.mp4"
        edl3_render.render(
            mixed_document("crop"), crop_out, workdir=root / "d7_crop", audio="drop"
        )
        crop_plain = border_means(crop_out, 0.4, "crop_plain")
        crop_fx = border_means(crop_out, 1.4, "crop_fx")
        check(
            "R16/D7 fill=crop: nessuna clip porta bande (stessa politica)",
            all(value > 5.0 for value in crop_plain + crop_fx),
            f"plain {crop_plain[0]:.1f}/{crop_plain[1]:.1f} "
            f"fx {crop_fx[0]:.1f}/{crop_fx[1]:.1f}",
        )

        # D20: two -14 LUFS masters summed by amix land far above the spec. The
        # render must report a master that is not deliverable, with the numbers.
        gate_bed = root / "gate_bed.m4a"
        audio_bed(gate_bed, 2.0)
        hot_doc = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, 568),
            clips=[edl3.Clip3(id="picture", source=str(source), src_in=0.0, src_out=2.0)],
            audio=[
                edl3.AudioTrack3(
                    role="dialogue",
                    clips=[
                        edl3.AudioClip3(
                            source=str(gate_bed), src_in=0.0, src_out=2.0,
                            dst_in=0.0, dst_out=2.0,
                        )
                    ],
                ),
                edl3.AudioTrack3(
                    role="music",
                    clips=[
                        edl3.AudioClip3(
                            source=str(gate_bed), src_in=0.0, src_out=2.0,
                            dst_in=0.0, dst_out=2.0,
                        )
                    ],
                ),
            ],
        )
        hot_report = edl3_render.render(
            hot_doc, root / "gate" / "master.mp4", workdir=root / "gate", audio="mix"
        )
        hot_gate = hot_report["loudness"]
        hot_lufs = float(hot_gate["measured"]["lufs"] or 0.0)
        check(
            "R16/D20 un master somma (-8 LUFS) e' bocciato dal gate",
            hot_gate["ok"] is False
            and hot_report["deliverable"] is False
            and -12.0 < hot_lufs < -5.0,
            f"{hot_lufs:.2f} LUFS ok={hot_gate['ok']}",
        )
        check(
            "R16/D20 il report dice i valori misurati, non 'ok'",
            "measured" in hot_gate["detail"]
            and f"{hot_lufs:.2f}" in hot_gate["detail"]
            and any("loudness gate failed" in item for item in hot_report["warnings"]),
            hot_gate["detail"][:120],
        )
        check(
            "R16/D20 il gate accetta -14 +/- 1 e il vero picco sotto -1",
            edl3_render.loudness_gate(-14.2, -1.5)["ok"] is True
            and edl3_render.loudness_gate(-16.1, -1.5)["ok"] is False
            and edl3_render.loudness_gate(-14.0, -0.4)["ok"] is False,
            edl3_render.loudness_gate(-14.0, -0.4)["detail"][:90],
        )

        # D21: a continuous music bed under a document with two dissolves. The
        # bed follows the shortened timeline (one cut per join) instead of being
        # refused, and the mix still ends with the picture.
        bed = root / "join_bed.m4a"
        audio_bed(bed, 6.0)
        join_doc = edl3.Timeline3(
            fps=FPS,
            size=(WIDTH, 240),
            clips=[
                edl3.Clip3(id="a", source=str(source), src_in=0.0, src_out=2.0),
                edl3.Clip3(
                    id="b", source=str(source), src_in=0.0, src_out=2.0,
                    transition_in=edl3.Transition("crossfade", 0.5),
                ),
                edl3.Clip3(
                    id="c", source=str(source), src_in=0.0, src_out=2.0,
                    transition_in=edl3.Transition("crossfade", 0.5),
                ),
            ],
            audio=[
                edl3.AudioTrack3(
                    role="music",
                    clips=[
                        edl3.AudioClip3(
                            source=str(bed), src_in=0.0, src_out=6.0,
                            dst_in=0.0, dst_out=6.0,
                        )
                    ],
                )
            ],
        )
        join_out = root / "joins" / "master.mp4"
        join_error = None
        try:
            join_report = edl3_render.render(
                join_doc, join_out, workdir=root / "joins", audio="mix"
            )
        except edl3_render.CompileError as exc:  # pragma: no cover - the defect
            join_error = exc
            join_report = {}
        frame_seconds = 1.0 / FPS
        check(
            "R16/D21 music bed + 2 join: il render non rifiuta il bed",
            join_error is None and join_out.is_file(),
            "" if join_error is None else str(join_error)[:120],
        )
        check(
            "R16/D21 durata audio = durata video (5.0 s, entro un frame)",
            join_error is None
            and abs(probe(join_out)["duration"] - 5.0) <= frame_seconds + 1e-6
            and abs(audio_seconds(join_out) - 5.0) <= frame_seconds + 1e-3,
            f"video {probe(join_out)['duration']:.3f}s audio "
            f"{audio_seconds(join_out):.3f}s",
        )
        check(
            "R16/D21 il mix resta nel target di loudness",
            join_error is None and join_report.get("loudness", {}).get("ok") is True,
            join_report.get("loudness", {}).get("measured"),
        )

        # D6: a declared 29.97 is the exact NTSC rational, not 29 or 2997/100.
        ntsc_doc = edl3.Timeline3(
            fps=29.97,
            size=(WIDTH, HEIGHT),
            clips=[edl3.Clip3(id="n", source=str(source), src_in=0.0, src_out=1.0)],
        )
        ntsc_plan = edl3_render.compile_timeline(ntsc_doc, audio="drop")
        ntsc_out = root / "ntsc" / "master.mp4"
        ntsc_report = edl3_render.render(
            ntsc_doc, ntsc_out, workdir=root / "ntsc", audio="drop"
        )
        check(
            "R16/D6 il piano dichiara 30000/1001",
            ntsc_plan["fps_text"] == "30000/1001"
            and ntsc_report["fps_text"] == "30000/1001",
            (ntsc_plan["fps_text"], ntsc_report["fps_text"]),
        )
        check(
            "R16/D6 ffprobe r_frame_rate = 30000/1001",
            frame_rate(ntsc_out) == "30000/1001",
            frame_rate(ntsc_out),
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
