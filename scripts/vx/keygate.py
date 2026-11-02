"""vx.keygate — il cancello dei keyframe: nessun render completo senza approvazione.

Tre fotogrammi chiave, al minimo, prima di bruciare un render intero: e' la
regola e qui e' un cancello, non un consiglio. La sequenza e':

1. :func:`plan` estrae da **3 a 6** fotogrammi dalla timeline vera (ritagliando
   la clip al punto scelto, con i suoi effetti) e calcola un'impronta: gli
   identificatori, i tagli, gli effetti e lo sha256 di ogni fotogramma;
2. :func:`approve` scrive il record con quell'impronta;
3. :func:`check` ricalcola l'impronta sulla timeline che si sta per rendere e
   pretende che coincida. Se il record manca, o se i keyframe sono cambiati dopo
   l'approvazione, il cancello e' chiuso: ``KEYFRAMES_NOT_APPROVED`` con il numero
   di approvazioni mancanti.

Un numero di keyframe fuori da 3-6 e' ``KEYFRAME_COUNT`` con il numero chiesto.
Nessuna soglia e' arbitraria: sotto tre non si vede un montaggio, sopra sei non
si guarda piu' il montaggio ma i fotogrammi.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contract import VxError
from . import edl3, edl3_render
from . import ffmpeg as ff

GATE_VERSION = "vx.keygate/1"
MIN_KEYFRAMES = 3
MAX_KEYFRAMES = 6
RECORD_NAME = "keyframes.json"


class GateError(VxError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class KeyframePlan:
    timeline_digest: str
    digest: str
    count: int
    frames: Tuple[Dict[str, Any], ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": GATE_VERSION,
            "timeline_digest": self.timeline_digest,
            "digest": self.digest,
            "count": self.count,
            "frames": [dict(frame) for frame in self.frames],
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _timeline_payload(timeline: edl3.Timeline3) -> Dict[str, Any]:
    return {
        "fps": timeline.fps,
        "size": list(timeline.size),
        "clips": [clip.to_dict() for clip in timeline.clips],
    }


def timeline_digest(timeline: edl3.Timeline3) -> str:
    return hashlib.sha256(
        json.dumps(_timeline_payload(timeline), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _frame_at(clip: edl3.Clip3) -> float:
    start = 0.0 if clip.src_in is None else float(clip.src_in)
    end = start + 1.0 if clip.src_out is None else float(clip.src_out)
    return max(start, min(end, (start + end) / 2.0))


def _extract(
    timeline: edl3.Timeline3,
    index: int,
    *,
    out_dir: Path,
    window: float = 0.12,
) -> Dict[str, Any]:
    clip = timeline.clips[index]
    at = _frame_at(clip)
    start = max(0.0, at - window / 2.0)
    probe = edl3.Clip3(
        id=f"{clip.id}-kf",
        source=clip.source,
        src_in=start,
        src_out=start + window,
        effects=list(clip.effects),
    )
    document = edl3.Timeline3(fps=timeline.fps, size=timeline.size, clips=[probe])
    out_dir.mkdir(parents=True, exist_ok=True)
    piece = out_dir / f"kf{index}.mp4"
    png = out_dir / f"kf{index}.png"
    edl3_render.render(document, piece, workdir=out_dir / f"w-kf{index}", audio="drop", settings={})
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(piece), "-frames:v", "1", str(png),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not png.is_file():
        raise GateError(
            "KEYFRAME_RENDER",
            f"clip {index}: {proc.stderr.decode('utf-8', 'replace')[-200:]}",
        )
    piece.unlink(missing_ok=True)
    return {
        "clip": clip.id,
        "index": index,
        "at": round(at, 6),
        "sha256": _sha256(png),
        "path": str(png),
    }


def plan(
    timeline: edl3.Timeline3,
    picks: Sequence[int],
    *,
    out_dir: Path,
) -> KeyframePlan:
    """Extract the chosen keyframes and fingerprint them together with the timeline."""
    chosen = [int(index) for index in picks]
    if not MIN_KEYFRAMES <= len(chosen) <= MAX_KEYFRAMES:
        raise GateError(
            "KEYFRAME_COUNT",
            f"{len(chosen)} keyframes requested; the gate accepts "
            f"{MIN_KEYFRAMES}-{MAX_KEYFRAMES}",
        )
    if len(set(chosen)) != len(chosen):
        raise GateError("KEYFRAME_DUPLICATE", f"repeated keyframe indices: {chosen}")
    for index in chosen:
        if not 0 <= index < len(timeline.clips):
            raise GateError(
                "KEYFRAME_INDEX",
                f"keyframe index {index} outside 0..{len(timeline.clips) - 1}",
            )
    frames = [_extract(timeline, index, out_dir=Path(out_dir)) for index in chosen]
    timeline_sha = timeline_digest(timeline)
    # Le impronte entrano nel digest senza il percorso: dove hai scritto i PNG
    # non e' una proprieta' dei keyframe, e due giri in cartelle diverse devono
    # dare lo stesso cancello.
    fingerprint = [
        {"clip": frame["clip"], "index": frame["index"], "at": frame["at"], "sha256": frame["sha256"]}
        for frame in frames
    ]
    digest = hashlib.sha256(
        json.dumps(
            {"version": GATE_VERSION, "timeline": timeline_sha, "frames": fingerprint},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return KeyframePlan(
        timeline_digest=timeline_sha,
        digest=digest,
        count=len(frames),
        frames=tuple(frames),
    )


def approve(keyframe_plan: KeyframePlan, record: Path) -> Dict[str, Any]:
    record = Path(record)
    record.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": GATE_VERSION,
        "approved": True,
        "digest": keyframe_plan.digest,
        "timeline_digest": keyframe_plan.timeline_digest,
        "count": keyframe_plan.count,
        "frames": [dict(frame) for frame in keyframe_plan.frames],
    }
    record.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return payload


def read(record: Path) -> Optional[Dict[str, Any]]:
    record = Path(record)
    if not record.is_file():
        return None
    try:
        payload = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


AUTONOMOUS_KEY = "autonomous"


def declare_autonomous(brief: Dict[str, Any], why: str) -> Dict[str, Any]:
    """The only exception to the gate: declared in the brief, and motivated."""
    brief[AUTONOMOUS_KEY] = {"declared": True, "why": str(why)}
    return brief


def autonomous_problems(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["autonomous: expected an object with declared and why"]
    found: List[str] = []
    if value.get("declared") is not True:
        found.append("autonomous.declared: must be true to open the gate")
    if not str(value.get("why") or "").strip():
        found.append("autonomous.why: a waiver without a reason is not a declaration")
    return found


def autonomous_waiver(brief: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The waiver, if the brief really declares and motivates it; else ``None``."""
    if not isinstance(brief, dict):
        return None
    value = brief.get(AUTONOMOUS_KEY)
    if not isinstance(value, dict):
        return None
    if autonomous_problems(value):
        return None
    return {"declared": True, "why": str(value.get("why")).strip()}


