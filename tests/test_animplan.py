"""Test dell'anim plan: il transient ratio misurato sceglie fra tre effetti eseguiti.

Le asserzioni sono sul contratto: driver, valore, provenienza, confidenza,
inazione, conteggio. La regressione sul Gate 2 verifica che il frammento
prodotto da ``chain_for_shot`` possa essere iniettato in un grafo EDL senza
rompere il cancello statico. Il test dei frame esegue davvero ``fade``, ``wipe``
e ``punch`` su una clip del corpus e pretende che il numero di frame non cambi.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import (  # noqa: E402
    animplan,
    contract,
    edl as edl_mod,
    ffmpeg as ff,
    gates,
    probe,
    registry,
    subs,
)

CORPUS = Path(os.environ.get("VX_CORPUS", str(ROOT / "tests" / "stock_corpus")))
WORK = ROOT / "tests" / "_out"
PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:46} {reason}")


def run_effect(src: Path, fragment: str, out: Path) -> object:
    return ff.run_ff(
        [
            "-i",
            str(src),
            "-vf",
            fragment,
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "26",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        loglevel="error",
    )


def main() -> int:
    thresholds = registry.THRESHOLDS["animation"]
    p33 = float(thresholds["p33"])
    p67 = float(thresholds["p67"])
    print(f"soglie animation: p33={p33} p67={p67} ({thresholds['provenance']})")
    print("campione: n=3 di 9 clip (solo clip con parole), direzione asserita")

    print("\ntransient ratio: la formula W0, ricomputata e confrontata col fixture")
    stats = json.loads(
        (ROOT / "tests" / "fixtures" / "corpus_stats.json").read_text(encoding="utf-8")
    )
    word_clips = [clip for clip in stats["clips"] if clip.get("words")]
    check("tre clip con parole", len(word_clips) == 3, str(len(word_clips)))
    for clip in word_clips:
        path = CORPUS / clip["path"]
        if not path.is_file():
            skip(
                f"{clip['name']}: transient == stored",
                f"corpus assente: {clip['path']}",
            )
            continue
        transcript = subs.transcribe(str(path))
        if transcript.skipped_reason or not transcript.words:
            skip(
                f"{clip['name']}: transient == stored",
                transcript.skipped_reason or "nessuna parola trascritta",
            )
            continue
        got = animplan.transient(path, transcript.words)
        stored = clip["transient_ratio"]
        check(
            f"{clip['name']}: transient == stored",
            got is not None and abs(float(got) - float(stored)) <= 1e-6,
            f"{got} vs {stored}",
        )

    print("\nmappatura del driver: fade / wipe / punch")
    fade = animplan.decide(p33 - 1.0)
    check("sotto p33 -> fade", fade["name"] == "fade", fade["name"])
    check("fade applied", fade["applied"] is True, f"conf {fade['confidence']}")
    check("fade count 1", fade["count"] == 1, str(fade["count"]))
    check("driver transient_ratio", fade["driver"] == "transient_ratio", fade["driver"])
    check("valore = ratio", fade["value"] == p33 - 1.0, str(fade["value"]))
    check(
        "provenienza corpus_hash",
        fade["provenance"].startswith("corpus_hash:"),
        fade["provenance"][:24],
    )
    check(
        "direction_basis asserted",
        fade["direction_basis"] == "asserted",
        fade["direction_basis"],
    )

    wipe = animplan.decide((p33 + p67) / 2.0)
    check("in banda -> wipe", wipe["name"] == "wipe", wipe["name"])
    check("wipe applied", wipe["applied"] is True, f"conf {wipe['confidence']}")

    punch = animplan.decide(p67 + 1.0)
    check("sopra p67 -> punch", punch["name"] == "punch", punch["name"])
    check("punch applied", punch["applied"] is True, f"conf {punch['confidence']}")

    print("\nconteggio: 1 normalmente, 2 solo punch con onset > 3.5, mai oltre 2")
    fast = animplan.decide(p67 + 1.0, onsets_per_s=4.0)
    check("punch + onset 4.0 -> count 2", fast["count"] == 2, str(fast["count"]))
    slow = animplan.decide(p67 + 1.0, onsets_per_s=3.0)
    check("punch + onset 3.0 -> count 1", slow["count"] == 1, str(slow["count"]))
    no_rate = animplan.decide(p67 + 1.0)
    check("punch senza onset -> count 1", no_rate["count"] == 1, str(no_rate["count"]))
    fade_fast = animplan.decide(p33 - 1.0, onsets_per_s=9.0)
    check(
        "fade + onset 9.0 -> count 1", fade_fast["count"] == 1, str(fade_fast["count"])
    )
    check(
        "count mai oltre 2",
        all(
            choice["count"] <= 2
            for choice in (fade, wipe, punch, fast, slow, no_rate, fade_fast)
        ),
        "max 2",
    )

    print("\nincertezza: inazione")
    uncertain = animplan.decide(p33 + 0.05)
    check("vicino a p33 -> none", uncertain["name"] == "none", uncertain["name"])
    check("applied False", uncertain["applied"] is False, str(uncertain["applied"]))
    check("count 0", uncertain["count"] == 0, str(uncertain["count"]))
    check(
        "confidenza in [0.2, 0.8]",
        0.2 <= uncertain["confidence"] <= 0.8,
        str(uncertain["confidence"]),
    )
    check("frase di inazione", bool(uncertain["inaction"]), uncertain["inaction"])

    missing = animplan.decide(None)
    check("ratio None -> none", missing["name"] == "none", missing["name"])
    check(
        "ratio None -> applied False",
        missing["applied"] is False,
        str(missing["applied"]),
    )
    check("ratio None -> count 0", missing["count"] == 0, str(missing["count"]))
    check("ratio None -> valore None", missing["value"] is None, str(missing["value"]))
    check("ratio None -> inazione", bool(missing["inaction"]), missing["inaction"])

    print("\nchain_for_shot")
    check("none -> stringa vuota", animplan.chain_for_shot({"name": "none"}) == "", "")
    fade_fragment = animplan.chain_for_shot(fade, {"duration": 2.0})
    check("fade contiene fade=t=in", "fade=t=in" in fade_fragment, fade_fragment)
    check("fade usa la durata", "d=2.0000" in fade_fragment, fade_fragment)
    wipe_fragment = animplan.chain_for_shot(wipe, {"duration": 2.0})
    check("wipe contiene geq", "geq=" in wipe_fragment, wipe_fragment[:48])
    check("wipe single-stream", "[" not in wipe_fragment, "nessun secondo input")
    punch_fragment = animplan.chain_for_shot(
        punch, {"duration": 0.25, "width": 1080, "height": 1350, "fps": 25.0}
    )
    check("punch contiene zoompan", "zoompan=" in punch_fragment, punch_fragment[:48])
    check("punch s=WxH", "s=1080x1350" in punch_fragment, punch_fragment)
    check(
        "nessuna virgola ai bordi",
        not fade_fragment.startswith(",") and not fade_fragment.endswith(","),
        f"{fade_fragment[:1]}...{fade_fragment[-1:]}",
    )

    print("\nregressione Gate 2: chain iniettata nel grafo EDL")
    shots = [
        edl_mod.EdlShot(
            label="a",
            src_in=0.0,
            src_out=2.0,
            duration=2.0,
            t_in=0.0,
            t_out=2.0,
            frames=50,
            cut_on="bar",
            contrast=1.5,
        ),
        edl_mod.EdlShot(
            label="b",
            src_in=2.0,
            src_out=4.0,
            duration=2.0,
            t_in=2.0,
            t_out=4.0,
            frames=50,
            cut_on="bar",
            contrast=1.5,
        ),
    ]
    edit = edl_mod.Edl(
        fps=25.0,
        shots=shots,
        total=4.0,
        frames=100,
        rhythmic_shots=2,
        inaction_shots=0,
    )
    graph = edl_mod.filtergraph(
        edit,
        {"a": 0, "b": 1},
        fps=25.0,
        video_extra=lambda shot: animplan.chain_for_shot(fade, shot),
    )
    check("chain presente nel grafo", "fade=t=in" in graph, graph[:60])
    try:
        gates.graph(graph)
        check("Gate 2 accetta il grafo", True, "trim + atrim + concat")
    except contract.QCFailure as exc:
        check("Gate 2 accetta il grafo", False, str(exc.result.get("checks")))

    print("\nframe: i tre effetti preservano numero di frame e geometria")
    src = CORPUS / "bright_45.mp4"
    if not src.is_file():
        skip("tre effetti: frame e geometria", f"corpus assente: {src.name}")
    else:
        WORK.mkdir(parents=True, exist_ok=True)
        info = probe.probe(str(src))
        shot = {
            "duration": 0.25,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
        }
        for name in ("fade", "wipe", "punch"):
            fragment = animplan.chain_for_shot({"name": name, "count": 1}, shot)
            out = WORK / f"animplan_{name}.mp4"
            proc = run_effect(src, fragment, out)
            check(f"{name}: ffmpeg rc=0", proc.rc == 0, proc.tail(2) if proc.rc else "")
            if proc.rc != 0:
                check(f"{name}: frame preservati", False, "ffmpeg fallito")
                check(f"{name}: geometria preservata", False, "ffmpeg fallito")
                continue
            got = probe.probe(str(out), count_frames=True)
            check(
                f"{name}: frame preservati",
                got.nb_frames == info.nb_frames,
                f"{got.nb_frames} vs {info.nb_frames}",
            )
            check(
                f"{name}: geometria preservata",
                (got.width, got.height) == (info.width, info.height),
                f"{got.width}x{got.height}",
            )

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
