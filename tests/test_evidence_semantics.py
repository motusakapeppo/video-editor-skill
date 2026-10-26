"""2.10b: the treatment -> finding allowlist is real, frozen and enforced."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import contract, gates  # noqa: E402

PASS = 0
FAIL = 0
DUR = 10.0

FINDINGS = [
    {"id": fid, "span": [0.0, DUR], "severity": "info"}
    for fid in (
        "audio.clipping",
        "audio.noise.high",
        "audio.loudness.off",
        "audio.lra.wide",
        "audio.silent",
        "video.jitter.shaky",
        "video.exposure.under",
        "video.exposure.high_clip",
        "video.color.cast",
        "video.pacing.drag",
        "video.scene.cuts",
        "faces.none",
        "faces.single",
        "faces.multi_span",
        "faces.multi_concurrent",
        "faces.track_broken",
        "speech.present",
        "speech.filler",
    )
] + [
    {"id": "audio.dead_air.excess", "span": [1.0, 2.0], "severity": "warn"},
    {"id": "video.freeze.static", "span": [3.0, 4.0], "severity": "warn"},
    {"id": "hook.missing", "span": [0.0, 4.0], "severity": "info"},
    {"id": "video.hook.weak", "span": [0.0, 4.0], "severity": "warn"},
]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:58} {detail}")


def witness(fid: str) -> dict:
    span = next(f["span"] for f in FINDINGS if f["id"] == fid)
    return {"finding_id": fid, "t0": span[0], "t1": span[1]}


def treatment(name: str, fid: str, witnesses: list | None = None) -> dict:
    return {
        "name": name,
        "kind": "technical",
        "enabled": True,
        "params": {},
        "reason": {"finding_id": fid, "explain": "test"},
        "evidence": witnesses or [witness(fid)],
    }


def plan(treatments: list, cuts: list | None = None) -> dict:
    return {
        "output": {"aspect": "9:16"},
        "treatments": treatments,
        "cuts": cuts or [{"in": 0.0, "out": DUR}],
    }


def errors(p: dict) -> list:
    return contract.validate_evidence(p, FINDINGS, DUR)


def semantic(p: dict) -> list:
    return [e for e in errors(p) if "INVALID_EVIDENCE_SEMANTICS" in e]


# -- every allowed pair passes ------------------------------------------------ #
allowed = [
    ("denoise", "audio.noise.high"),
    ("loudnorm", "audio.loudness.off"),
    ("compress", "audio.lra.wide"),
    ("audio_restore", "audio.silent"),
    ("stabilize", "video.jitter.shaky"),
    ("exposure_fix", "video.exposure.under"),
    ("exposure_fix", "video.exposure.high_clip"),
    ("color_fix", "video.color.cast"),
    ("pacing_fix", "video.pacing.drag"),
    ("autoframe", "faces.single"),
    ("autoframe", "faces.multi_concurrent"),
    ("hook_overlay", "video.hook.weak"),
    ("zoom", "video.pacing.drag"),
    ("fit", "faces.multi_concurrent"),
    ("subtitles", "speech.present"),
    ("filler_cut", "speech.filler"),
]
for name, fid in allowed:
    check(f"allowed {name} <- {fid}", semantic(plan([treatment(name, fid)])) == [])

cut_ok = plan(
    [
        treatment(
            "cut_silence",
            "audio.dead_air.excess",
            [witness("audio.dead_air.excess"), witness("video.freeze.static")],
        )
    ],
    cuts=[
        {"in": 0.0, "out": 1.0},
        {"in": 2.0, "out": 3.0},
        {"in": 4.0, "out": DUR},
    ],
)
check("allowed cut_silence with two witnesses", semantic(cut_ok) == [])

# -- every rejected pair is blocked with the semantic code --------------------- #
rejected = [
    ("cut_silence", "audio.loudness.off"),
    ("denoise", "faces.single"),
    ("loudnorm", "video.freeze.static"),
    ("compress", "audio.dead_air.excess"),
    ("stabilize", "video.scene.cuts"),
    ("exposure_fix", "video.color.cast"),
    ("color_fix", "video.exposure.under"),
    ("autoframe", "audio.noise.high"),
    ("hook_overlay", "video.scene.cuts"),
    ("hook_overlay", "hook.missing"),
    ("zoom", "video.scene.cuts"),
    ("fit", "faces.single"),
    ("subtitles", "speech.filler"),
]
for name, fid in rejected:
    found = semantic(plan([treatment(name, fid)]))
    check(
        f"rejected {name} <- {fid}",
        any("INVALID_EVIDENCE_SEMANTICS" in e for e in found),
        found,
    )

ghost = treatment("ghost_treatment", "audio.noise.high")
check(
    "unknown treatment has no evidence policy",
    any("no evidence policy" in e for e in semantic(plan([ghost]))),
    semantic(plan([ghost])),
)

mixed = plan(
    [
        treatment(
            "cut_silence",
            "audio.dead_air.excess",
            [witness("audio.dead_air.excess"), witness("audio.loudness.off")],
        )
    ],
    cuts=[{"in": 0.0, "out": 1.0}, {"in": 2.0, "out": DUR}],
)
check(
    "mixed cut_silence witness: the bad one is named",
    any("cannot cite finding audio.loudness.off" in e for e in semantic(mixed)),
    semantic(mixed),
)

# -- the map is frozen and covers the real vocabulary ------------------------- #
try:
    contract.ALLOWED_EVIDENCE_MAP["denoise"] = frozenset({"audio.loudness.off"})
    check("map is immutable", False, "assignment succeeded")
except TypeError:
    check("map is immutable", True, "TypeError")
except Exception as exc:  # noqa: BLE001 - any mutation must be refused
    check("map is immutable", False, repr(exc))

expected = {
    "clipping_fix",
    "denoise",
    "loudnorm",
    "compress",
    "audio_restore",
    "stabilize",
    "exposure_fix",
    "color_fix",
    "pacing_fix",
    "autoframe",
    "hook_overlay",
    "zoom",
    "fit",
    "subtitles",
    "filler_cut",
    "cut_silence",
}
check(
    "map covers all 16 treatments",
    set(contract.ALLOWED_EVIDENCE_MAP) == expected,
    sorted(expected - set(contract.ALLOWED_EVIDENCE_MAP)),
)

# -- pre_render inherits the semantic gate ------------------------------------ #
bad = contract.EditPlan(
    output={"aspect": "9:16"},
    treatments=[treatment("denoise", "faces.single")],
    cuts=[{"in": 0.0, "out": DUR}],
)
try:
    gates.pre_render(bad, {"duration": DUR, "fps": 30.0}, findings=FINDINGS)
    check("pre_render inherits the semantic gate", False, "no exception")
except contract.QCFailure as exc:
    payload = getattr(exc, "result", {})
    check(
        "pre_render inherits the semantic gate",
        "INVALID_EVIDENCE_SEMANTICS" in str(payload),
        payload.get("failures"),
    )

print(f"\n{PASS} PASS / {FAIL} FAIL")
raise SystemExit(0 if FAIL == 0 else 1)
