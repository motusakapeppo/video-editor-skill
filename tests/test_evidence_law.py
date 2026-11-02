"""Evidence law: every enabled treatment proves itself with source-time spans.

python tests/test_evidence_law.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


# --------------------------------------------------------------------------- #
# contract fixtures
# --------------------------------------------------------------------------- #


def _finding(fid: str, span=None, stale: bool = False) -> dict:
    finding = {
        "id": fid,
        "severity": "warn",
        "metric": "m",
        "value": 1,
        "threshold": 0,
        "evidence": "e",
        "suggested_treatment": None,
    }
    if span is not None:
        finding["span"] = span
    if stale:
        finding["stale"] = True
    return finding


def _witness(fid: str, t0, t1) -> dict:
    return {"finding_id": fid, "t0": t0, "t1": t1}


def _treatment(name: str, finding_id: str, evidence, enabled: bool = True) -> dict:
    treatment = {
        "name": name,
        "kind": "technical",
        "enabled": enabled,
        "params": {},
        "reason": {"finding_id": finding_id, "explain": "x"},
    }
    if evidence is not None:
        treatment["evidence"] = evidence
    return treatment


def _plan(treatments: list, cuts: list | None = None) -> dict:
    return {
        "output": {"aspect": "9:16"},
        "treatments": treatments,
        "cuts": cuts or [],
    }


# --------------------------------------------------------------------------- #
# contract matrix
# --------------------------------------------------------------------------- #


def contract_matrix() -> None:
    a = _finding("a", [0.0, 10.0])
    b = _finding("b", [0.0, 10.0])
    real = _finding("audio.loudness.off", [0.0, 10.0])
    findings = [a, b, real]

    p = _plan([_treatment("loudnorm", "a", None)])
    check(
        "missing evidence rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )
    check(
        "missing evidence rejected by shape",
        bool(contract.validate_evidence_shape(p)),
    )

    p = _plan([_treatment("loudnorm", "a", [])])
    check(
        "empty evidence rejected", bool(contract.validate_evidence(p, findings, 10.0))
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("b", 0.0, 10.0)])])
    check(
        "evidence[0] id mismatch rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    p = _plan([_treatment("loudnorm", "ghost", [_witness("ghost", 0.0, 10.0)])])
    check(
        "unknown finding rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    stale = _finding("s", [0.0, 10.0], stale=True)
    p = _plan([_treatment("loudnorm", "s", [_witness("s", 0.0, 10.0)])])
    check(
        "stale finding with plausible span rejected",
        bool(contract.validate_evidence(p, [stale], 10.0)),
    )

    nospan = _finding("n")
    p = _plan([_treatment("loudnorm", "n", [_witness("n", 0.0, 10.0)])])
    check(
        "finding without span rejected",
        bool(contract.validate_evidence(p, [nospan], 10.0)),
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("a", "x", 10.0)])])
    check(
        "malformed witness rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    badspan = _finding("bad", ["x", "y"])
    p = _plan([_treatment("loudnorm", "bad", [_witness("bad", 0.0, 10.0)])])
    check(
        "malformed finding span rejected",
        bool(contract.validate_evidence(p, [badspan], 10.0)),
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("a", 5.0, 2.0)])])
    check("t0 > t1 rejected", bool(contract.validate_evidence(p, findings, 10.0)))

    p = _plan([_treatment("loudnorm", "a", [_witness("a", -1.0, 11.0)])])
    check(
        "outside span both sides rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("a", 2.0, 8.0)])])
    check(
        "narrowed interval rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    # A finding span is timeline time. When the source needs normalising, the
    # timeline is the staging file and can be a few frames shorter than the
    # container; a span that reached the container's end made the gate refuse a
    # real run ("witness outside 0..14.9937"). `_timeline_duration` is the bound.
    whole = _finding("video.jitter.shaky", [0.0, 15.026])
    whole_report = {
        "source": {"duration": 15.026, "analysis_duration": 14.9937},
        "analysis": {"video": {"jitter": {"score": 1.92, "headroom_pct": 44}}},
    }
    clamped = plan.detect_findings(whole_report)
    jitter = next((f for f in clamped if f.get("id") == "video.jitter.shaky"), {})
    check(
        "a whole-clip finding stops at the analysed timeline",
        jitter.get("span") == [0.0, 14.9937],
        jitter.get("span"),
    )
    whole_plan = _plan(
        [
            _treatment(
                "stabilize",
                "video.jitter.shaky",
                [_witness("video.jitter.shaky", 0.0, 14.9937)],
            )
        ]
    )
    check(
        "and its witness passes the gate",
        not contract.validate_plan(whole_plan, {"source": whole_report["source"], "findings": [jitter]}),
        ",".join(contract.validate_plan(whole_plan, {"source": whole_report["source"], "findings": [jitter]})),
    )

    da = _finding("audio.dead_air.excess", [0.0, 2.0])
    fr = _finding("video.freeze.static", [8.0, 10.0])
    cuts = [{"in": 0.0, "out": 4.0}, {"in": 6.0, "out": 10.0}]
    p = _plan(
        [
            _treatment(
                "cut_silence",
                "audio.dead_air.excess",
                [
                    _witness("audio.dead_air.excess", 0.0, 2.0),
                    _witness("video.freeze.static", 8.0, 10.0),
                ],
            )
        ],
        cuts,
    )
    check(
        "cut outside witness union rejected",
        bool(contract.validate_evidence(p, [da, fr], 10.0)),
    )

    cuts = [{"in": 0.0, "out": 1.0}, {"in": 3.0, "out": 10.0}]
    p = _plan(
        [
            _treatment(
                "cut_silence",
                "audio.dead_air.excess",
                [_witness("audio.dead_air.excess", 0.0, 4.0)],
            )
        ],
        cuts,
    )
    check(
        "cut inside witness union accepted",
        contract.validate_evidence(
            p, [_finding("audio.dead_air.excess", [0.0, 4.0])], 10.0
        )
        == [],
    )

    p = _plan(
        [
            _treatment(
                "loudnorm",
                "a",
                [_witness("a", 0.0, 10.0), _witness("b", 0.0, 10.0)],
            )
        ]
    )
    check(
        "loudnorm cannot use two witnesses",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    z = _finding("z", [5.0, 5.0])
    p = _plan([_treatment("loudnorm", "z", [_witness("z", 5.0, 5.0)])])
    check(
        "zero-length witness rejected",
        bool(contract.validate_evidence(p, [z], 10.0)),
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("a", True, 10.0)])])
    check(
        "boolean witness rejected",
        bool(contract.validate_evidence(p, findings, 10.0)),
    )

    p = _plan(
        [
            _treatment(
                "cut_silence",
                "audio.dead_air.excess",
                [_witness("audio.dead_air.excess", 0.0, 4.0)],
            )
        ],
        [{"in": 0.0, "out": 1.0}, {"in": 3.0, "out": 10.0}],
    )
    check(
        "cut_silence without duration rejected",
        bool(
            contract.validate_evidence(
                p, [_finding("audio.dead_air.excess", [0.0, 4.0])], None
            )
        ),
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("a", 0.0, 12.0)])])
    check(
        "witness beyond duration rejected",
        bool(contract.validate_evidence(p, [_finding("a", [0.0, 12.0])], 10.0)),
    )

    p = _plan(
        [
            _treatment(
                "loudnorm",
                "audio.loudness.off",
                [_witness("audio.loudness.off", 0.0, 10.0)],
            )
        ]
    )
    check(
        "valid evidence accepted",
        contract.validate_evidence(p, findings, 10.0) == [],
    )
    check(
        "valid shape accepted",
        contract.validate_evidence_shape(p) == [],
    )

    p = _plan([_treatment("loudnorm", "a", [_witness("a", None, None)], enabled=False)])
    check(
        "disabled treatment may carry null witnesses",
        contract.validate_evidence_shape(p) == [],
    )

    report = {"source": {"duration": 10.0}, "findings": [stale]}
    p = _plan([_treatment("loudnorm", "s", [_witness("s", 0.0, 10.0)])])
    check(
        "validate_plan rejects stale evidence",
        bool(contract.validate_plan(p, report)),
    )

    report = {"source": {"duration": 10.0}, "findings": [real]}
    p = _plan(
        [
            _treatment(
                "loudnorm",
                "audio.loudness.off",
                [_witness("audio.loudness.off", 0.0, 10.0)],
            )
        ]
    )
    check(
        "validate_plan accepts valid evidence",
        contract.validate_plan(p, report) == [],
    )


# --------------------------------------------------------------------------- #
# plan fixtures
# --------------------------------------------------------------------------- #


def _report(**analysis: dict) -> dict:
    report: dict = {
        "source": {
            "width": 1080,
            "height": 1920,
            "duration": 12.0,
            "has_audio": True,
        }
    }
    if analysis:
        report["analysis"] = analysis
    return report


def _brief(**extra: object) -> dict:
    brief: dict = {"format": "9:16", "hook": "none", "subtitles": "auto"}
    brief.update(extra)
    return brief


def _faces_report(width: int = 1920, height: int = 1080, **faces: object) -> dict:
    return {
        "source": {
            "width": width,
            "height": height,
            "duration": 12.0,
            "has_audio": True,
        },
        "analysis": {"faces": faces},
    }


def _ids(report: dict) -> list:
    return [f.get("id") for f in report.get("findings") or [] if isinstance(f, dict)]


def _treatment_named(built, name: str):
    return next((t for t in built.treatments if t.get("name") == name), None)


# --------------------------------------------------------------------------- #
# plan matrix
# --------------------------------------------------------------------------- #


def plan_matrix() -> None:
    report = _report(
        video={
            "jitter": {"score": 0.5},
            "exposure": {"mean_luma": 20.0},
            "color": {"cast_rg": 20.0},
            "freezes": [
                {"start": 2.0, "end": 4.0, "duration": 2.0},
                {"start": 8.0, "end": 9.5, "duration": 1.5},
            ],
            "pacing": {"mean_shot_len": 5.0},
            "motion": {"mean": 0.5},
            "scene_cuts": [1.0, 2.0],
            "retention": {"hook_candidates": []},
        },
        audio={
            "snr_db": 5.0,
            "dead_air": [
                {"start": 1.0, "end": 2.0},
                {"start": 5.0, "end": 7.0},
            ],
            "clipping_pct": 0.02,
            "loudness": {"input_i": -20.0, "input_lra": 15.0},
        },
        faces={
            "detected_ratio": 0.8,
            "max_concurrent": 2,
            "span_x_ratio": 0.8,
            "tracks": [{"persistence": 0.8}],
        },
        speech={"word_count": 40, "wpm": 200, "fillers": 5},
    )
    findings = plan.detect_findings(report, _brief())
    fresh = {f["id"]: f for f in findings}

    check(
        "every fresh finding has a span",
        all(isinstance(f.get("span"), list) and len(f["span"]) == 2 for f in findings),
        [(f["id"], f.get("span")) for f in findings],
    )
    check(
        "every span is numeric and ordered",
        all(
            isinstance(f["span"][0], (int, float))
            and isinstance(f["span"][1], (int, float))
            and f["span"][0] <= f["span"][1]
            for f in findings
        ),
    )

    jitter = fresh["video.jitter.shaky"]
    check(
        "global default span [0, duration]",
        jitter["span"] == [0.0, 12.0],
        jitter["span"],
    )

    dead = fresh["audio.dead_air.excess"]
    check("dead-air hull", dead["span"] == [1.0, 7.0], dead["span"])

    freeze = fresh["video.freeze.static"]
    check("freeze hull", freeze["span"] == [2.0, 9.5], freeze["span"])

    hook = fresh["hook.missing"]
    check("hook span [0, 4]", hook["span"] == [0.0, 4.0], hook["span"])

    built = plan.build_plan(report, _brief())
    enabled = [t for t in built.treatments if t.get("enabled")]
    check(
        "every enabled treatment has evidence",
        all(isinstance(t.get("evidence"), list) and t["evidence"] for t in enabled),
        [(t["name"], t.get("evidence")) for t in enabled],
    )
    check(
        "evidence[0] matches reason.finding_id",
        all(
            t["evidence"][0]["finding_id"] == t["reason"]["finding_id"] for t in enabled
        ),
    )
    check(
        "evidence copies the finding span",
        all(
            t["evidence"][0]["t0"] == fresh[t["reason"]["finding_id"]]["span"][0]
            and t["evidence"][0]["t1"] == fresh[t["reason"]["finding_id"]]["span"][1]
            for t in enabled
        ),
    )
    check(
        "plan validates",
        plan.validate(built, report) == [],
        plan.validate(built, report),
    )

    cut = _treatment_named(built, "cut_silence")
    cut_ids = [w["finding_id"] for w in (cut or {}).get("evidence") or []]
    check(
        "cut_silence two witnesses when both exist",
        cut_ids == ["audio.dead_air.excess", "video.freeze.static"],
        cut_ids,
    )

    # a freeze beyond the dead-air hull is still covered
    report2 = _report(
        video={"freezes": [{"start": 8.0, "end": 10.0, "duration": 2.0}]},
        audio={"dead_air": [{"start": 1.0, "end": 2.0}]},
    )
    built2 = plan.build_plan(report2, _brief())
    cut2 = _treatment_named(built2, "cut_silence")
    spans = [
        (w["finding_id"], w["t0"], w["t1"]) for w in (cut2 or {}).get("evidence") or []
    ]
    check(
        "freeze beyond dead-air hull is covered",
        ("video.freeze.static", 8.0, 10.0) in spans,
        spans,
    )
    check(
        "freeze beyond dead-air hull validates",
        plan.validate(built2, report2) == [],
        plan.validate(built2, report2),
    )

    # carried findings are stale, fresh findings are not
    carried = _report()
    carried["findings"] = [
        {"id": "old.finding", "severity": "info", "span": [0.0, 1.0]}
    ]
    plan.build_plan(carried, _brief())
    old = next(f for f in carried["findings"] if f["id"] == "old.finding")
    check("carried finding marked stale", old.get("stale") is True, old)
    check(
        "fresh findings never carry stale",
        all(
            not f.get("stale") for f in carried["findings"] if f["id"] != "old.finding"
        ),
    )

    # stale freeze + fresh dead air: only the fresh witness is used
    mixed = _report(audio={"dead_air": [{"start": 1.0, "end": 2.0}]})
    mixed["findings"] = [
        {
            "id": "video.freeze.static",
            "severity": "warn",
            "metric": "freeze.duration",
            "value": 2.0,
            "threshold": 1.5,
            "evidence": "stale",
            "suggested_treatment": "cut_silence",
            "span": [8.0, 10.0],
        }
    ]
    built_mixed = plan.build_plan(mixed, _brief())
    cut_mixed = _treatment_named(built_mixed, "cut_silence")
    mixed_ids = [w["finding_id"] for w in (cut_mixed or {}).get("evidence") or []]
    check(
        "stale freeze + fresh dead air: one witness",
        mixed_ids == ["audio.dead_air.excess"],
        mixed_ids,
    )
    check(
        "stale freeze + fresh dead air: plan valid",
        plan.validate(built_mixed, mixed) == [],
        plan.validate(built_mixed, mixed),
    )

    # only stale cut findings: no cut_silence treatment at all
    stale_only = _report()
    stale_only["findings"] = [
        {
            "id": "audio.dead_air.excess",
            "severity": "warn",
            "metric": "audio.dead_air_ratio",
            "value": 0.2,
            "threshold": 0.08,
            "evidence": "stale",
            "suggested_treatment": "cut_silence",
            "span": [1.0, 2.0],
        },
        {
            "id": "video.freeze.static",
            "severity": "warn",
            "metric": "freeze.duration",
            "value": 2.0,
            "threshold": 1.5,
            "evidence": "stale",
            "suggested_treatment": "cut_silence",
            "span": [8.0, 10.0],
        },
    ]
    built_stale = plan.build_plan(stale_only, _brief())
    check(
        "only stale cut findings: no cut_silence",
        _treatment_named(built_stale, "cut_silence") is None,
        [t["name"] for t in built_stale.treatments],
    )
    check(
        "only stale cut findings: plan valid",
        plan.validate(built_stale, stale_only) == [],
        plan.validate(built_stale, stale_only),
    )


# --------------------------------------------------------------------------- #
# faces matrix
# --------------------------------------------------------------------------- #


def faces_matrix() -> None:
    report = _faces_report(
        detected_ratio=0.8,
        max_concurrent=2,
        span_x_ratio=0.8,
        tracks=[{"persistence": 0.8}],
    )
    built = plan.build_plan(report, _brief())
    ids = _ids(report)
    check("multi_concurrent emitted", "faces.multi_concurrent" in ids, ids)
    check("multi_span not emitted", "faces.multi_span" not in ids, ids)
    autoframe = _treatment_named(built, "autoframe")
    check("autoframe enabled", bool(autoframe and autoframe.get("enabled")), autoframe)
    check(
        "autoframe mode fit",
        (autoframe or {}).get("params", {}).get("mode") == "fit",
        (autoframe or {}).get("params"),
    )
    explain = str(((autoframe or {}).get("reason") or {}).get("explain") or "")
    check("autoframe explain due volti", "due volti in scena" in explain, explain)

    report = _faces_report(
        detected_ratio=0.8,
        max_concurrent=2,
        span_x_ratio=1.2,
        tracks=[{"persistence": 0.8}],
    )
    built = plan.build_plan(report, _brief())
    ids = _ids(report)
    check("span 1.2 -> multi_span", "faces.multi_span" in ids, ids)
    check("span 1.2 -> no multi_concurrent", "faces.multi_concurrent" not in ids, ids)
    autoframe = _treatment_named(built, "autoframe")
    check(
        "multi_span autoframe mode fit",
        (autoframe or {}).get("params", {}).get("mode") == "fit",
        (autoframe or {}).get("params"),
    )

    report = _faces_report(
        detected_ratio=0.8,
        max_concurrent=1,
        span_x_ratio=0.8,
        tracks=[{"persistence": 0.8}],
    )
    plan.build_plan(report, _brief())
    ids = _ids(report)
    check("concurrent 1 -> single", "faces.single" in ids, ids)
    check(
        "concurrent 1 -> no multi_concurrent", "faces.multi_concurrent" not in ids, ids
    )

    report = _faces_report(
        detected_ratio=0.8,
        max_concurrent=2,
        span_x_ratio=1.2,
        tracks=[{"persistence": 0.8}],
    )
    plan.build_plan(report, _brief())
    ids = _ids(report)
    check(
        "multi_span and multi_concurrent never both emit",
        "faces.multi_span" in ids and "faces.multi_concurrent" not in ids,
        ids,
    )

    order = plan.FACE_PRIORITY
    check(
        "FACE_PRIORITY order",
        order.index("faces.multi_span")
        < order.index("faces.multi_concurrent")
        < order.index("faces.track_broken"),
        order,
    )

    report = _faces_report(
        width=400,
        height=2000,
        detected_ratio=0.8,
        max_concurrent=2,
        span_x_ratio=0.8,
        tracks=[{"persistence": 0.8}],
    )
    built = plan.build_plan(report, _brief())
    autoframe = _treatment_named(built, "autoframe")
    check(
        "crop_h/src_h < 0.4 veto disables autoframe",
        bool(autoframe) and autoframe.get("enabled") is False,
        autoframe,
    )
    explain = str(((autoframe or {}).get("reason") or {}).get("explain") or "")
    check("crop veto explain", "oltre il 60%" in explain, explain)


def main() -> int:
    print("contract: evidence matrix")
    contract_matrix()
    print("\nplan: spans and evidence")
    plan_matrix()
    print("\nfaces: multi_concurrent")
    faces_matrix()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
