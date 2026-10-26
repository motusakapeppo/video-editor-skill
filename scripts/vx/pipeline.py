"""vx.pipeline — la catena si compone, non si cabla.

La skill non ha "la pipeline": ha un pianificatore che legge richiesta, materiale e
asset disponibili e decide quali passi esistono. Un progetto puo' avere grade,
grafica e transizioni; un altro nessuno dei tre, e la catena risultante e' piu'
corta — non una versione degradata della stessa.

Divisione delle responsabilita' (decisa dai numeri, non dal gusto):
  FFmpeg      tagli e colore    — esatto per definizione, in YUV intero
  Compositor  testo e grafica   — spazio e tempo, dove Pillow e' imbattibile
  Gate 3/5    il file che esce  — pre-encode per il colore, sul file per durata e sync

Ogni passo dichiara perche' esiste. Un passo non applicabile non viene "saltato in
silenzio": semplicemente non entra nel piano, e il piano si legge.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import contract, decide, ffmpeg as ff, gates, grade as grade_mod, layout, motion

__all__ = ["Plan", "Overlay", "plan", "run"]


@dataclass
class Overlay:
    """Asset grafico con la sua collocazione. `box` in pixel del canvas nativo."""

    name: str
    path: str
    box: Tuple[int, int, int, int]
    native: Tuple[int, int] = (1080, 1440)
    kind: str = "badge"
    pulse: float = 0.0

    def scaled(self, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
        """Riproporziona al canvas; None se il formato non e' compatibile."""
        nw, nh = self.native
        if abs(width / height - nw / nh) > 0.02:
            return None
        k = height / nh
        x, y, w, h = self.box
        return (
            int(round(x * k)),
            int(round(y * k)),
            int(round(w * k)),
            int(round(h * k)),
        )


@dataclass
class Plan:
    ok: bool
    request: str
    canvas: Dict[str, int] = field(default_factory=dict)
    engine: str = "pillow"
    grade: Optional[Dict[str, Any]] = None
    overlays: List[Dict[str, Any]] = field(default_factory=list)
    text: Dict[str, Any] = field(default_factory=dict)
    transitions: Dict[str, Any] = field(default_factory=dict)
    steps: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    blockers: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "request": self.request,
            "canvas": self.canvas,
            "engine": self.engine,
            "grade": self.grade,
            "overlays": self.overlays,
            "text": self.text,
            "transitions": self.transitions,
            "steps": self.steps,
            "reasons": self.reasons,
            "blockers": self.blockers,
        }

    def write(self, path: str | Path) -> str:
        p = Path(path)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(p)

    def summary(self) -> str:
        if not self.ok:
            return "\n".join(
                [f"SOSPESO — {len(self.blockers)} variabili aperte"]
                + [f"  [{b['code']}] {b['question']}" for b in self.blockers]
            )
        lines = [
            f"piano: {self.canvas.get('width')}x{self.canvas.get('height')} "
            f"{self.canvas.get('aspect_ratio')} · engine {self.engine}"
        ]
        lines.append(
            f"  passi: {' -> '.join(self.steps) if self.steps else '(nessuno)'}"
        )
        for r in self.reasons:
            lines.append(f"  · {r}")
        return "\n".join(lines)


