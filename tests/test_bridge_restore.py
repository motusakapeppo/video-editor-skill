"""A7: denoise and stabilisation only on a finding, single pass, declared.

python tests/test_bridge_restore.py

No ffmpeg, no media: the audio and video analyses are dicts. The test pins the
two directions of the gate — a finding that crosses its threshold produces
exactly one filter per clip, a finding that does not is declared with the
measured value and leaves the graph untouched — plus the failure path, where a
missing analysis is a declaration and never a filter.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import bridge, contract, edl_plan, render  # noqa: E402

PASS = 0
FAIL = 0

SPEECH = [(0.0, 1.0), (1.8, 4.0), (4.6, 6.0)]
SILENCE = [(1.0, 1.8), (4.0, 4.6)]
SNR_LOW = float(contract.THRESHOLDS["noise_snr_low"])
JITTER_HIGH = float(contract.THRESHOLDS["jitter_shaky"])


def check(label: str, ok: bool, description: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:60} {description}")


def analysis(snr: float = 8.0, floor: float = -38.0) -> dict:
    return {
        "speech_regions": [{"start": s, "end": e} for s, e in SPEECH],
        "dead_air": [{"start": s, "end": e, "duration": e - s} for s, e in SILENCE],
        "snr_db": snr,
        "noise_floor_dbfs": floor,
    }


def motion(score: float = 0.52) -> dict:
    return {"jitter": {"score": score, "headroom_pct": 30.0}, "scene_cuts": []}


def plan() -> edl_plan.EDLPlan:
    source = {"c01": edl_plan.SourceRef(path="c01.mp4", sha256="a" * 64, duration=6.0)}
    video = edl_plan.ClipSegment(
        "v0_0", "c01", 0.0, 6.0, 0.0, 6.0, 0, "main_talking_head"
    )
    dialogue = edl_plan.ClipSegment("a0_0", "c01", 0.0, 6.0, 0.0, 6.0, 0, "dialogue")
    music = edl_plan.ClipSegment(
        "a1_0", "c01", 0.0, 6.0, 0.0, 6.0, 0, "music", None, None, -8.0
    )
    return edl_plan.EDLPlan(
        fps=25.0,
        duration_sec=6.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources=source,
        video_tracks=(edl_plan.Track(0, (video,), "a_roll"),),
        audio_tracks=(
            edl_plan.Track(0, (dialogue,), "dialogue"),
            edl_plan.Track(1, (music,), "music"),
        ),
        ducking=edl_plan.DuckingSpec(),
    )


def build(
    audio_analysis: object, video_analysis: object = ..., motion_none: bool = False
):
    kwargs = {} if motion_none else {"motion": {"c01": video_analysis}}
    return bridge.build(
        plan(),
        {"c01": audio_analysis},
        target_lufs=-14.0,
        loudness_spans=lambda path, spans: {"input_i": -20.0},
        **kwargs,
    )


def graph(new_plan: edl_plan.EDLPlan, report: bridge.BridgeReport) -> str:
    restore = {
        item["clip_id"]: {
            "denoise": item["denoise_filter"] if item["denoise"] else None,
            "stabilize": bool(item["stabilize"]),
        }
        for item in report.restore
    }
    return render.build_edl_filtergraph(
        new_plan, {"c01": 0}, has_audio=True, restore=restore
    )


def default_graph(new_plan: edl_plan.EDLPlan) -> str:
    return render.build_edl_filtergraph(new_plan, {"c01": 0}, has_audio=True)


def reasons(report: bridge.BridgeReport) -> list:
    return [item.get("reason") for item in report.not_applied]


def main() -> int:
    base_graph = render.build_edl_filtergraph(plan(), {"c01": 0}, has_audio=True)

    # -- finding present: both filters, one instance per clip --------------- #
    new_plan, report = build(analysis(), motion())
    check("una voce restore per frammento", len(report.restore) == 3)
    required = {"clip_id", "denoise", "stabilize", "noise_dbfs", "jitter", "declared"}
    check(
        "ogni voce porta i campi dichiarati",
        all(required <= set(item) for item in report.restore),
        sorted(report.restore[0]),
    )
    check(
        "denoise applicato quando snr < soglia",
        all(item["denoise"] for item in report.restore)
        and all(item["noise_dbfs"] == -38.0 for item in report.restore),
        [(item["snr_db"], item["noise_dbfs"]) for item in report.restore],
    )
    check(
        "stabilize proposto quando jitter >= soglia",
        all(item["stabilize"] for item in report.restore)
        and all(item["jitter"] == 0.52 for item in report.restore),
        report.restore[0]["declared"],
    )
    check(
        "nessun not_applied di gating quando il finding c'e'",
        "denoise_not_justified" not in reasons(report)
        and "stabilize_not_justified" not in reasons(report),
        reasons(report),
    )

    applied = graph(new_plan, report)
    check(
        "afftdn su ogni frammento dialogato",
        applied.count("afftdn=nf=-38.0") == 3,
        applied.count("afftdn"),
    )
    check("deshake su ogni frammento a_roll", applied.count("deshake") == 3)
    check(
        "un solo afftdn per catena (passata singola)",
        all(
            part.count("afftdn") == 1 for part in applied.split(";") if "afftdn" in part
        ),
    )
    check(
        "denoise prima del gain, stabilize proposto prima della geometria",
        applied.index("afftdn") < applied.index("volume=")
        and all(
            part.index("deshake") < part.index("scale=")
            for part in applied.split(";")
            if "deshake" in part
        ),
    )

    # -- finding absent: declared, measured, graph untouched ---------------- #
    quiet_plan, quiet = build(analysis(snr=22.0, floor=-55.0), motion(0.12))
    check(
        "senza finding nessun filtro",
        not any(item["denoise"] or item["stabilize"] for item in quiet.restore),
        [(item["denoise"], item["stabilize"]) for item in quiet.restore],
    )
    check(
        "il valore misurato accompagna il rifiuto",
        all(
            item["noise_dbfs"] == -55.0 and item["jitter"] == 0.12
            for item in quiet.restore
        ),
        quiet.restore[0]["declared"],
    )
    skipped_denoise = [
        item
        for item in quiet.not_applied
        if item.get("reason") == "denoise_not_justified"
    ]
    skipped_stabilize = [
        item
        for item in quiet.not_applied
        if item.get("reason") == "stabilize_not_justified"
    ]
    check(
        "denoise non applicato: dichiarato con snr e soglia",
        len(skipped_denoise) == 3
        and all(
            item["snr_db"] == 22.0 and item["threshold_db"] == SNR_LOW
            for item in skipped_denoise
        ),
        skipped_denoise[0],
    )
    check(
        "stabilize non applicato: dichiarato con jitter e soglia",
        len(skipped_stabilize) == 3
        and all(
            item["jitter"] == 0.12 and item["threshold"] == JITTER_HIGH
            for item in skipped_stabilize
        ),
        skipped_stabilize[0],
    )
    check(
        "nessun finding, grafo identico al default",
        graph(quiet_plan, quiet) == default_graph(quiet_plan),
    )

    # -- the two thresholds, exactly at the edge ---------------------------- #
    edge_plan, edge = build(analysis(snr=SNR_LOW, floor=-30.0), motion(JITTER_HIGH))
    check(
        "snr == soglia: sotto significa sotto",
        not any(item["denoise"] for item in edge.restore),
        edge.restore[0]["snr_db"],
    )
    check(
        "jitter == soglia: al limite si applica",
        all(item["stabilize"] for item in edge.restore),
        edge.restore[0]["jitter"],
    )

    # -- missing analysis: a declaration, never a filter -------------------- #
    failed_plan, failed = build(
        RuntimeError("audio muto"), RuntimeError("niente video")
    )
    check("analisi fallita: una clip, una voce", len(failed.restore) == 1)
    check(
        "analisi fallita: nessun filtro, valore nullo",
        failed.restore[0]["denoise"] is False
        and failed.restore[0]["stabilize"] is False
        and failed.restore[0]["noise_dbfs"] is None
        and failed.restore[0]["jitter"] is None,
        failed.restore[0]["declared"],
    )
    check(
        "analisi fallita: il motivo viaggia nella dichiarazione",
        all(
            str(item.get("detail", "")).startswith("analysis_failed")
            for item in failed.not_applied
            if item.get("reason")
            in ("denoise_not_justified", "stabilize_not_justified")
        )
        and len(
            [
                item
                for item in failed.not_applied
                if item.get("reason")
                in ("denoise_not_justified", "stabilize_not_justified")
            ]
        )
        == 2,
        reasons(failed),
    )
    check(
        "analisi fallita: grafo identico al default",
        graph(failed_plan, failed) == default_graph(failed_plan),
    )

    # -- off by default ---------------------------------------------------- #
    _plain_plan, plain = build(analysis(), motion_none=True)
    check("senza motion il gate resta spento", plain.restore == [])
    check(
        "restore vuoto non tocca il grafo",
        render.build_edl_filtergraph(plan(), {"c01": 0}, has_audio=True, restore={})
        == base_graph,
    )

    # -- hostile inputs ---------------------------------------------------- #
    try:
        render.build_edl_filtergraph(
            plan(),
            {"c01": 0},
            has_audio=True,
            restore={"v0_0": {"denoise": True, "stabilize": False}},
        )
    except render.RenderFailure as exc:
        check("denoise senza filtro rifiutato", True, str(exc)[:48])
    else:
        check("denoise senza filtro rifiutato", False, "nessuna eccezione")
    try:
        render.build_edl_filtergraph(
            plan(),
            {"c01": 0},
            has_audio=True,
            restore={"v0_0": {"denoise": "aecho=1", "stabilize": False}},
        )
    except render.RenderFailure as exc:
        check("filtro fuori whitelist rifiutato", True, str(exc)[:48])
    else:
        check("filtro fuori whitelist rifiutato", False, "nessuna eccezione")

    check(
        "render_edl accetta restore con default None",
        inspect.signature(render.render_edl).parameters["restore"].default is None,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
