"""Il referto di coerenza: cinque codici, ognuno con la sua prova.

Niente rete, niente render, niente ffmpeg: i piani sono dizionari sintetici e i
fotogrammi sono PNG scritti qui, cosi' ogni verdetto e' riproducibile.

    python tests/test_coherence.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, coherence  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


# --------------------------------------------------------------------------- #
# fotogrammi sintetici
# --------------------------------------------------------------------------- #


def _patterns(directory: Path) -> dict:
    """Cinque immagini strutturalmente diverse piu' i due casi inutilizzabili."""
    import cv2
    import numpy as np

    directory.mkdir(parents=True, exist_ok=True)
    out: dict = {}

    stripe = np.array([255] * 40 + [0] * 40, dtype=np.uint8)
    vertical = np.tile(stripe, (240, 4))[:, :320]
    out["vertical"] = directory / "vertical.png"
    cv2.imwrite(str(out["vertical"]), vertical)

    horizontal = np.tile(stripe.reshape(80, 1), (3, 320))[:240, :]
    out["horizontal"] = directory / "horizontal.png"
    cv2.imwrite(str(out["horizontal"]), horizontal)

    tile = np.kron([[255, 0], [0, 255]], np.ones((40, 40), dtype=np.uint8)).astype(np.uint8)
    checker = np.tile(tile, (3, 4))[:240, :320]
    out["checker"] = directory / "checker.png"
    cv2.imwrite(str(out["checker"]), checker)

    circle = np.zeros((240, 320), dtype=np.uint8)
    cv2.circle(circle, (160, 120), 70, 255, -1)
    out["circle"] = directory / "circle.png"
    cv2.imwrite(str(out["circle"]), circle)

    diagonal = np.zeros((240, 320), dtype=np.uint8)
    for row in range(240):
        diagonal[row, : min(320, row + 80)] = 255
    out["diagonal"] = directory / "diagonal.png"
    cv2.imwrite(str(out["diagonal"]), diagonal)

    black = directory / "black.png"
    cv2.imwrite(str(black), np.zeros((240, 320), dtype=np.uint8))
    out["black"] = black

    flat = directory / "flat.png"
    cv2.imwrite(str(flat), np.full((240, 320), 128, dtype=np.uint8))
    out["flat"] = flat
    return out


# --------------------------------------------------------------------------- #
# piani sintetici
# --------------------------------------------------------------------------- #


def _plan(shots, *, treatments=None) -> dict:
    """Un EDL minimo: solo la traccia video di layer 0 conta.

    ``shots`` e' una lista di ``(source_id, duration, provenance)``; la
    provenienza e' una lista di record o ``None``.
    """
    clips = []
    sources: dict = {}
    cursor = 0.0
    for index, (source_id, duration, provenance) in enumerate(shots):
        clip = {
            "clip_id": f"clip_{index:03d}",
            "source_id": source_id,
            "src_in": 0.0,
            "src_out": float(duration),
            "dst_in": round(cursor, 3),
            "dst_out": round(cursor + float(duration), 3),
            "layer": 0,
            "role": "a_roll",
        }
        if provenance is not None:
            clip["provenance"] = provenance
        clips.append(clip)
        sources[source_id] = {
            "path": "",
            "sha256": "0" * 64,
            "duration": float(duration),
        }
        cursor += float(duration)
    plan = {
        "timeline": {
            "fps": 30.0,
            "duration_sec": round(cursor, 3),
            "aspect_ratio": "9:16",
            "resolution": [1080, 1920],
        },
        "sources": sources,
        "tracks": {"video": [{"track_id": 0, "role": "a_roll", "clips": clips}]},
    }
    if treatments is not None:
        plan["treatments"] = treatments
    return plan


def _run(shots, frames, *, bridge=None, text_required=False, treatments=None) -> dict:
    return coherence.evaluate(
        _plan(shots, treatments=treatments),
        {},
        bridge=bridge,
        frames=frames,
        text_required=text_required,
    )


def _codes(report: dict) -> list:
    return [item.get("code") for item in report.get("violations") or []]


def _violation(report: dict, code: str):
    return next(
        (item for item in report.get("violations") or [] if item.get("code") == code),
        None,
    )


# --------------------------------------------------------------------------- #
# i test
# --------------------------------------------------------------------------- #