def plan(
    request: str,
    *,
    source_duration: float = 0.0,
    source_size: Optional[Tuple[int, int]] = None,
    source_has_handles: bool = True,
    grade_node: Optional[Dict[str, Any]] = None,
    overlays: Sequence[Overlay] = (),
    assets: Optional[Sequence[str]] = None,
    has_text: bool = True,
) -> Plan:
    """Compone il piano. Nessun valore e' una costante di progetto.

    `grade_node` arriva dal payload: se e' None o "none", il passo di grading non
    esiste e i passi FFmpeg si riducono a tagli. `overlays` vengono riproporzionati
    al canvas e scartati se il formato non combacia.
    """
    decision = decide.interpret(request, assets=assets, has_handles=source_has_handles)
    p = Plan(
        ok=decision.ok,
        request=request,
        blockers=[b.to_dict() for b in decision.blockers],
    )
    if not decision.ok:
        return p

    canvas = decision.contract.get("canvas") or {"width": 1080, "height": 1920,
                                                 "aspect_ratio": "9:16"}
    if source_size:
        # Se il materiale e' gia' nel formato di consegna, il canvas viene da lui:
        # comporre un master 3:4 su un canvas 9:16 lo deformerebbe.
        canvas = {"width": int(source_size[0]), "height": int(source_size[1]),
                  "aspect_ratio": f"{source_size[0]}:{source_size[1]}"}
        p.reasons.append(f"canvas preso dal materiale: {canvas['aspect_ratio']}")
    else:
        p.reasons.append(f"canvas dedotto dalla piattaforma: {canvas['aspect_ratio']}")
    p.canvas = canvas
    p.engine = decision.deduced.get("engine", "pillow")

    node = grade_mod.parse(grade_node) if grade_node else grade_mod.parse(None)
    if node.is_noop():
        p.reasons.append(
            "nessun nodo cromatico nel payload: il passo di grading non esiste"
        )
    else:
        p.grade = node.to_dict()
        p.reasons.append(f"grade dal payload: {node.type}")

    compatible = []
    for ov in overlays:
        box = ov.scaled(canvas["width"], canvas["height"])
        if box is None:
            p.reasons.append(f"overlay {ov.name} scartato: formato non compatibile")
            continue
        compatible.append(
            {
                "name": ov.name,
                "path": ov.path,
                "box": list(box),
                "kind": ov.kind,
                "pulse": ov.pulse,
            }
        )
    p.overlays = compatible
    if overlays and not compatible:
        p.reasons.append("nessun overlay compatibile col canvas: si compone solo testo")

    trans = decision.deduced.get("transitions") or {}
    p.transitions = trans

    width, height = canvas["width"], canvas["height"]
    p.text = {
        "margin_lr": int(round(width * 0.055)),
        "bottom_limit": int(round(height * 0.838)),
        "wrap_chars": 30,
        "font_px_cap": int(round(height * 0.070)),
    }

    if source_duration <= 0:
        p.blockers.append(
            {
                "code": "no_duration",
                "question": "Durata sorgente non nota.",
                "options": ["fornisci il file", "specifica la durata"],
            }
        )
        p.ok = False
        return p

    steps = []
    if trans:
        steps.append("cut_overlap")
    else:
        steps.append("cut_concat")
    if p.grade:
        steps.append("grade_ffmpeg")
    if has_text:
        steps.append("composite_text")
    if p.overlays:
        steps.append("composite_graphics")
    steps += ["gate3", "gate5_pre", "encode", "gate3_final"]
    p.steps = steps
    return p


