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