def test_healthy(pics: dict) -> None:
    print("\n-- piano sano")
    frames = [pics["vertical"], pics["horizontal"], pics["checker"], pics["circle"]]
    provenance = [{"kind": "beat"}]
    first = [{"kind": "hook"}, {"kind": "beat"}]
    shots = [
        ("a", 2.0, first),
        ("b", 2.0, provenance),
        ("c", 2.0, provenance),
        ("d", 2.0, provenance),
    ]
    report = _run(shots, frames)
    check("nessuna violazione", not report["violations"], report["violations"])
    check("ok vero", report["ok"] is True, report["ok"])
    check(
        "varieta' 1.0: quattro shot distinti",
        report["metrics"]["variety"] == 1.0,
        report["metrics"]["variety"],
    )
    check(
        "tre tagli su tre giustificati",
        report["metrics"]["justified_cuts"] == 3
        and report["metrics"]["unjustified_cuts"] == 0,
        (report["metrics"]["justified_cuts"], report["metrics"]["unjustified_cuts"]),
    )
    check(
        "apertura con hook",
        report["metrics"]["opening"] == "hook",
        report["metrics"]["opening"],
    )
    check(
        "provenienza dal piano",
        report["metrics"]["cut_provenance"] == "plan",
        report["metrics"]["cut_provenance"],
    )


def test_low_variety(pics: dict) -> None:
    print("\n-- varieta' bassa e ripetizione")
    frames = [
        pics["vertical"],
        pics["vertical"],
        pics["vertical"],
        pics["horizontal"],
        pics["horizontal"],
    ]
    beat = [{"kind": "beat"}]
    shots = [
        ("a", 2.0, [{"kind": "hook"}, {"kind": "beat"}]),
        ("a", 2.0, beat),
        ("a", 2.0, beat),
        ("b", 2.0, beat),
        ("b", 2.0, beat),
    ]
    report = _run(shots, frames)
    low = _violation(report, "low_variety")
    repeated = _violation(report, "repeated_shots")
    check("low_variety c'e'", low is not None, _codes(report))
    check("rapporto 0.4 sotto 0.5", low and low["metrics"]["ratio"] == 0.4, low)
    check("repeated_shots c'e'", repeated is not None, _codes(report))
    check(
        "ripetizione 0-2 con tre indici",
        repeated and repeated["metrics"]["indices"] == [0, 1, 2],
        repeated and repeated["metrics"],
    )
    check("ok falso", report["ok"] is False, report["ok"])


def test_repeat_without_low_variety(pics: dict) -> None:
    print("\n-- ripetizione senza varieta' bassa")
    frames = [
        pics["vertical"],
        pics["vertical"],
        pics["vertical"],
        pics["horizontal"],
    ]
    beat = [{"kind": "beat"}]
    shots = [
        ("a", 2.0, [{"kind": "hook"}, {"kind": "beat"}]),
        ("a", 2.0, beat),
        ("a", 2.0, beat),
        ("b", 2.0, beat),
    ]
    report = _run(shots, frames)
    check(
        "varieta' 0.5 non e' una violazione (soglia stretta)",
        "low_variety" not in _codes(report),
        _codes(report),
    )
    check(
        "tre identici di fila sono una ripetizione",
        "repeated_shots" in _codes(report),
        _codes(report),
    )


def test_black_frame(pics: dict) -> None:
    print("\n-- frame nero")
    frames = [pics["vertical"], pics["black"], pics["checker"]]
    beat = [{"kind": "beat"}]
    shots = [
        ("a", 2.0, [{"kind": "hook"}, {"kind": "beat"}]),
        ("b", 2.0, beat),
        ("c", 2.0, beat),
    ]
    report = _run(shots, frames)
    unusable = _violation(report, "unusable_frame")
    check("unusable_frame c'e'", unusable is not None, _codes(report))
    check(
        "e' l'indice 1, nero",
        unusable and unusable["metrics"]["index"] == 1
        and unusable["metrics"]["reason"] == "black",
        unusable and unusable["metrics"],
    )
    check(
        "luma misurata sotto 0.02",
        report["metrics"]["black_frames"]
        and report["metrics"]["black_frames"][0]["luma"] < coherence.BLACK_LUMA,
        report["metrics"]["black_frames"],
    )
    check("l'evidenza dice nero", "nero" in (unusable or {}).get("evidence", ""), unusable)