def run(
    p: Plan,
    *,
    master: str | Path,
    cues: Sequence[Tuple[float, float, str]],
    out: str | Path,
    workdir: str | Path,
    overlay_assets: Optional[Dict[str, str]] = None,
    beats: Optional[Sequence[float]] = None,
    reference: Optional[str | Path] = None,
    lossless_gate: bool = True,
    verdict: Any = None,
) -> Dict[str, Any]:
    """Esegue il piano. Salta i passi che il piano non contiene.

    REGOLA IMPRESSA (non aggirabile): senza un brief firmato dal direttore non si
    renderizza. Se `verdict` manca, o non e' firmato, il rendering viene rifiutato
    prima di toccare un solo frame. E' la correzione strutturale dell'errore in cui
    sono incorso: avevo reso uno spot scrivendo stile e animazione a mano, saltando
    la giuria, e presentando il risultato come lavoro della skill.
    """
    if not p.ok:
        raise contract.QCFailure({"plan": p.to_dict()})

    from . import board as board_mod  # noqa: PLC0415

    if verdict is None:
        raise contract.QCFailure({
            "step": "jury",
            "failures": [
                "nessun brief della giuria: la skill non renderizza su decisioni scritte "
                "a mano. Convoca i ruoli (board.convene) e fai firmare il direttore."
            ],
        })
    brief = board_mod.merge(verdict)
    if not brief:
        raise contract.QCFailure({"step": "jury", "failures": ["brief vuoto"]})
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"steps": [], "engine": p.engine}

    src = Path(master)
    graded = src
    if "grade_ffmpeg" in p.steps:
        node = grade_mod.parse(p.grade)
        graded = work / "graded.mkv"
        proc = subprocess.run(
            [
                ff.ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-vf",
                grade_mod.ffmpeg_filter(node),
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-c:a",
                "copy",
                str(graded),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise contract.QCFailure(
                {"step": "grade_ffmpeg", "failures": [proc.stderr[-300:]]}
            )
        report["steps"].append("grade_ffmpeg")
        report["graded"] = str(graded)

    lay = layout.build(p.canvas["width"], p.canvas["height"], overlay_assets or {})
    payload = motion.build_payload(
        cues,
        width=p.canvas["width"],
        height=p.canvas["height"],
        fps=25.0,
        aspect=p.canvas["aspect_ratio"],
        font_path=_font_path(),
        margin_lr=p.text["margin_lr"],
        bottom_limit=min(p.text["bottom_limit"], lay.text_limit_y),
        beats=beats,
        wrap_chars=p.text["wrap_chars"],
    )
    payload.beats = list(beats or [])
    # lo stile viene dal brief firmato: font, corpo, inchiostro, animazione
    def _rgb(v):
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return tuple(int(x) for x in v[:3])
        s = str(v or "").lstrip("#")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) if len(s) == 6 else None

    _fp = brief.get("font_path")
    if not _fp and brief.get("font"):
        from . import creative as creative_mod  # noqa: PLC0415
        _fp = next((f["path"] for f in creative_mod.font_pool()
                    if f["name"] == brief["font"]), None)
    payload.style = {
        "font_path": _fp or payload.font_path,
        "font_px": int(brief.get("font_px") or payload.font_px),
        "ink": _rgb(brief.get("ink")) or (255, 255, 255),
        "accent": _rgb(brief.get("accent")),
        "case": brief.get("case", "mixed"),
        "fade_ms": int(brief.get("fade_ms", 0)),
        "pop_ms": int(brief.get("pop_ms", pop_ms_default(payload))),
        "animation": brief.get("animation") or {},
    }
    payload.elements = []
    for ov in p.overlays:
        if "composite_graphics" in p.steps:
            payload.elements.append(
                motion.Element(
                    ov["name"],
                    ov["path"],
                    tuple(ov["box"]),
                    kind=ov["kind"],
                    z=0 if ov["kind"] == "frame" else 1,
                    pulse=float(ov["pulse"]),
                )
            )

    box = motion.animation_box(payload)
    gates.layout(lay, layout.Rect(box["x"], box["y"], box["w"], box["h"]))
    if payload.elements:
        gates.layout_elements(
            lay,
            motion.element_boxes(payload),
            text_box=layout.Rect(box["x"], box["y"], box["w"], box["h"]),
        )
    report["steps"].append("gate4")

    info = motion.render_video(graded, payload, out, engine=p.engine, grade=None)
    report["steps"].append("composite")
    report["render"] = {
        k: info[k] for k in ("frames_written", "frames_declared", "frames_match")
    }

    _frames, _dur, _fps = motion.expected_frames(graded)
    res = gates.post_render(
        contract.EditPlan(),
        {"fps": 25.0, "duration": 200.0},
        Path(out),
        workdir=work,
        planned_total=_dur,
        expected_size=(p.canvas["width"], p.canvas["height"]),
    )
    report["steps"].append("gate3")
    report["gate3"] = res.ok
    if not res.ok:
        raise contract.QCFailure(
            {"step": "gate3", "checks": [c.__dict__ for c in res.checks]}
        )

    if reference:
        from . import motion as motion_mod

        _frames, dur, _fps = motion_mod.expected_frames(graded)
        node = grade_mod.parse(p.grade) if p.grade else grade_mod.parse(None)
        if not node.is_noop():
            cal = gates.calibrate(node, graded, at=dur * 0.4, workdir=work / "cal")
            worst = next(c.value for c in cal.checks if c.code == "max_dl")
            report["gate5_pre"] = worst
            report["steps"].append("gate5_pre")
        try:
            gates.color(Path(out), Path(reference), region=(0.05, 0.60), inset=0.05)
            report["gate5_file"] = "pass"
        except contract.QCFailure as exc:
            report["gate5_file"] = [
                c for c in exc.result.get("checks", []) if c["status"] == "fail"
            ]
    return report


def pop_ms_default(payload: Any) -> int:
    return int(getattr(payload, "font_px", 24))


def _font_path() -> str:
    candidates = [
        Path(r"C:\Users\motus\AppData\Local\Microsoft\Windows\Fonts\Antonio-Bold.ttf"),
        Path(r"C:\Windows\Fonts\ariblk.ttf"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    raise contract.QCFailure({"step": "font", "failures": ["nessun font disponibile"]})
