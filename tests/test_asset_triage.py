"""Asset triage: what enters the timeline and what is rejected, with reasons.

The gate is only trustworthy if every input is accounted for exactly once, a
rejection always carries its measured numbers, and a heuristic inside the
0.2..0.8 confidence band never rejects.

python tests/test_asset_triage.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage as at  # noqa: E402
from vx import contract, settings  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def _asset(asset_id: str, **kwargs) -> at.AssetInfo:
    payload = {
        "asset_id": asset_id,
        "kind": "video",
        "path": asset_id,
        "width": 1080,
        "height": 1920,
        "duration_sec": 5.0,
        "has_audio": True,
    }
    payload.update(kwargs)
    return at.AssetInfo(**payload)


def _ids(decisions) -> list:
    return [item.asset_id for item in decisions]


def _triage(assets, *, target=(1080, 1920), values=None, **kwargs):
    table = values or {}
    return at.triage_assets(assets, target, measure=lambda asset: dict(table), **kwargs)


def main() -> int:
    # -- geometric gate: exact boundary ------------------------------------ #
    square = _asset("a.mp4", width=100, height=100)
    at_edge = _triage([square], target=(150, 150))
    check(
        "upscale 1.5 selezionato",
        _ids(at_edge.selected) == ["a.mp4"],
        _ids(at_edge.selected),
    )
    over = _triage([square], target=(151, 151))
    check(
        "upscale 1.51 rifiutato",
        _ids(over.rejected) == ["a.mp4"]
        and over.rejected[0].rejection_reason == "RESOLUTION_TOO_LOW",
        over.rejected[0].rejection_reason if over.rejected else "selezionato",
    )
    check(
        "il rifiuto geometrico porta i numeri",
        over.rejected
        and {"upscale", "max_upscale", "src_w", "src_h", "target_w", "target_h"}
        <= set(over.rejected[0].details),
        sorted(over.rejected[0].details) if over.rejected else [],
    )

    # -- inaction rule: dead band ----------------------------------------- #
    base = _asset("a.mp4")
    band = _triage([base], values={"sharpness": 40.0})
    check(
        "confidence 0.2 seleziona",
        _ids(band.selected) == ["a.mp4"],
        band.selected[0].details if band.selected else [],
    )
    check(
        "confidence 0.2 segnala incertezza",
        band.selected
        and band.selected[0].details.get("uncertain") is True
        and band.selected[0].details.get("signal") == "ambiguous",
        band.selected[0].details if band.selected else {},
    )
    upper = _triage([base], values={"sharpness": 10.0})
    check(
        "confidence 0.8 seleziona",
        _ids(upper.selected) == ["a.mp4"],
        upper.selected[0].details if upper.selected else [],
    )
    just_over = _triage([base], values={"sharpness": 9.5})
    check(
        "confidence 0.81 rifiuta",
        _ids(just_over.rejected) == ["a.mp4"]
        and just_over.rejected[0].rejection_reason == "VIDEO_TOO_BLURRY",
        just_over.rejected[0].rejection_reason if just_over.rejected else "selezionato",
    )

    # -- one rejection per measured metric --------------------------------- #
    noisy = _triage(
        [_asset("audio.mp3", kind="audio", width=0, height=0)], values={"snr_db": 2.0}
    )
    check(
        "audio rumoroso rifiutato",
        noisy.rejected and noisy.rejected[0].rejection_reason == "AUDIO_TOO_NOISY",
        noisy.rejected[0].details if noisy.rejected else [],
    )
    dark = _triage([base], values={"mean_luma": 0.0})
    check(
        "video scuro rifiutato",
        dark.rejected and dark.rejected[0].rejection_reason == "VIDEO_TOO_DARK",
        dark.rejected[0].details if dark.rejected else [],
    )
    incoherent = _triage(
        [_asset("music.mp3", kind="audio", width=0, height=0)],
        values={"music_ratio": 0.0},
    )
    check(
        "musica incoerente rifiutata",
        incoherent.rejected
        and incoherent.rejected[0].rejection_reason == "MUSIC_INCOHERENT",
        incoherent.rejected[0].details if incoherent.rejected else [],
    )

    # -- unreadable and too short ------------------------------------------ #
    broken = _asset("broken.mp4", metrics={"probe_error": "OSError: no header"})
    broken_result = _triage([broken])
    check(
        "file illeggibile rifiutato con la causa",
        broken_result.rejected
        and broken_result.rejected[0].rejection_reason == "UNREADABLE"
        and "probe_error" in broken_result.rejected[0].details,
        broken_result.rejected[0].details if broken_result.rejected else [],
    )
    short = _triage([_asset("short.mp4", duration_sec=0.5)])
    check(
        "clip troppo corta rifiutata",
        short.rejected and short.rejected[0].rejection_reason == "TOO_SHORT",
        short.rejected[0].details if short.rejected else [],
    )

    # -- duplicates -------------------------------------------------------- #
    same = _triage(
        [
            _asset("a.mp4", size=1000, sha256="aa"),
            _asset("b.mp4", size=1000, sha256="aa"),
        ]
    )
    check(
        "duplicato esatto rifiutato",
        _ids(same.rejected) == ["b.mp4"]
        and same.rejected[0].details.get("duplicate_of") == "a.mp4"
        and same.rejected[0].details.get("method") == "sha256",
        same.rejected[0].details if same.rejected else [],
    )
    different = _triage(
        [
            _asset("a.mp4", size=1000, sha256="aa"),
            _asset("b.mp4", size=1000, sha256="bb"),
        ]
    )
    check(
        "stessa dimensione, contenuto diverso: nessun rifiuto",
        not different.rejected and len(different.selected) == 2,
        len(different.rejected),
    )

    # -- slot caps --------------------------------------------------------- #
    pool = [
        _asset(f"img{i}.png", kind="image", width=2000, height=2000, duration_sec=0.0)
        for i in range(1, 5)
    ]
    capped = _triage(pool, target=(1000, 1000))
    check(
        "quattro b-roll, cap tre",
        len(capped.selected) == 3
        and len(capped.rejected) == 1
        and capped.rejected[0].rejection_reason == "REDUNDANT_BROLL",
        (len(capped.selected), len(capped.rejected)),
    )
    check(
        "il b-roll eccedente dichiara il rango",
        capped.rejected
        and capped.rejected[0].details.get("rank") == 4
        and capped.rejected[0].details.get("cap") == 3,
        capped.rejected[0].details if capped.rejected else [],
    )
    music = _triage(
        [
            _asset("m1.mp3", kind="audio", width=0, height=0),
            _asset("m2.mp3", kind="audio", width=0, height=0),
        ]
    )
    check(
        "due musiche, cap uno",
        len([i for i in music.selected if i.role == "music"]) == 1
        and music.rejected
        and music.rejected[0].rejection_reason == "REDUNDANT_MUSIC",
        music.rejected[0].details if music.rejected else [],
    )

    # -- partition and determinism ---------------------------------------- #
    mixed = [
        _asset("z.mp4"),
        _asset("a.mp4"),
        _asset("m.mp3", kind="audio", width=0, height=0),
        _asset("bad.mp4", metrics={"probe_error": "boom"}),
    ]
    first = _triage(mixed)
    second = _triage(list(reversed(mixed)))
    accounted = sorted(_ids(first.selected) + _ids(first.rejected))
    check(
        "ogni asset compare esattamente una volta",
        accounted == sorted(item.asset_id for item in mixed)
        and len(set(accounted)) == len(accounted),
        accounted,
    )
    check(
        "ordine deterministico",
        _ids(first.selected) == _ids(second.selected)
        and _ids(first.rejected) == _ids(second.rejected),
        (_ids(first.selected), _ids(second.selected)),
    )

    # -- anchor policy ----------------------------------------------------- #
    anchored = _triage(
        [_asset("b.mp4", has_audio=False, at_sec=2.0, role="b_roll")],
        require_anchor=True,
    )
    check(
        "ancora dichiarata: b-roll selezionato",
        _ids(anchored.selected) == ["b.mp4"],
        _ids(anchored.selected),
    )
    unanchored = _triage(
        [_asset("b.mp4", has_audio=False, role="b_roll")],
        require_anchor=True,
    )
    check(
        "senza ancora: rifiuto dichiarato",
        unanchored.rejected
        and unanchored.rejected[0].rejection_reason == "NO_SEMANTIC_ANCHOR",
        unanchored.rejected[0].details if unanchored.rejected else [],
    )

    # -- absent metrics are declared, never invented ----------------------- #
    silent = _triage([_asset("v.mp4", has_audio=False)])
    check(
        "metrica assente finisce in not_evaluated",
        {"video_sharpness", "video_exposure", "semantic_anchor"}
        <= set(silent.not_evaluated),
        silent.not_evaluated,
    )
    check(
        "metrica assente non produce rifiuti",
        not silent.rejected,
        [item.rejection_reason for item in silent.rejected],
    )

    # -- payload block is the external contract ---------------------------- #
    block = first.payload_block()
    check(
        "payload_block: chiavi di primo livello",
        set(block)
        == {
            "selected_assets",
            "rejected_assets",
            "not_evaluated",
            "thresholds",
            "role_source",
        },
        sorted(block),
    )
    selected_entry = block["selected_assets"][0]
    check(
        "payload_block: chiavi del selezionato",
        set(selected_entry)
        == {"asset_id", "type", "role", "target_duration_sec", "treatment"},
        sorted(selected_entry),
    )
    rejected_entry = block["rejected_assets"][0]
    check(
        "payload_block: chiavi dello scartato",
        set(rejected_entry) == {"asset_id", "type", "rejection_reason", "details"},
        sorted(rejected_entry),
    )
    music_block = music.payload_block()
    music_entry = next(
        item for item in music_block["selected_assets"] if item["role"] == "music"
    )
    check(
        "payload_block: ducking dichiarato per la musica",
        music_entry.get("ducking_db") == -8.0,
        music_entry,
    )
    check(
        "validate_payload_block accetta un blocco sano",
        at.validate_payload_block(block) == [],
        at.validate_payload_block(block),
    )
    bad_block = json.loads(json.dumps(block))
    bad_block["rejected_assets"][0]["rejection_reason"] = "BOH"
    bad_block["rejected_assets"][0]["details"] = {}
    bad_block["selected_assets"][0]["role"] = ""
    errors = at.validate_payload_block(bad_block)
    check(
        "validate_payload_block nomina motivo, dettagli e ruolo",
        any("unknown reason" in item for item in errors)
        and any("empty details" in item for item in errors)
        and any("missing role" in item for item in errors),
        errors,
    )

    # -- manifest ---------------------------------------------------------- #
    manifest_block = _triage(
        [_asset("b.mp4", has_audio=False, role="b_roll", at_sec=1.5)],
        require_anchor=True,
    )
    check(
        "manifest: ruolo e ancora rispettati",
        manifest_block.role_source == "manifest"
        and _ids(manifest_block.selected) == ["b.mp4"],
        (manifest_block.role_source, _ids(manifest_block.selected)),
    )

    with tempfile.TemporaryDirectory(prefix="vx-triage-") as raw:
        work = Path(raw)
        ghost = work / "broken.mp4"
        ghost.write_bytes(b"not a media file\n" * 4)
        before = hashlib.sha256(ghost.read_bytes()).hexdigest()
        found = at.discover_assets(work)
        check(
            "discovery: file illeggibile classificato, non ignorato",
            len(found) == 1 and found[0].metrics.get("probe_error"),
            [item.asset_id for item in found],
        )
        discovered = at.triage_assets(found, (1080, 1920), measure=lambda a: {})
        check(
            "discovery: il file illeggibile viene rifiutato",
            discovered.rejected
            and discovered.rejected[0].rejection_reason == "UNREADABLE",
            [item.rejection_reason for item in discovered.rejected],
        )
        after = hashlib.sha256(ghost.read_bytes()).hexdigest()
        check("discovery: sorgente intatta", before == after, before[:12])
        for bad_manifest in (
            {"assets": [{"path": Path.cwd().drive + "/escape.mp4"}]},
            {"assets": [{"path": "../escape.mp4"}]},
            {"assets": [{"path": "ok.mp4", "boh": 1}]},
        ):
            try:
                at.discover_assets(work, manifest=bad_manifest)
            except ValueError:
                ok = True
            else:
                ok = False
            check(
                f"manifest rifiutato: {json.dumps(bad_manifest)[:28]}",
                ok,
                "ValueError" if ok else "accettato",
            )

    # -- settings override reaches the effective threshold ---------------- #
    settings.apply(settings.build(overrides=["thresholds.triage_max_upscale=2.0"]))
    try:
        relaxed = _triage([_asset("a.mp4", width=100, height=100)], target=(180, 180))
        effective = relaxed.thresholds["triage_max_upscale"]
    finally:
        settings.reset()
    check(
        "override cambia la soglia effettiva del triage",
        effective == 2.0 and _ids(relaxed.selected) == ["a.mp4"],
        (effective, _ids(relaxed.selected)),
    )
    check(
        "dopo reset la soglia torna 1.5",
        contract.THRESHOLDS["triage_max_upscale"] == 1.5,
        contract.THRESHOLDS["triage_max_upscale"],
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
