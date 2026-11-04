"""Per-clip effects execute inside the EDL render path (``render_edl``).

    python tests/test_render_effects.py

Script-style: every check prints PASS/FAIL and the process exits 0 only when all
checks pass. Synthetic clip (``testsrc2``), real ffmpeg, no network.

What it proves:

E1. a plan without effects produces the very graph this engine shipped (sha of
    the normalised graph equals the baseline literal) and reports
    ``per_clip: 0``;
E2. a plan with three clips, each carrying ``transform+grade+blur``, renders a
    6 s master at 320x240 and every clip's frame differs from the source by a
    mean absolute difference above 8 (the three numbers are printed);
E3. the three effect chains live in three distinct namespaces
    (``fxc0_*``/``fxc1_*``/``fxc2_*``) in one ``filter_complex``;
E4. the render report carries ``render.effects {per_clip, budget_used}`` and the
    same block is on disk as ``render_effects.json``;
E5. ``particles`` is refused with ``PRIMITIVE_NOT_RENDERED`` and no master is
    written;
E6. a plan-level table (``plan.effects`` keyed by ``clip_id``) and a historical
    name (``"zoom"``) reach the graph through ``expand()``;
E7. an empty spec leaves the graph byte-identical;
E8. the cost of the effect chain is measured (seconds with and without effects,
    both printed).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import edl_plan, ffmpeg, probe as probe_mod, render  # noqa: E402
from vx.contract import RenderFailure  # noqa: E402

WIDTH = 320
HEIGHT = 240
FPS = 30
SECONDS = 6.0
CLIP_SECONDS = 2.0
PASS = 0
FAIL = 0

#: The graph ``build_edl_filtergraph`` produced before effects were wired in,
#: for this exact plan (3 clips, fill=fit, no audio, no reframe/restore).
GOLDEN_GRAPH = (
    "[0:v]trim=start=0.000000:end=2.000000,setpts=PTS-STARTPTS,"
    "scale=320:240:force_original_aspect_ratio=decrease,"
    "pad=320:240:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v0];"
    "[0:v]trim=start=2.000000:end=4.000000,setpts=PTS-STARTPTS,"
    "scale=320:240:force_original_aspect_ratio=decrease,"
    "pad=320:240:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v1];"
    "[0:v]trim=start=4.000000:end=6.000000,setpts=PTS-STARTPTS,"
    "scale=320:240:force_original_aspect_ratio=decrease,"
    "pad=320:240:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v2];"
    "[v0][v1][v2]concat=n=3:v=1:a=0[base];[base]null[vout]"
)

#: transform + grade + blur, cost 1.942 at 2 s (under the 3.0 settings budget).
EFFECT_SPEC = {
    "id": "punch-grade-blur",
    "duration": CLIP_SECONDS,
    "primitives": [
        {
            "kind": "transform",
            "params": {"scale": [1.0, 1.18]},
            "curve": {"easing": "in_out"},
        },
        {
            "kind": "grade",
            "params": {"contrast": [1.0, 1.30], "saturation": 1.25},
        },
        {"kind": "blur", "params": {"sigma": 6.0}},
    ],
}


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def run(command: list) -> None:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:])


def make_source(path: Path) -> None:
    run(
        [
            ffmpeg.ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS}",
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


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def graph_sha(graph: str) -> str:
    return hashlib.sha256((" ".join(graph.split()) + "\n").encode()).hexdigest()


def build_plan(source: Path, effects: object = None, plan_table: object = None):
    clips = []
    for index in range(3):
        clip = edl_plan.ClipSegment(
            f"v0_{index}",
            "c01",
            index * CLIP_SECONDS,
            (index + 1) * CLIP_SECONDS,
            index * CLIP_SECONDS,
            (index + 1) * CLIP_SECONDS,
            0,
            "main_talking_head",
        )
        if effects is not None:
            object.__setattr__(clip, "effects", effects)
        clips.append(clip)
    plan = edl_plan.EDLPlan(
        fps=FPS,
        duration_sec=SECONDS,
        aspect_ratio="4:3",
        resolution=(WIDTH, HEIGHT),
        sources={
            "c01": edl_plan.SourceRef(
                path=str(source), sha256="a" * 64, duration=SECONDS
            )
        },
        video_tracks=(edl_plan.Track(0, tuple(clips), "a_roll"),),
    )
    if plan_table is not None:
        object.__setattr__(plan, "effects", plan_table)
    return plan


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vx_render_effects_"))
    try:
        source = tmp / "source.mp4"
        make_source(source)

        # -- E1/E7: no effects, no bytes changed ---------------------------- #
        report: dict = {}
        plain = build_plan(source)
        base_graph = render.build_edl_filtergraph(
            plain, {"c01": 0}, has_audio=False, effects_report=report
        )
        check("E1 grafo senza effetti == golden", base_graph == GOLDEN_GRAPH,
              graph_sha(base_graph)[:16])
        check("E1 sha del grafo golden",
              graph_sha(base_graph)
              == "3328a48210692246dccdb33bb1ca436c9b3ec5c5d606fba03a7d53f7d682bd34",
              graph_sha(base_graph))
        check("E1 report senza effetti: per_clip=0", report.get("per_clip") == 0,
              json.dumps(report, sort_keys=True))
        check("E1 report senza effetti: budget_used=0",
              float(report.get("budget_used") or 0.0) == 0.0)

        empty = build_plan(source, effects=[{"primitives": []}])
        check("E7 spec vuota: grafo identico al golden",
              render.build_edl_filtergraph(empty, {"c01": 0}, has_audio=False)
              == GOLDEN_GRAPH)

        # -- E2/E3/E4: three clips, transform+grade+blur each -------------- #
        effects = build_plan(source, effects=[dict(EFFECT_SPEC)])
        fx_report: dict = {}
        fx_graph = render.build_edl_filtergraph(
            effects, {"c01": 0}, has_audio=False, effects_report=fx_report
        )
        check("E3 zoompan per ogni clip", fx_graph.count("zoompan=") == 3,
              fx_graph.count("zoompan="))
        prefixes = [f"fxc{index}_" for index in range(3)]
        check("E3 namespace per clip",
              all(prefix in fx_graph for prefix in prefixes),
              [prefix for prefix in prefixes if prefix in fx_graph])
        check("E3 nessuna catena fuori namespace",
              "[fx0]" not in fx_graph and "[fx1]" not in fx_graph)
        check("E4 report per_clip=3", fx_report.get("per_clip") == 3,
              json.dumps(fx_report.get("clips"), sort_keys=True))
        check("E4 report budget_used>0",
              float(fx_report.get("budget_used") or 0.0) > 0.0,
              fx_report.get("budget_used"))
        check("E4 report budget dichiarato",
              float(fx_report.get("budget") or 0.0) > 0.0,
              fx_report.get("budget"))

        started = time.monotonic()
        base_master = render.render_edl(
            plain,
            {"c01": str(source)},
            tmp / "master_plain.mp4",
            workdir=tmp / "work_plain",
            has_audio=False,
            loudness=None,
        )
        base_seconds = time.monotonic() - started
        check("E1 master senza effetti scritto",
              Path(base_master).is_file() and Path(base_master).stat().st_size > 0,
              sha(Path(base_master))[:16])

        disk_report: dict = {}
        started = time.monotonic()
        fx_master = render.render_edl(
            effects,
            {"c01": str(source)},
            tmp / "master_fx.mp4",
            workdir=tmp / "work_fx",
            has_audio=False,
            loudness=None,
            effects_report=disk_report,
        )
        fx_seconds = time.monotonic() - started
        check("E2 master con effetti scritto",
              Path(fx_master).is_file() and Path(fx_master).stat().st_size > 0,
              sha(Path(fx_master))[:16])
        found = probe_mod.probe(str(fx_master))
        check("E2 durata e risoluzione dichiarate",
              abs(found.duration - SECONDS) <= 0.1
              and int(found.width) == WIDTH
              and int(found.height) == HEIGHT,
              f"{found.duration:.3f}s {found.width}x{found.height}")

        for index in range(3):
            at = index * CLIP_SECONDS + CLIP_SECONDS / 2.0
            out_frame = frame(Path(fx_master), at, tmp / f"fx_{index}.png")
            src_frame = frame(source, at, tmp / f"src_{index}.png")
            diff = mean_abs(out_frame, src_frame)
            check(f"E2 clip {index}: frame diverso dal sorgente (>{8})",
                  diff > 8.0, f"{diff:.3f}")

        on_disk = json.loads(
            (tmp / "work_fx" / "render_effects.json").read_text(encoding="utf-8")
        )
        block = (on_disk.get("render") or {}).get("effects") or {}
        check("E4 render_effects.json: render.effects.per_clip=3",
              block.get("per_clip") == 3, json.dumps(block, sort_keys=True))
        check("E4 render_effects.json: budget_used=report",
              block.get("budget_used") == disk_report.get("budget_used"),
              block.get("budget_used"))

        # -- E6: plan-level table and a historical name --------------------- #
        table_plan = build_plan(source, plan_table={"v0_0": "zoom"})
        table_report: dict = {}
        table_graph = render.build_edl_filtergraph(
            table_plan, {"c01": 0}, has_audio=False, effects_report=table_report
        )
        check("E6 plan.effects + nome storico 'zoom' nel grafo",
              "zoompan=" in table_graph and table_report.get("per_clip") == 1,
              table_report.get("per_clip"))

        # -- E5: particles refused by code, no master ----------------------- #
        refused = build_plan(
            source,
            effects=[{"id": "dust", "primitives": [{"kind": "particles", "params": {}}]}],
        )
        bad_out = tmp / "master_particles.mp4"
        code = ""
        raised = False
        try:
            render.render_edl(
                refused,
                {"c01": str(source)},
                bad_out,
                workdir=tmp / "work_particles",
                has_audio=False,
                loudness=None,
            )
        except RenderFailure as exc:
            raised = True
            payload = exc.args[0] if exc.args else {}
            code = payload.get("code", "") if isinstance(payload, dict) else ""
        check("E5 particles: RenderFailure dichiarata", raised)
        check("E5 particles: codice PRIMITIVE_NOT_RENDERED",
              code == "PRIMITIVE_NOT_RENDERED", code)
        check("E5 particles: nessun file scritto", not bad_out.exists())

        # -- E8: the measured cost ----------------------------------------- #
        print(
            f"  [INFO] costo misurato: senza effetti {base_seconds:.3f}s, "
            f"con 3 effetti/clip {fx_seconds:.3f}s (+{fx_seconds - base_seconds:.3f}s)"
        )
        check("E8 costo misurato su entrambi i render",
              base_seconds > 0.0 and fx_seconds > 0.0,
              f"{base_seconds:.3f}/{fx_seconds:.3f}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
