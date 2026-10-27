"""A9: perceptual duplicate detection in asset triage.

Two encodes of the same take must collapse to one survivor, while a merely
similar clip (same set, different take) must survive untouched. The fingerprint
is injected here so the test never decodes a file and never runs ffmpeg: what is
under test is the comparison logic, the rejection path and the determinism, not
opencv.

python tests/test_triage_dedup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import asset_triage as at  # noqa: E402
from vx import contract  # noqa: E402

PASS = 0
FAIL = 0

HEX_CHARS = 48
BITS = HEX_CHARS * 4


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


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


def _triage(assets, hashes, **kwargs):
    return at.triage_assets(
        assets,
        (1080, 1920),
        measure=lambda asset: {},
        fingerprint=lambda asset: hashes[asset.asset_id],
        **kwargs,
    )


def _ids(decisions) -> list:
    return [item.asset_id for item in decisions]


def _rejected(result) -> dict:
    return {item.asset_id: item for item in result.rejected}


def _bits(ones: int) -> str:
    """A fingerprint with exactly `ones` set bits, the rest zero."""
    binary = "1" * ones + "0" * (BITS - ones)
    return "".join(
        f"{int(binary[index : index + 4], 2):x}" for index in range(0, BITS, 4)
    )


def main() -> int:
    # -- the declared constants -------------------------------------------- #
    max_distance = contract.THRESHOLDS["triage_phash_max_distance"]
    tolerance = contract.THRESHOLDS["triage_phash_duration_tolerance_sec"]
    check(
        "soglie A9 dichiarate nel contratto",
        (max_distance, tolerance) == (0.06, 0.5),
        (max_distance, tolerance),
    )

    # -- identical fingerprints: one survivor ------------------------------ #
    same = _triage(
        [_asset("a.mp4"), _asset("b.mp4")],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": "0" * HEX_CHARS},
    )
    same_rejected = _rejected(same)
    check(
        "impronte identiche: un solo selezionato",
        _ids(same.selected) == ["a.mp4"],
        _ids(same.selected),
    )
    check(
        "impronte identiche: DUPLICATE_ASSET sul secondo",
        set(same_rejected) == {"b.mp4"}
        and same_rejected["b.mp4"].rejection_reason == "DUPLICATE_ASSET",
        sorted(same_rejected),
    )
    check(
        "il rifiuto nomina il superstite e il punteggio",
        same_rejected["b.mp4"].details.get("duplicate_of") == "a.mp4"
        and same_rejected["b.mp4"].details.get("method") == "phash"
        and same_rejected["b.mp4"].details.get("similarity") == 1.0,
        same_rejected["b.mp4"].details,
    )
    check(
        "il superstite non è mai rifiutato",
        "a.mp4" not in same_rejected,
        sorted(same_rejected),
    )
    block = same.payload_block()
    block_entry = block["rejected_assets"][0]
    check(
        "payload_block porta id e punteggio senza cambio di forma",
        set(block_entry) == {"asset_id", "type", "rejection_reason", "details"}
        and block_entry["rejection_reason"] == "DUPLICATE_ASSET"
        and block_entry["details"]["duplicate_of"] == "a.mp4"
        and block_entry["details"]["similarity"] == 1.0,
        block_entry,
    )
    check(
        "validate_payload_block accetta il blocco",
        at.validate_payload_block(block) == [],
        at.validate_payload_block(block),
    )

    # -- boundary: inside the threshold collapses, outside it does not ----- #
    inside_bits = int(max_distance * BITS)
    inside = _triage(
        [_asset("a.mp4"), _asset("b.mp4")],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": _bits(inside_bits)},
    )
    check(
        f"{inside_bits} bit su {BITS}: ancora duplicato",
        set(_rejected(inside)) == {"b.mp4"},
        {key: value.details for key, value in _rejected(inside).items()},
    )
    outside_bits = inside_bits + 1
    outside = _triage(
        [_asset("a.mp4"), _asset("b.mp4")],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": _bits(outside_bits)},
    )
    check(
        f"{outside_bits} bit su {BITS}: oltre soglia, entrambi restano",
        not outside.rejected and len(outside.selected) == 2,
        ([item.rejection_reason for item in outside.rejected], _ids(outside.selected)),
    )
    check(
        "la distanza oltre soglia resta dichiarata",
        at.fingerprint_distance("0" * HEX_CHARS, _bits(outside_bits))
        == outside_bits / BITS,
        at.fingerprint_distance("0" * HEX_CHARS, _bits(outside_bits)),
    )

    # -- different duration: not a duplicate ------------------------------- #
    longer = _triage(
        [_asset("a.mp4"), _asset("b.mp4", duration_sec=5.0 + tolerance + 0.25)],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": "0" * HEX_CHARS},
    )
    check(
        "durata oltre la tolleranza: entrambi restano",
        not longer.rejected and len(longer.selected) == 2,
        ([item.rejection_reason for item in longer.rejected], _ids(longer.selected)),
    )

    # -- different resolution: not a duplicate ----------------------------- #
    smaller = _triage(
        [_asset("a.mp4"), _asset("b.mp4", width=720, height=1280)],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": "0" * HEX_CHARS},
    )
    check(
        "risoluzione diversa: entrambi restano",
        not smaller.rejected and len(smaller.selected) == 2,
        ([item.rejection_reason for item in smaller.rejected], _ids(smaller.selected)),
    )

    # -- decode failure is absence, never a rejection ---------------------- #
    def _explode(asset: at.AssetInfo) -> str:
        raise at.FingerprintError("no decodable frame at 0.15")

    broken = at.triage_assets(
        [_asset("a.mp4"), _asset("b.mp4")],
        (1080, 1920),
        measure=lambda asset: {},
        fingerprint=_explode,
    )
    check(
        "decodifica fallita: nessun rifiuto",
        not broken.rejected,
        [item.rejection_reason for item in broken.rejected],
    )
    check(
        "decodifica fallita: l'asset resta selezionato",
        len(broken.selected) == 2,
        _ids(broken.selected),
    )
    check(
        "decodifica fallita: dichiarata in not_evaluated con la causa",
        any(
            item.startswith("visual_fingerprint: FingerprintError:")
            for item in broken.not_evaluated
        ),
        broken.not_evaluated,
    )

    # -- exact sha dedup is unchanged -------------------------------------- #
    exact = _triage(
        [
            _asset("a.mp4", size=1000, sha256="aa"),
            _asset("b.mp4", size=1000, sha256="aa"),
        ],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": "1" * HEX_CHARS},
    )
    exact_rejected = _rejected(exact)
    check(
        "sha esatto: rifiuto con method sha256",
        set(exact_rejected) == {"b.mp4"}
        and exact_rejected["b.mp4"].details.get("method") == "sha256"
        and exact_rejected["b.mp4"].details.get("duplicate_of") == "a.mp4",
        exact_rejected["b.mp4"].details if exact_rejected else {},
    )
    check(
        "sha esatto: il rifiuto porta la dimensione del file",
        exact_rejected["b.mp4"].details.get("size") == 1000,
        exact_rejected["b.mp4"].details,
    )
    distinct = _triage(
        [
            _asset("a.mp4", size=1000, sha256="aa"),
            _asset("b.mp4", size=1000, sha256="bb"),
        ],
        {"a.mp4": "0" * HEX_CHARS, "b.mp4": _bits(outside_bits)},
    )
    check(
        "stessa dimensione, sha diverso e impronte lontane: nessun rifiuto",
        not distinct.rejected and len(distinct.selected) == 2,
        (
            [item.rejection_reason for item in distinct.rejected],
            _ids(distinct.selected),
        ),
    )

    # -- the survivor is the first in the deterministic order -------------- #
    hashes = {
        "a.mp4": "0" * HEX_CHARS,
        "b.mp4": "0" * HEX_CHARS,
        "c.mp4": "0" * HEX_CHARS,
    }
    pool = [_asset("c.mp4"), _asset("a.mp4"), _asset("b.mp4")]
    forward = _triage(list(pool), hashes)
    backward = _triage(list(reversed(pool)), hashes)
    forward_rejected = _rejected(forward)
    backward_rejected = _rejected(backward)
    check(
        "superstite deterministico: a.mp4 in entrambi gli ordini",
        _ids(forward.selected) == ["a.mp4"] and _ids(backward.selected) == ["a.mp4"],
        (_ids(forward.selected), _ids(backward.selected)),
    )
    check(
        "gli altri due puntano al superstite in entrambi gli ordini",
        {item.details["duplicate_of"] for item in forward_rejected.values()}
        == {"a.mp4"}
        and {item.details["duplicate_of"] for item in backward_rejected.values()}
        == {"a.mp4"}
        and set(forward_rejected) == set(backward_rejected) == {"b.mp4", "c.mp4"},
        (sorted(forward_rejected), sorted(backward_rejected)),
    )

    # -- every asset is still accounted for exactly once ------------------- #
    accounted = sorted(_ids(forward.selected) + _ids(forward.rejected))
    check(
        "ogni asset compare esattamente una volta",
        accounted == ["a.mp4", "b.mp4", "c.mp4"],
        accounted,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