def test_sharpness_frame(pics: dict) -> None:
    print("\n-- frame mosso/uniforme")
    frames = [pics["vertical"], pics["horizontal"], pics["flat"]]
    beat = [{"kind": "beat"}]
    shots = [
        ("a", 2.0, [{"kind": "hook"}, {"kind": "beat"}]),
        ("b", 2.0, beat),
        ("c", 2.0, beat),
    ]
    report = _run(shots, frames)
    unusable = _violation(report, "unusable_frame")
    check("unusable_frame c'e'", unusable is not None, _codes(report))
    check(
        "motivo motion_blur, indice 2",
        unusable and unusable["metrics"]["reason"] == "motion_blur"
        and unusable["metrics"]["index"] == 2,
        unusable and unusable["metrics"],
    )
    check(
        "non e' scambiato per nero",
        report["metrics"]["black_frames"] == [],
        report["metrics"]["black_frames"],
    )


def test_unjustified_cut(pics: dict) -> None:
    print("\n-- taglio senza provenienza")
    frames = [pics["vertical"], pics["horizontal"], pics["checker"]]
    shots = [
        ("a", 2.0, [{"kind": "hook"}, {"kind": "beat"}]),
        ("b", 2.0, None),
        ("c", 2.0, None),
    ]
    report = _run(shots, frames)
    unjust = _violation(report, "unjustified_cut")
    check("unjustified_cut c'e'", unjust is not None, _codes(report))
    check(
        "un taglio su due senza ragione",
        unjust and unjust["metrics"]["count"] == 1
        and unjust["metrics"]["total"] == 2,
        unjust and unjust["metrics"],
    )
    check(
        "il taglio scoperto e' dopo lo shot 1",
        unjust and unjust["metrics"]["indices"] == [1],
        unjust and unjust["metrics"]["indices"],
    )
    check(
        "metriche coerenti",
        report["metrics"]["justified_cuts"] == 1
        and report["metrics"]["unjustified_cuts"] == 1,
        (report["metrics"]["justified_cuts"], report["metrics"]["unjustified_cuts"]),
    )


def test_unknown_provenance(pics: dict) -> None:
    print("\n-- piano vecchio: provenienza assente")
    frames = [pics["vertical"], pics["horizontal"], pics["checker"]]
    shots = [("a", 2.0, None), ("b", 2.0, None), ("c", 2.0, None)]
    report = _run(shots, frames)
    check(
        "cut_provenance e' unknown",
        report["metrics"]["cut_provenance"] == "unknown",
        report["metrics"]["cut_provenance"],
    )
    check(
        "nessun unjustified_cut inventato",
        "unjustified_cut" not in _codes(report),
        _codes(report),
    )
    check(
        "il motivo e' scritto",
        any(item.get("check") == "cuts" for item in report["metrics"]["unknown"]),
        report["metrics"]["unknown"],
    )
    check(
        "i tagli non giudicati restano None",
        report["metrics"]["unjustified_cuts"] is None,
        report["metrics"]["unjustified_cuts"],
    )


def test_weak_open(pics: dict) -> None:
    print("\n-- apertura senza motivo")
    frames = [pics["vertical"], pics["horizontal"]]
    beat = [{"kind": "beat"}]
    shots = [("a", 2.0, beat), ("b", 2.0, beat)]
    report = _run(shots, frames)
    check("weak_open c'e'", "weak_open" in _codes(report), _codes(report))
    check(
        "opening e' none",
        report["metrics"]["opening"] == "none",
        report["metrics"]["opening"],
    )


def test_text_open(pics: dict) -> None:
    print("\n-- apertura col primo verso")
    frames = [pics["vertical"], pics["horizontal"]]
    beat = [{"kind": "beat"}]
    shots = [("a", 4.0, beat), ("b", 2.0, beat)]
    bridge = {
        "captions": [{"dst_in": 0.3, "dst_out": 1.2, "text": "primo verso"}],
        "anchors": [],
        "cuts": [],
    }
    report = _run(shots, frames, bridge=bridge, text_required=True)
    check(
        "opening e' text",
        report["metrics"]["opening"] == "text",
        report["metrics"]["opening"],
    )
    check("niente weak_open", "weak_open" not in _codes(report), _codes(report))


def test_bridge_derived_cut(pics: dict) -> None:
    print("\n-- taglio giustificato da un'ancora del ponte")
    frames = [pics["vertical"], pics["horizontal"]]
    shots = [("a", 2.0, None), ("a", 2.0, None)]
    bridge = {
        "anchors": [{"kind": "beat", "source_id": "a", "t0": 2.0}],
        "cuts": [],
        "captions": [{"dst_in": 0.2, "dst_out": 1.0, "text": "via"}],
    }
    report = _run(shots, frames, bridge=bridge)
    check(
        "provenienza dal referto",
        report["metrics"]["cut_provenance"] == "report",
        report["metrics"]["cut_provenance"],
    )
    check(
        "il taglio sul beat e' giustificato",
        report["metrics"]["justified_cuts"] == 1
        and "unjustified_cut" not in _codes(report),
        report["metrics"],
    )


