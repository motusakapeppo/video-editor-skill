"""J/L cuts: a dialogue boundary moves by <=1.5 s onto its own audio track."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cutplan, probe, render  # noqa: E402
from vx.edl import Edl, EdlShot  # noqa: E402
from vx.edl_plan import CameraSpec, SourceRef  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FFMPEG = os.environ.get("VX_FFMPEG") or "ffmpeg"


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:56} {reason}")


def _shot(
    name: str, src_in: float, src_out: float, t_in: float, t_out: float
) -> EdlShot:
    return EdlShot(
        name,
        src_in,
        src_out,
        src_out - src_in,
        t_in,
        t_out,
        int(round((t_out - t_in) * 25)),
        "bar",
        1.5,
    )


def _edl(shots: list) -> Edl:
    return Edl(
        fps=25.0,
        shots=shots,
        total=shots[-1].t_out,
        frames=sum(shot.frames for shot in shots),
        rhythmic_shots=len(shots),
        inaction_shots=0,
    )


def _sources(duration: float = 20.0) -> dict:
    return {"src_01": SourceRef("main.mp4", "a" * 64, duration)}


def _shift(
    at: float,
    lead: float,
    *,
    finding: str = "audio.dialogue_lead",
    t0: float | None = None,
    t1: float | None = None,
) -> cutplan.DialogueShift:
    return cutplan.DialogueShift(
        at=at,
        lead_sec=lead,
        finding_id=finding,
        finding_source_id="src_01",
        t0=at - 0.5 if t0 is None else t0,
        t1=at + 0.5 if t1 is None else t1,
    )


def _expect_error(
    label: str, edl: Edl, shift: cutplan.DialogueShift, duration: float = 20.0
) -> None:
    try:
        cutplan.build_edl_plan(
            edl,
            source_id="src_01",
            sources=_sources(duration),
            dialogue_shifts=[shift],
            fps=25.0,
            duration=edl.total,
        )
    except cutplan.EDLValidationError as exc:
        check(label, True, f"{type(exc).__name__}: {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"{type(exc).__name__}: {exc}")
    else:
        check(label, False, "accettato")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _make(path: Path, color: str) -> bool:
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
            f"color=c={color}:s=320x180:r=25:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
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
            "128k",
            str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file()


def main() -> int:
    two = _edl(
        [_shot("main", 0.0, 10.0, 0.0, 10.0), _shot("main", 10.0, 20.0, 10.0, 20.0)]
    )

    j_cut = cutplan.build_edl_plan(
        two,
        source_id="src_01",
        sources=_sources(),
        dialogue_shifts=[_shift(10.0, 1.0)],
        fps=25.0,
        duration=20.0,
    )
    check("J: piano valido", j_cut.plan.validate() == [], j_cut.plan.validate())
    check(
        "J: due tracce audio",
        len(j_cut.plan.audio_tracks) == 2,
        len(j_cut.plan.audio_tracks),
    )
    if len(j_cut.plan.audio_tracks) == 2:
        base = j_cut.plan.audio_tracks[0].clips[0]
        lead = j_cut.plan.audio_tracks[1].clips[0]
        check("J: A resta 0-10", (base.dst_in, base.dst_out) == (0.0, 10.0), base)
        check("J: B entra a 9", (lead.dst_in, lead.src_in) == (9.0, 9.0), lead)
        check("J: video invariato", j_cut.plan.video_tracks[0].clips[1].dst_in == 10.0)
    check(
        "J: witness dialogue_lead",
        j_cut.witness("a2")["finding_id"] == "audio.dialogue_lead",
        j_cut.witness("a2"),
    )

    l_cut = cutplan.build_edl_plan(
        two,
        source_id="src_01",
        sources=_sources(),
        dialogue_shifts=[_shift(10.0, -1.0, finding="audio.dialogue_trail")],
        fps=25.0,
        duration=20.0,
    )
    check("L: piano valido", l_cut.plan.validate() == [], l_cut.plan.validate())
    check(
        "L: due tracce audio",
        len(l_cut.plan.audio_tracks) == 2,
        len(l_cut.plan.audio_tracks),
    )
    if len(l_cut.plan.audio_tracks) == 2:
        base = l_cut.plan.audio_tracks[0].clips[0]
        trail = l_cut.plan.audio_tracks[1].clips[0]
        check("L: A esce a 11", (trail.dst_out, trail.src_out) == (11.0, 11.0), trail)
        check("L: B resta a 10", (base.dst_in, base.src_out) == (10.0, 20.0), base)
    check(
        "L: witness dialogue_trail",
        l_cut.witness("a1")["finding_id"] == "audio.dialogue_trail",
        l_cut.witness("a1"),
    )

    _expect_error("lead 0 rifiutato", two, _shift(10.0, 0.0))
    _expect_error("lead 1.6 rifiutato", two, _shift(10.0, 1.6))
    _expect_error(
        "finding sbagliato per J",
        two,
        _shift(10.0, 0.5, finding="audio.dialogue_trail"),
    )
    _expect_error("at non e' un taglio", two, _shift(5.0, 0.5))

    early = _edl(
        [
            _shot("main", 0.0, 0.5, 0.0, 0.5),
            _shot("main", 0.5, 2.0, 0.5, 2.0),
        ]
    )
    _expect_error("J oltre l'inizio sorgente", early, _shift(0.5, 1.0), duration=2.0)

    short = _edl(
        [
            _shot("main", 0.0, 2.0, 0.0, 2.0),
            _shot("main", 2.0, 3.0, 2.0, 3.0),
        ]
    )
    _expect_error(
        "L oltre la fine sorgente",
        short,
        _shift(2.0, -1.4, finding="audio.dialogue_trail"),
        duration=3.0,
    )

    three = _edl(
        [
            _shot("main", 0.0, 5.0, 0.0, 5.0),
            _shot("main", 5.0, 10.0, 5.0, 10.0),
            _shot("main", 10.0, 13.0, 10.0, 13.0),
        ]
    )
    two_lead = cutplan.build_edl_plan(
        three,
        source_id="src_01",
        sources=_sources(),
        dialogue_shifts=[_shift(5.0, 0.5), _shift(10.0, 0.5)],
        fps=25.0,
        duration=13.0,
    )
    check(
        "due J su tagli diversi",
        len(two_lead.plan.audio_tracks) == 3 and two_lead.plan.validate() == [],
        len(two_lead.plan.audio_tracks),
    )

    with tempfile.TemporaryDirectory(prefix="vx-jl-") as raw:
        tmp = Path(raw)
        a, b = tmp / "a.mp4", tmp / "b.mp4"
        if not (_make(a, "blue") and _make(b, "red")):
            skip("render J reale", "ffmpeg non disponibile")
        else:
            edl = _edl(
                [_shot("main", 0.0, 1.0, 0.0, 1.0), _shot("main", 1.0, 2.0, 1.0, 2.0)]
            )
            sources = {
                "src_01": SourceRef(str(a), _sha256(a), 2.0, CameraSpec(0.0, 35.0)),
                "src_02": SourceRef(str(b), _sha256(b), 2.0, CameraSpec(45.0, 35.0)),
            }
            built = cutplan.build_edl_plan(
                edl,
                source_id="src_01",
                sources=sources,
                dialogue_shifts=[
                    cutplan.DialogueShift(
                        1.0, 0.4, "audio.dialogue_lead", "src_01", 0.9, 1.1
                    )
                ],
                fps=25.0,
                duration=2.0,
            )
            built = cutplan.build_edl_plan(
                edl,
                source_id="src_01",
                sources=sources,
                dialogue_shifts=[
                    cutplan.DialogueShift(
                        1.0, 0.4, "audio.dialogue_lead", "src_01", 0.9, 1.1
                    )
                ],
                fps=25.0,
                duration=2.0,
            )
            out = tmp / "out.mp4"
            try:
                render.render_edl(
                    built.plan,
                    {"src_01": str(a), "src_02": str(b)},
                    out,
                    workdir=tmp / "work",
                    crf=30,
                    preset="ultrafast",
                )
            except Exception as exc:  # noqa: BLE001
                check("render J reale", False, f"{type(exc).__name__}: {exc}")
            else:
                info = probe.probe(str(out))
                check("render J reale: file", out.is_file() and out.stat().st_size > 0)
                check(
                    "render J reale: durata",
                    abs(info.duration - 2.0) <= 0.15,
                    info.duration,
                )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