def waiver_line(waiver: Dict[str, Any]) -> str:
    """The sentence the report repeats, with the reason verbatim."""
    return (
        "modalita' autonoma dichiarata nel brief: il cancello dei keyframe e' "
        f"aperto per eccezione, motivo dichiarato: \"{waiver['why']}\""
    )


def check(
    keyframe_plan: KeyframePlan,
    record: Path,
    *,
    brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Close the gate unless the record matches, or the brief waives it in writing."""
    payload = read(record)
    waiver = autonomous_waiver(brief)
    if payload is not None:
        approved = int(payload.get("count") or 0)
        if payload.get("approved") is True and approved > 0:
            if payload.get("digest") != keyframe_plan.digest:
                raise GateError(
                    "KEYFRAMES_NOT_APPROVED",
                    f"0 of {keyframe_plan.count} keyframes approved: the keyframes changed "
                    f"after approval (record {str(payload.get('digest'))[:12]} != now "
                    f"{keyframe_plan.digest[:12]})",
                )
            return dict(payload, waiver=None, report="")
    if waiver is not None:
        return {
            "version": GATE_VERSION,
            "approved": False,
            "digest": keyframe_plan.digest,
            "count": keyframe_plan.count,
            "waiver": waiver,
            "report": waiver_line(waiver),
        }
    if payload is None:
        raise GateError(
            "KEYFRAMES_NOT_APPROVED",
            f"0 of {keyframe_plan.count} keyframes approved: no record at {Path(record)} "
            "and the brief does not declare autonomous mode",
        )
    raise GateError(
        "KEYFRAMES_NOT_APPROVED",
        f"0 of {keyframe_plan.count} keyframes approved: the record is not an approval "
        "and the brief does not declare autonomous mode",
    )


def describe(keyframe_plan: KeyframePlan) -> Dict[str, Any]:
    return {
        "version": GATE_VERSION,
        "count": keyframe_plan.count,
        "digest": keyframe_plan.digest,
        "timeline_digest": keyframe_plan.timeline_digest,
        "frames": [
            {"clip": frame["clip"], "at": frame["at"], "sha256": frame["sha256"][:12]}
            for frame in keyframe_plan.frames
        ],
    }


__all__ = [
    "AUTONOMOUS_KEY",
    "GATE_VERSION",
    "GateError",
    "KeyframePlan",
    "MAX_KEYFRAMES",
    "MIN_KEYFRAMES",
    "RECORD_NAME",
    "approve",
    "autonomous_problems",
    "autonomous_waiver",
    "check",
    "declare_autonomous",
    "describe",
    "plan",
    "read",
    "timeline_digest",
    "waiver_line",
]