def test_silence_fragments(pics: dict) -> None:
    print("\n-- frammenti di silenzio: una sola inquadratura")
    frames = [pics["vertical"]]
    shots = [("a", 2.0, None), ("a", 2.0, None), ("a", 2.0, None)]
    bridge = {
        "anchors": [],
        "cuts": [
            {"t": 2.0, "reason": "dead_air"},
            {"t": 4.0, "reason": "dead_air"},
        ],
        "captions": [{"dst_in": 0.2, "dst_out": 1.0, "text": "via"}],
    }
    plan = _plan(shots)
    # I tre clip sono contigui nello stesso sorgente: 0-2, 2-4, 4-6.
    clips = plan["tracks"]["video"][0]["clips"]
    clips[0]["src_out"] = 2.0
    clips[1]["src_in"] = 2.0
    clips[1]["src_out"] = 4.0
    clips[2]["src_in"] = 4.0
    clips[2]["src_out"] = 6.0
    report = coherence.evaluate(plan, {}, bridge=bridge, frames=frames)
    check(
        "tre frammenti sono un solo shot",
        report["metrics"]["shots"] == 1 and report["metrics"]["clips"] == 3,
        (report["metrics"]["shots"], report["metrics"]["clips"]),
    )
    check(
        "nessuna ripetizione inventata",
        "repeated_shots" not in _codes(report)
        and "low_variety" not in _codes(report),
        _codes(report),
    )
    check(
        "i due tagli sul silenzio sono giustificati dal parlato",
        report["metrics"]["justified_cuts"] == 2
        and report["metrics"]["unjustified_cuts"] == 0,
        (report["metrics"]["justified_cuts"], report["metrics"]["unjustified_cuts"]),
    )


def test_frames_missing(pics: dict) -> None:
    print("\n-- fotogrammi mancanti: unknown, non violazione")
    frames = [pics["vertical"], None, pics["checker"], pics["circle"]]
    beat = [{"kind": "beat"}]
    shots = [
        ("a", 2.0, [{"kind": "hook"}, {"kind": "beat"}]),
        ("b", 2.0, beat),
        ("c", 2.0, beat),
        ("d", 2.0, beat),
    ]
    report = _run(shots, frames)
    check(
        "variety resta None",
        report["metrics"]["variety"] is None,
        report["metrics"]["variety"],
    )
    check("niente low_variety inventato", "low_variety" not in _codes(report), _codes(report))
    check(
        "il motivo e' dichiarato",
        any(item.get("check") == "frames" for item in report["metrics"]["unknown"]),
        report["metrics"]["unknown"],
    )


def test_disabled() -> None:
    print("\n-- --free: spento e dichiarato")
    report = coherence.disabled("--free")
    check("disabled vero", report["disabled"] is True, report["disabled"])
    check("ok vero ma non pulito", report["ok"] is True and report["violations"] == [], report)
    check(
        "metrics.checked falso",
        report["metrics"]["checked"] is False,
        report["metrics"],
    )
    check("il motivo nomina il flag", "--free" in report["reason"], report["reason"])


def test_cli_flags() -> None:
    print("\n-- i flag della riga di comando")
    parser = cli.build_parser()
    plain = parser.parse_args(
        ["auto", "--prompt", "x", "--assets", ".", "--out", "out.mp4"]
    )
    free = parser.parse_args(
        ["auto", "--prompt", "x", "--assets", ".", "--out", "out.mp4", "--free"]
    )
    alias = parser.parse_args(
        [
            "auto",
            "--prompt",
            "x",
            "--assets",
            ".",
            "--out",
            "out.mp4",
            "--no-coherence",
        ]
    )
    check("default: controlli attivi", getattr(plain, "free", False) is False)
    check("--free esiste", getattr(free, "free", False) is True)
    check("--no-coherence esiste", getattr(alias, "no_coherence", False) is True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-coherence-") as tmp:
        pics = _patterns(Path(tmp))
        test_healthy(pics)
        test_low_variety(pics)
        test_repeat_without_low_variety(pics)
        test_black_frame(pics)
        test_sharpness_frame(pics)
        test_unjustified_cut(pics)
        test_unknown_provenance(pics)
        test_weak_open(pics)
        test_text_open(pics)
        test_bridge_derived_cut(pics)
        test_silence_fragments(pics)
        test_frames_missing(pics)
    test_disabled()
    test_cli_flags()
    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
