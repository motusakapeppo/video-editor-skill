"""vx.gates — cancelli invalicabili: le regole stanno nel codice, non nella disciplina.

Tre cancelli, in ordine di esecuzione:

  pre_render   l'aritmetica del piano deve chiudersi prima di toccare il media
  graph        il filtergraph deve tagliare picture e sound nella stessa operazione
  post_render  il file deve dimostrare durata, sincronia e decodifica, o va in scarto

Il cancello 3 non "consiglia": sposta il file in `scarto/` e solleva `QCFailure`
(codice di uscita 5). Un file difettoso non puo' raggiungere la delivery.

Ogni controllo dichiara se e' stato eseguito. Un controllo non applicabile viene
riportato come `skipped` con il motivo: non e' un passaggio silenzioso.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from . import layout as layout_mod  # noqa: PLC0415
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import contract
from . import ffmpeg as ff

DURATION_TOLERANCE_MS = 40.0
FRAME_TOLERANCE = 1


@dataclass
class Check:
    code: str
    status: str  # pass | fail | skipped
    value: Any = None
    limit: Any = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "value": self.value,
            "limit": self.limit,
            "detail": self.detail,
        }


@dataclass
class GateResult:
    gate: str
    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    @property
    def failures(self) -> List[str]:
        return [c.code for c in self.checks if c.status == "fail"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "ok": self.ok,
            "failures": self.failures,
            "checks": [c.to_dict() for c in self.checks],
        }


def _probe(path: Path) -> Dict[str, Any]:
    out = subprocess.run(
        [
            ff.ffprobe(),
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return json.loads(out.stdout or "{}")


# --------------------------------------------------------------------------- #
# Gate 1 — aritmetica del piano
# --------------------------------------------------------------------------- #


def pre_render(plan, media: Dict[str, Any]) -> GateResult:
    """The plan must add up before a single frame is rendered."""
    result = GateResult("pre_render")
    cuts = list(getattr(plan, "cuts", None) or [])
    fps = float(media.get("fps") or 0.0)
    duration = float(media.get("duration") or 0.0)

    if duration <= 0:
        result.checks.append(
            Check(
                "source_duration",
                "fail",
                duration,
                "> 0",
                "probe gave no duration: nothing can be validated",
            )
        )
        return result

    if not cuts:
        result.checks.append(
            Check(
                "plan_arithmetic",
                "skipped",
                0,
                "n/a",
                "no cuts: the plan keeps the whole source",
            )
        )
    else:
        bad = [c for c in cuts if float(c.get("out", 0)) <= float(c.get("in", 0))]
        result.checks.append(
            Check(
                "cut_order",
                "fail" if bad else "pass",
                len(bad),
                0,
                f"{len(bad)} ranges with out<=in" if bad else "",
            )
        )
        outside = [
            c
            for c in cuts
            if float(c.get("in", 0)) < 0 or float(c.get("out", 0)) > duration + 1e-6
        ]
        result.checks.append(
            Check(
                "cuts_inside_source",
                "fail" if outside else "pass",
                len(outside),
                0,
                f"{len(outside)} ranges outside 0..{duration:.3f}" if outside else "",
            )
        )
        total = round(
            sum(float(c.get("out", 0)) - float(c.get("in", 0)) for c in cuts), 3
        )
        result.checks.append(
            Check(
                "planned_total",
                "pass",
                total,
                f"> 0 and <= {duration:.3f}",
                f"{total:.3f}s planned",
            )
        )
        if total <= 0 or total > duration + 1e-6:
            result.checks[-1].status = "fail"
        if fps > 0:
            frames = int(round(total * fps))
            result.checks.append(
                Check("planned_frames", "pass", frames, "> 0", f"at {fps:.3f} fps")
            )

    if not result.ok:
        raise contract.QCFailure(result.to_dict())
    return result


# --------------------------------------------------------------------------- #
# Gate 2 — un solo passaggio per picture e sound
# --------------------------------------------------------------------------- #

_VIDEO_CUT = re.compile(r"\[(\d+):v\]\s*trim|trim=[^;]*\[\s*v\d*\s*\]")
_AUDIO_CUT = re.compile(r"\[(\d+):a\]\s*atrim|atrim=[^;]*\[\s*a\d*\s*\]")
_SINGLE_CONCAT = re.compile(r"concat\s*=\s*n\s*=\s*\d+\s*:\s*v\s*=\s*1\s*:\s*a\s*=\s*1")


def graph(graph_text: str) -> GateResult:
    """Static gate: picture and sound must be trimmed and concatenated together."""
    result = GateResult("graph")
    text = graph_text or ""
    has_v = bool(_VIDEO_CUT.search(text))
    has_a = bool(_AUDIO_CUT.search(text))
    joined = bool(_SINGLE_CONCAT.search(text))
    result.checks.append(
        Check(
            "video_trim_present",
            "pass" if has_v else "fail",
            has_v,
            True,
            "no video trim found" if not has_v else "",
        )
    )
    result.checks.append(
        Check(
            "audio_trim_present",
            "pass" if has_a else "fail",
            has_a,
            True,
            "no audio trim found" if not has_a else "",
        )
    )
    result.checks.append(
        Check(
            "single_concat_v1a1",
            "pass" if joined else "fail",
            joined,
            True,
            "concat must be n=N:v=1:a=1 so both streams advance together"
            if not joined
            else "",
        )
    )
    if not result.ok:
        raise contract.QCFailure(result.to_dict())
    return result


# --------------------------------------------------------------------------- #
# Gate 3 — il file deve dimostrare di essere integro
# --------------------------------------------------------------------------- #


def _stream(data: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    return next(
        (s for s in data.get("streams", []) if s.get("codec_type") == kind), None
    )


def post_render(
    plan,
    media: Dict[str, Any],
    output: str | Path,
    *,
    workdir: str | Path,
    planned_total: Optional[float] = None,
    expected_size: Optional[tuple[int, int]] = None,
) -> GateResult:
    """Validate the delivered file; quarantine it and raise if it is not usable."""
    result = GateResult("post_render")
    path = Path(output)

    if not path.is_file():
        result.checks.append(Check("file_exists", "fail", False, True, str(path)))
        raise contract.QCFailure(result.to_dict())

    decode = ff.run_ff(
        ["-v", "error", "-i", str(path), "-f", "null", "-"],
        loglevel="error",
        timeout=1800,
    )
    result.checks.append(
        Check(
            "decodable",
            "pass" if decode.ok and not decode.stderr.strip() else "fail",
            decode.ok,
            True,
            decode.stderr.strip()[:200],
        )
    )

    data = _probe(path)
    video = _stream(data, "video")
    audio = _stream(data, "audio")

    if video is None:
        result.checks.append(
            Check("video_stream", "fail", False, True, "no video stream")
        )
    else:
        result.checks.append(Check("video_stream", "pass", True, True, ""))
        if expected_size is not None:
            got = (int(video.get("width") or 0), int(video.get("height") or 0))
            wanted = (int(expected_size[0]), int(expected_size[1]))
            result.checks.append(
                Check(
                    "output_size",
                    "pass" if got == wanted else "fail",
                    f"{got[0]}x{got[1]}",
                    f"{wanted[0]}x{wanted[1]}",
                    ""
                    if got == wanted
                    else "geometry differs from the declared target",
                )
            )

    v_dur = float(
        (video or {}).get("duration") or data.get("format", {}).get("duration") or 0.0
    )
    a_dur = float((audio or {}).get("duration") or 0.0)

    if audio is None:
        result.checks.append(
            Check(
                "audio_stream",
                "skipped",
                False,
                True,
                "silent delivery: synchrony not applicable",
            )
        )
    else:
        delta_ms = abs(v_dur - a_dur) * 1000.0
        result.checks.append(
            Check(
                "av_duration_delta_ms",
                "pass" if delta_ms <= DURATION_TOLERANCE_MS else "fail",
                round(delta_ms, 1),
                DURATION_TOLERANCE_MS,
                f"video {v_dur:.3f}s vs audio {a_dur:.3f}s",
            )
        )
        v_start = float(video.get("start_time") or 0.0)
        a_start = float(audio.get("start_time") or 0.0)
        start_delta = abs(v_start - a_start) * 1000.0
        result.checks.append(
            Check(
                "av_start_delta_ms",
                "pass" if start_delta <= DURATION_TOLERANCE_MS else "fail",
                round(start_delta, 1),
                DURATION_TOLERANCE_MS,
                "",
            )
        )

    fps = float(media.get("fps") or 0.0)
    frames_raw = (video or {}).get("nb_read_frames")

    if planned_total is None:
        result.checks.append(
            Check(
                "planned_vs_rendered",
                "skipped",
                round(v_dur, 3),
                "n/a",
                "the plan does not carry an output duration: pass it explicitly",
            )
        )
    else:
        delta_ms = abs(v_dur - float(planned_total)) * 1000.0
        result.checks.append(
            Check(
                "planned_vs_rendered",
                "pass" if delta_ms <= DURATION_TOLERANCE_MS else "fail",
                round(delta_ms, 1),
                DURATION_TOLERANCE_MS,
                f"planned {float(planned_total):.3f}s vs rendered {v_dur:.3f}s",
            )
        )
        if fps > 0 and frames_raw not in (None, "N/A"):
            expected = int(round(float(planned_total) * fps))
            got = int(frames_raw)
            result.checks.append(
                Check(
                    "frame_count_delta",
                    "pass" if abs(got - expected) <= FRAME_TOLERANCE else "fail",
                    got - expected,
                    FRAME_TOLERANCE,
                    f"{got} frames vs {expected} expected",
                )
            )

    if not result.ok:
        reject = Path(workdir) / "scarto"
        reject.mkdir(parents=True, exist_ok=True)
        target = reject / f"DIFETTOSO_{path.name}"
        try:
            shutil.move(str(path), str(target))
        except OSError:
            target = path
        (reject / f"{path.stem}.gate.json").write_text(
            json.dumps(result.to_dict(), indent=2), encoding="utf-8"
        )
        result.checks.append(
            Check(
                "quarantined",
                "fail",
                str(target),
                "delivery",
                "file moved out of delivery",
            )
        )
        raise contract.QCFailure(result.to_dict())
    return result


class LayoutFailure(contract.QCFailure):
    """Gate 4 rifiuta il rendering: uscita 6, `contract.EXIT_RENDER_FAIL`.

    Distinta da `QCFailure` perche' i cancelli 1 e 3 mappano su 5 (`EXIT_QC_FAIL`):
    un file gia' scritto e' un'altra cosa da un rendering che non deve partire.
    """

    exit_code = contract.EXIT_RENDER_FAIL


# --------------------------------------------------------------------------- #
# Gate 4 — layout: testo contro grafica, prima di ffmpeg
# --------------------------------------------------------------------------- #


def layout(lay, text_box, *, crop_box=None, require_graphic: bool = True) -> GateResult:
    """Static gate: the declared text box must not touch the declared graphic box.

    Runs before the filtergraph reaches ffmpeg, on *declarations* only, so a
    collision costs a millisecond instead of a full render. Raises `LayoutFailure`,
    which carries `exit_code = EXIT_RENDER_FAIL` (6): the render is refused.

    `require_graphic` is the anti-silent-pass rule. If overlays were declared but
    none could be measured (missing alpha, OpenCV absent), `graphic` is None and an
    overlap check would pass vacuously. No measurement means no evidence, so the
    gate refuses instead of waving the render through.
    """
    result = GateResult("layout")
    canvas = lay.canvas

    graphic = getattr(lay, "graphic", None)
    declared = int(getattr(lay, "declared", 0) or 0)
    measured = int(getattr(lay, "measured", 0) or 0)

    if require_graphic and declared > 0 and measured == 0:
        result.checks.append(
            Check(
                "graphic_measured",
                "fail",
                measured,
                f"> 0 su {declared}",
                "overlay dichiarati ma nessuno misurabile: la fascia non e' nota, "
                "il rendering non parte",
            )
        )
        raise LayoutFailure(result.to_dict())

    if graphic is None and declared > 0:
        result.checks.append(
            Check(
                "graphic_measured",
                "fail" if require_graphic else "skipped",
                measured,
                f"> 0 su {declared}",
                "nessuna componente nella fascia inferiore",
            )
        )
        if require_graphic:
            raise LayoutFailure(result.to_dict())

    overlap = graphic.intersection(text_box) if graphic is not None else None
    result.checks.append(
        Check(
            "text_vs_graphic",
            "fail" if overlap else "pass",
            overlap.to_dict() if overlap else 0,
            0,
            f"testo e grafica si sovrappongono per {overlap.area} px"
            if overlap
            else "nessuna sovrapposizione",
        )
    )

    gap = int(lay.text_limit_y) - int(text_box.bottom)
    result.checks.append(
        Check(
            "text_clearance_px",
            "pass" if gap >= 0 else "fail",
            gap,
            ">= 0",
            f"limite {lay.text_limit_y}, fondo testo {text_box.bottom}",
        )
    )

    inside = canvas.contains(text_box)
    result.checks.append(
        Check(
            "text_inside_canvas",
            "pass" if inside else "fail",
            text_box.to_dict(),
            canvas.to_dict(),
            "" if inside else "il testo dichiarato esce dal canvas",
        )
    )

    if crop_box is not None:
        room = max(0, int(getattr(lay, "canvas", canvas).w) - int(crop_box.w))
        result.checks.append(
            Check(
                "crop_pan_room_px",
                "skipped",
                room,
                "n/a",
                "spazio di pan orizzontale disponibile nel crop dichiarato",
            )
        )

    if not result.ok:
        raise LayoutFailure(result.to_dict())
    return result


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #


def _selftest() -> int:
    failures = 0

    def expect(label: str, fn, should_raise: bool) -> None:
        nonlocal failures
        try:
            fn()
            raised = False
        except contract.QCFailure:
            raised = True
        ok = raised == should_raise
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    plan_ok = contract.EditPlan(
        cuts=[{"in": 0.0, "out": 4.0}, {"in": 6.0, "out": 10.0}]
    )
    plan_bad = contract.EditPlan(cuts=[{"in": 4.0, "out": 2.0}])
    plan_out = contract.EditPlan(cuts=[{"in": 0.0, "out": 999.0}])
    media = {"fps": 25.0, "duration": 12.0, "has_audio": True}

    print("Gate 1 — aritmetica del piano")
    expect("piano coerente passa", lambda: pre_render(plan_ok, media), False)
    expect("cut con out<=in rifiutato", lambda: pre_render(plan_bad, media), True)
    expect("cut fuori durata rifiutato", lambda: pre_render(plan_out, media), True)

    print("Gate 2 — filtergraph")
    good = "[0:v]trim=0:4,setpts=PTS-STARTPTS[v0];[0:a]atrim=0:4,asetpts=PTS-STARTPTS[a0];[v0][a0]concat=n=1:v=1:a=1[v][a]"
    bad = "[0:v]trim=0:4,setpts=PTS-STARTPTS[v0];[v0]scale=1080:-2[v]"
    expect("grafo con trim video+audio e concat v1a1", lambda: graph(good), False)
    expect("grafo senza audio rifiutato", lambda: graph(bad), True)

    print("Gate 3 — file finale")
    good_file = Path(
        r"D:\VOX POPULI - ALBUM\SOCIAL\PUBBLICAZIONI\P_25-10-26\video\motus_vox_v3_3x4.mp4"
    )
    bad_file = Path(r"D:\video_work\vox\stress\DIFETTOSO_motus_vox_v2_3x4_subs.mp4")
    tmp = Path(r"D:\video_work\_gates")
    tmp.mkdir(parents=True, exist_ok=True)
    if good_file.is_file():
        res = post_render(
            plan_ok,
            {"fps": 25.0, "duration": 22.84},
            good_file,
            workdir=tmp,
            planned_total=22.84,
        )
        print(
            f"  [{'PASS' if res.ok else 'FAIL'}] v3 passa tutti i controlli ({len(res.checks)})"
        )
        failures += 0 if res.ok else 1
    if bad_file.is_file():
        copy = tmp / "v2_difettoso.mp4"
        shutil.copy2(bad_file, copy)
        expect(
            "v2 desincronizzato rifiutato e messo in scarto",
            lambda: post_render(
                plan_ok,
                {"fps": 25.0, "duration": 22.83},
                copy,
                workdir=tmp,
                planned_total=22.83,
            ),
            True,
        )
        failed = list((tmp / "scarto").glob("DIFETTOSO_*"))
        print(f"  [{'PASS' if failed else 'FAIL'}] file in scarto: {len(failed)}")
        failures += 0 if failed else 1

    print(f"\n{'TUTTO OK' if failures == 0 else str(failures) + ' FALLITI'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)


# --------------------------------------------------------------------------- #
# Gate 5 — color audit: il grade del nuovo motore non tradisce quello certificato
# --------------------------------------------------------------------------- #

COLOR_SAMPLES = 12
# Tolleranze sul confronto a parita' di sorgente. Sono strette di proposito: il
# grade lo fa lo stesso codice, quindi una differenza misurabile e' un errore, non
# una sfumatura. Misurato: 0.000 fra un file e se stesso.
COLOR_TOLERANCE = {"luma_p01": 0.010, "luma_p50": 0.020, "luma_p99": 0.030,
                   "mid_sat": 0.050, "hi_sat": 0.050}


def _frame_stats(video: str | Path, at: float, region: tuple[float, float],
                 inset: float = 0.0) -> dict:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise contract.QCFailure({"gate": "color", "failures": [f"non apribile: {video}"]})
    cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise contract.QCFailure({"gate": "color", "failures": [f"frame non leggibile a {at}"]})
    h, w = frame.shape[0], frame.shape[1]
    y0, y1 = int(h * region[0]), int(h * region[1])
    x0, x1 = int(w * inset), int(w * (1.0 - inset))
    rgb = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    hi = rgb.max(axis=2)
    lo = rgb.min(axis=2)
    sat = np.where(hi > 1e-6, (hi - lo) / np.maximum(hi, 1e-6), 0.0)
    mid = (luma > 0.35) & (luma < 0.65)
    bright = luma > 0.72
    return {
        "luma_p01": float(np.percentile(luma, 1)),
        "luma_p50": float(np.percentile(luma, 50)),
        "luma_p99": float(np.percentile(luma, 99)),
        "mid_sat": float(sat[mid].mean()) if mid.any() else 0.0,
        "hi_sat": float(sat[bright].mean()) if bright.any() else 0.0,
    }


def color(
    candidate: str | Path,
    reference: str | Path,
    *,
    samples: int = COLOR_SAMPLES,
    region: tuple[float, float] = (0.0, 0.62),
    inset: float = 0.0,
    tolerance: dict | None = None,
) -> GateResult:
    """Gate 5: il grade del file nuovo deve combaciare con quello certificato.

    Non confronta i pixel (la grafica puo' differire), confronta la DISTRIBUZIONE
    di luma e saturazione nella zona di puro grade — sopra il blocco testo. E' il
    cancello che rende sicuro spostare il duotone dentro il motore di compositing:
    senza, un errore di colore passerebbe silenzioso.
    """
    tol = dict(COLOR_TOLERANCE)
    if tolerance:
        tol.update({k: float(v) for k, v in tolerance.items()})
    result = GateResult("color")
    from . import motion as motion_mod  # noqa: PLC0415

    frames, dur, _fps = motion_mod.expected_frames(reference)
    if dur <= 0:
        raise contract.QCFailure({"gate": "color", "failures": ["durata non leggibile"]})
    times = [dur * i / max(1, samples - 1) * 0.999 for i in range(samples)]

    worst = {k: 0.0 for k in tol}
    used = 0
    for at in times:
        # Un campione oltre l'ultimo frame decodificabile non e' un errore di
        # colore: si salta. Se ne restano troppo pochi, il gate rifiuta comunque.
        try:
            ref = _frame_stats(reference, at, region, inset)
            cand = _frame_stats(candidate, at, region, inset)
        except contract.QCFailure:
            continue
        used += 1
        for key in tol:
            worst[key] = max(worst[key], abs(cand[key] - ref[key]))
    if used < max(2, samples // 2):
        raise contract.QCFailure({
            "gate": "color",
            "failures": [f"solo {used} campioni validi su {samples}: misura non affidabile"],
        })

    for key, limit in tol.items():
        delta = worst[key]
        result.checks.append(
            Check(
                key,
                "pass" if delta <= limit else "fail",
                round(delta, 4),
                f"<= {limit}",
                f"scarto massimo su {used} campioni validi nella fascia {region} inset {inset}",
            )
        )
    if not result.ok:
        raise contract.QCFailure(result.to_dict())
    return result


def layout_elements(lay, boxes, *, text_box=None) -> GateResult:
    """Gate 4 universale: nessun elemento grafico invade un altro.

    `boxes` e' un dict nome -> (x, y, w, h) dei box MASSIMI sull'animazione. I
    contenitori (cornice) non entrano: il loro bbox e' il canvas, quindi
    colliderebbero con tutto. Si controllano i badge fra loro e contro il testo.
    """
    result = GateResult("layout_elements")
    items = {k: (v if isinstance(v, layout_mod.Rect) else layout_mod.Rect(*v))
             for k, v in (boxes or {}).items()}
    if text_box is not None and not isinstance(text_box, layout_mod.Rect):
        text_box = layout_mod.Rect(*text_box)
    if text_box is not None:
        items["__text__"] = text_box

    names = sorted(items)
    collisions = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            inter = items[a].intersection(items[b])
            if inter is not None:
                collisions.append({"a": a, "b": b, "area": inter.area})

    result.checks.append(
        Check(
            "element_collisions",
            "fail" if collisions else "pass",
            len(collisions),
            0,
            "; ".join(f"{c['a']}x{c['b']} {c['area']}px" for c in collisions[:4])
            or "nessuna sovrapposizione fra elementi",
        )
    )
    outside = [k for k, r in items.items()
               if not lay.canvas.contains(r)]
    result.checks.append(
        Check(
            "elements_in_canvas",
            "fail" if outside else "pass",
            outside or 0,
            0,
            "elementi fuori dal canvas" if outside else "tutti dentro il canvas",
        )
    )
    if not result.ok:
        raise LayoutFailure(result.to_dict())
    return result


def calibrate(node, video, *, at: float = 0.0, tolerance: float = 0.025,
              workdir=None) -> GateResult:
    """Gate 5 dinamico: le due implementazioni dello stesso nodo devono combaciare.

    Non confronta un file con un master statico: prende UN frame, applica il nodo
    con FFmpeg e con Pillow, e misura lo scarto di luma ai livelli di ingresso. Se
    divergono oltre la tolleranza, il motore e' respinto PRIMA del render completo.

    Vale per qualunque nodo e per qualunque risoluzione: la mappatura e' per-pixel,
    quindi non contiene coordinate.
    """
    import subprocess  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    from . import ffmpeg as ff  # noqa: PLC0415
    from . import grade as grade_mod  # noqa: PLC0415

    work = Path(workdir) if workdir else Path(".")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "cal_frame.png"
    proc = subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.3f}",
         "-i", str(video), "-frames:v", "1", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not src.exists():
        raise contract.QCFailure({"gate": "color_calibration",
                                  "failures": [f"frame non estraibile a {at}: {proc.stderr[-200:]}"]})

    via_ff = work / "cal_ff.png"
    proc = subprocess.run(
        [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vf", grade_mod.ffmpeg_filter(node), "-frames:v", "1", str(via_ff)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise contract.QCFailure({"gate": "color_calibration",
                                  "failures": [f"filtro non applicabile: {proc.stderr[-200:]}"]})

    img_ff = Image.open(via_ff).convert("RGB")
    img_pl = grade_mod.apply_pillow(Image.open(src), node)

    import numpy as np  # noqa: PLC0415

    def stats(im):
        rgb = np.asarray(im, dtype=np.float32) / 255.0
        y = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
        hi, lo = rgb.max(axis=2), rgb.min(axis=2)
        sat = np.where(hi > 1e-6, (hi - lo) / np.maximum(hi, 1e-6), 0.0)
        mid = (y > 0.35) & (y < 0.65)
        return {
            "p01": float(np.percentile(y, 1)),
            "p50": float(np.percentile(y, 50)),
            "p99": float(np.percentile(y, 99)),
            "mid_sat": float(sat[mid].mean()) if mid.any() else 0.0,
        }

    a, b = stats(img_ff), stats(img_pl)
    result = GateResult("color_calibration")
    worst = 0.0
    for key in ("p01", "p50", "p99", "mid_sat"):
        delta = abs(a[key] - b[key])
        if key.startswith("p"):
            worst = max(worst, delta)
        limit = tolerance if key.startswith("p") else max(tolerance, 0.05)
        result.checks.append(
            Check(key, "pass" if delta <= limit else "fail", round(delta, 4),
                  f"<= {limit}", f"ffmpeg {a[key]:.4f} vs pillow {b[key]:.4f}")
        )
    result.checks.append(
        Check("max_dl", "pass" if worst <= tolerance else "fail",
              round(worst, 4), f"<= {tolerance}", "scarto massimo di luma")
    )
    if not result.ok:
        raise contract.QCFailure(result.to_dict())
    return result
