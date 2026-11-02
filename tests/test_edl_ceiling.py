"""EDL true-peak ceiling: peak-aware per-clip gain plus a declared delivery limiter.

The bridge used to raise a quiet clip to the loudness target without looking at
its peaks, and loudnorm in linear mode does not limit them: a master could pass
``master_lufs`` and fail ``master_true_peak``. The gain is now clamped to the
clip's own peak headroom, and the renderer places a true-peak limiter after
loudnorm, under the same ceiling ``qc.audit_master`` measures.

python tests/test_edl_ceiling.py
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, qc, render  # noqa: E402
from vx.contract import THRESHOLDS  # noqa: E402
from vx.edl_plan import (  # noqa: E402
    ClipSegment,
    DuckingSpec,
    EDLPlan,
    SourceRef,
    Track,
)

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"

# Graph of the fixture below at HEAD 9b8ec2c: the new keyword must not move a
# single byte when it is absent.
# Aggiornato il 2/11/2026: `aformat` precede `apad` sul bus audio. Il grafo cambia
# di proposito (senza il formato dichiarato, `apad` non allunga dopo un `amix`,
# misurato 4,019 s su 6), e il golden lo registra invece di subirlo.
GOLDEN_SHA256 = "0e9bc9be24ea993580b1c1f0eb69d0f929af19155907e2896a6d23bcb048a57f"
CEILING = -0.9
HEIGHT = 568


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _graph_plan(*, dialogue_gain: float = 18.0) -> EDLPlan:
    sources = {
        "src": SourceRef("a.mp4", "a" * 64, 2.0),
        "bed": SourceRef("b.mp4", "b" * 64, 2.0),
    }
    video = ClipSegment("v0", "src", 0.0, 2.0, 0.0, 2.0, 0, "main_talking_head")
    dialogue = ClipSegment(
        "d0", "src", 0.0, 2.0, 0.0, 2.0, 0, "dialogue", gain_db=dialogue_gain
    )
    music = ClipSegment("m0", "bed", 0.0, 2.0, 0.0, 2.0, 0, "music")
    return EDLPlan(
        fps=30.0,
        duration_sec=2.0,
        aspect_ratio="16:9",
        resolution=(320, 180),
        sources=sources,
        video_tracks=(Track(0, (video,), "main"),),
        audio_tracks=(Track(0, (dialogue,), "dialogue"), Track(1, (music,), "music")),
        ducking=DuckingSpec(),
    )


def _graph(**kwargs) -> str:
    return render.build_edl_filtergraph(
        _graph_plan(), {"src": 0, "bed": 1}, has_audio=True, **kwargs
    )


def _gain_plan() -> EDLPlan:
    sources = {"s": SourceRef("a.mp4", "a" * 64, 2.0)}
    video = ClipSegment("v", "s", 0.0, 2.0, 0.0, 2.0, 0, "main_talking_head")
    dialogue = ClipSegment("d", "s", 0.0, 2.0, 0.0, 2.0, 0, "dialogue")
    return EDLPlan(
        fps=30.0,
        duration_sec=2.0,
        aspect_ratio="9:16",
        resolution=(320, HEIGHT),
        sources=sources,
        video_tracks=(Track(0, (video,), "main"),),
        audio_tracks=(Track(0, (dialogue,), "dialogue"),),
    )


def _measure(input_i: float, input_tp: float | None):
    def measure(path, spans):
        out = {"input_i": input_i, "input_lra": 1.0}
        if input_tp is not None:
            out["input_tp"] = input_tp
        return out

    return measure


def _gain(input_i: float, input_tp: float | None, target: float = -14.0) -> tuple:
    plan, report = bridge.build(
        _gain_plan(),
        {"s": {"speech_regions": [{"start": 0.0, "end": 2.0}]}},
        target_lufs=target,
        loudness_spans=_measure(input_i, input_tp),
    )
    clip = list(plan.audio_tracks[0].clips)[0]
    entry = [item for item in report.audio if "gain_db" in item][0]
    declared = [
        item
        for item in report.not_applied
        if item.get("reason") == "gain_limited_by_peak"
    ]
    unknown = [
        item
        for item in report.not_applied
        if item.get("reason") == "peak_headroom_unknown"
    ]
    return clip.gain_db, entry, declared, unknown


def _make_source(path: Path) -> bool:
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x{HEIGHT}:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=pink:amplitude=0.45:duration=2:sample_rate=48000:seed=7",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file()


def _render_to_wav(source: Path, graph: str, target: Path) -> bool:
    script = target.with_suffix(".txt")
    script.write_text(graph, encoding="utf-8")
    proc = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-filter_complex_script",
            str(script),
            "-map",
            "[aout]",
            "-c:a",
            "pcm_f32le",
            str(target),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and target.is_file()


def main() -> int:
    ceiling_tp = float(THRESHOLDS["bridge_gain_tp_ceiling_dbtp"])
    clamped_gain, clamped_entry, clamped_declared, _ = _gain(-24.85, -10.0)
    expected_gain = round(ceiling_tp - (-10.0), 2)
    expected_short = round(-14.0 - (-24.85 + expected_gain), 2)
    check(
        "picco che limita: gain = tetto - input_tp",
        abs(clamped_gain - expected_gain) <= 1e-9
        and abs(clamped_entry["gain_db"] - expected_gain) <= 1e-9,
        clamped_gain,
    )
    check(
        "picco che limita: peak_limited, input_tp, loudness_shortfall_lu",
        clamped_entry.get("peak_limited") is True
        and clamped_entry.get("input_tp") == -10.0
        and abs(clamped_entry.get("loudness_shortfall_lu", 0.0) - expected_short)
        <= 1e-9,
        clamped_entry,
    )
    check(
        "picco che limita: not_applied dichiara i due numeri",
        len(clamped_declared) == 1
        and abs(clamped_declared[0]["required_gain_db"] - 10.85) <= 1e-9
        and abs(clamped_declared[0]["gain_db"] - expected_gain) <= 1e-9,
        clamped_declared,
    )

    free_gain, free_entry, free_declared, free_unknown = _gain(-24.85, -25.0)
    check(
        "picco con margine: il gain resta quello della loudness",
        abs(free_gain - 10.85) <= 1e-9
        and not free_entry.get("peak_limited")
        and free_declared == []
        and free_unknown == [],
        free_entry,
    )

    unknown_gain, unknown_entry, _, unknown = _gain(-24.85, None)
    check(
        "input_tp assente: il picco non clampa, peak_headroom_unknown dichiarato",
        abs(unknown_gain - 10.85) <= 1e-9
        and not unknown_entry.get("peak_limited")
        and len(unknown) == 1,
        unknown,
    )

    base = _graph()
    check(
        "grafo senza ceiling: byte-identico a HEAD",
        hashlib.sha256(base.encode("utf-8")).hexdigest() == GOLDEN_SHA256,
        hashlib.sha256(base.encode("utf-8")).hexdigest(),
    )
    check(
        "ceiling_dbtp=None esplicito: grafo invariato",
        _graph(ceiling_dbtp=None) == base,
    )

    limited = _graph(ceiling_dbtp=CEILING)
    headroom = float(THRESHOLDS["limiter_tolerance_db"]) + float(
        THRESHOLDS["limiter_encode_overshoot_db"]
    )
    linear = 10.0 ** ((CEILING - headroom) / 20.0)
    check("grafo con ceiling: un solo alimiter", limited.count("alimiter") == 1)
    check(
        "grafo con ceiling: limit calcolato e level=false",
        f"alimiter=limit={linear:.6f}:level=false" in limited,
        f"{linear:.6f}",
    )
    check(
        "grafo con ceiling: latenza del limiter compensata",
        f"alimiter=limit={linear:.6f}:level=false:latency=true" in limited,
    )
    out_bus = [part for part in limited.split(";") if part.endswith("[aout]")]
    check(
        "grafo con ceiling: aresample 192k prima, 48k dopo",
        len(out_bus) == 1
        and f"aresample={render.LIMITER_RATE},alimiter=" in out_bus[0]
        and out_bus[0].endswith(f"aresample={render.AUDIO_RATE}[aout]"),
        out_bus,
    )

    loudnorm = "loudnorm=I=-14.0:TP=-1.0:linear=true"
    plain04 = render.edl_loudness_graph(loudnorm)
    check(
        "stage 04 senza ceiling: solo loudnorm",
        plain04 == f"[0:a]{loudnorm}[aout]" and "alimiter" not in plain04,
        plain04,
    )
    limited04 = render.edl_loudness_graph(loudnorm, ceiling_dbtp=CEILING)
    check(
        "stage 04 con ceiling: limiter dopo loudnorm",
        limited04.count("alimiter") == 1
        and limited04.index("loudnorm=") < limited04.index("alimiter=")
        and limited04.index("alimiter=")
        < limited04.index(f"aresample={render.AUDIO_RATE}")
        and f"alimiter=limit={linear:.6f}:level=false" in limited04,
        limited04,
    )

    check(
        "qc.true_peak_ceiling: target + margine del gate",
        qc.true_peak_ceiling(-1.0) == -1.0 + qc.QC_DEFAULTS["true_peak_margin"],
        qc.true_peak_ceiling(-1.0),
    )
    check(
        "render_edl e build_edl_filtergraph: ceiling keyword-only, default None",
        inspect.signature(render.render_edl).parameters["ceiling_dbtp"].default is None
        and inspect.signature(render.build_edl_filtergraph)
        .parameters["ceiling_dbtp"]
        .default
        is None
        and inspect.signature(render.render_edl).parameters["ceiling_dbtp"].kind
        is inspect.Parameter.KEYWORD_ONLY,
    )

    with tempfile.TemporaryDirectory(prefix="vx-ceiling-") as raw:
        tmp = Path(raw)
        src = tmp / "src.mp4"
        if not _make_source(src):
            skip("render reale con picco limitato", "ffmpeg non disponibile")
        else:
            sources = {"s": SourceRef(str(src), _sha256(src), 2.0)}
            video = ClipSegment("v", "s", 0.0, 2.0, 0.0, 2.0, 0, "main_talking_head")
            dialogue = ClipSegment("d", "s", 0.0, 2.0, 0.0, 2.0, 0, "dialogue")
            plan = EDLPlan(
                fps=30.0,
                duration_sec=2.0,
                aspect_ratio="9:16",
                resolution=(320, HEIGHT),
                sources=sources,
                video_tracks=(Track(0, (video,), "main"),),
                audio_tracks=(Track(0, (dialogue,), "dialogue"),),
            )
            bridged, report = bridge.build(
                plan,
                {"s": {"speech_regions": [{"start": 0.0, "end": 2.0}]}},
                target_lufs=-14.0,
                loudness_spans=lambda path, spans: {
                    "input_i": -21.75,
                    "input_tp": -8.43,
                    "input_lra": 1.0,
                },
            )
            peak_limited = any(item.get("peak_limited") for item in report.audio)
            check(
                "render reale: il picco limita davvero il gain",
                peak_limited,
                report.audio,
            )

            master = tmp / "master.mp4"
            work = tmp / "work"
            render.render_edl(
                bridged,
                {"s": str(src)},
                master,
                workdir=work,
                crf=35,
                preset="ultrafast",
                loudness=(-14.0, -1.0),
                ceiling_dbtp=qc.true_peak_ceiling(-1.0),
            )
            delivered = qc.lufs_of(master)
            check(
                "render reale: TP consegnato <= ceiling",
                float(delivered["input_tp"]) <= qc.true_peak_ceiling(-1.0),
                delivered["input_tp"],
            )
            check(
                "render reale: loudness entro 1.5 LU da -14",
                abs(float(delivered["input_i"]) + 14.0) <= 1.5,
                delivered["input_i"],
            )
            try:
                result = qc.audit_master(master, target_lufs=-14.0, target_tp=-1.0)
            except Exception as exc:  # noqa: BLE001 - the gate result is the assertion
                check(
                    "render reale: audit_master ok", False, getattr(exc, "result", exc)
                )
            else:
                statuses = {item["code"]: item["status"] for item in result["checks"]}
                check(
                    "render reale: master_lufs e master_true_peak pass",
                    statuses.get("master_lufs") == "pass"
                    and statuses.get("master_true_peak") == "pass",
                    statuses,
                )

            stats = json.loads((work / "edl_render.json").read_text(encoding="utf-8"))[
                "loudness"
            ]["measured"]
            af = render.loudnorm_two_pass(
                stats, target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )[1]
            before, after = tmp / "before.wav", tmp / "after.wav"
            ok = _render_to_wav(
                work / "final_pre_loudness.mp4",
                render.edl_loudness_graph(af),
                before,
            ) and _render_to_wav(
                work / "final_pre_loudness.mp4",
                render.edl_loudness_graph(af, ceiling_dbtp=qc.true_peak_ceiling(-1.0)),
                after,
            )
            if not ok:
                skip("limiter: riduzione di guadagno", "ffmpeg non ha prodotto i WAV")
            else:
                reduction = float(qc.lufs_of(before)["input_tp"]) - float(
                    qc.lufs_of(after)["input_tp"]
                )
                check(
                    "limiter: riduzione di guadagno <= 1.0 dB",
                    reduction <= 1.0,
                    round(reduction, 3),
                )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
