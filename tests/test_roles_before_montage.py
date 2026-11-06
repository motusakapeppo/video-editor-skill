"""Il ruolo a_roll/b_roll si decide **prima** del montaggio, e si dichiara.

    python tests/test_roles_before_montage.py

Difetto del 2026-11-06: con 15.23 s di girato e 10 s chiesti, il reel usciva da
4.65 s e finiva `NON-PUBBLICABILE` per durata. Il materiale c'era: il secondo
video non ha audio, quindi finiva b_roll, e nessuno lo promuoveva.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def assets() -> dict:
    return {
        "voce.mp4": SimpleNamespace(kind="video", duration_sec=5.26),
        "silenzioso.mp4": SimpleNamespace(kind="video", duration_sec=9.97),
        "foto.jpeg": SimpleNamespace(kind="image", duration_sec=3.0),
        "musica.wav": SimpleNamespace(kind="audio", duration_sec=20.0),
    }


def entries() -> list:
    return [
        {"asset_id": "voce.mp4", "role": "a_roll", "target_duration_sec": 4.65},
        {"asset_id": "silenzioso.mp4", "role": "b_roll", "target_duration_sec": 9.97},
        {"asset_id": "foto.jpeg", "role": "b_roll", "target_duration_sec": 3.0},
        {"asset_id": "musica.wav", "role": "music", "target_duration_sec": 20.0},
    ]


def test_promotes_the_video_that_covers_the_gap() -> None:
    print("\n1. il video silenzioso entra in a_roll quando la durata non regge")
    result, block = auto.promote_a_roll(entries(), assets(), 10.0)
    roles = {item["asset_id"]: item["role"] for item in result}
    check("il silenzioso diventa a_roll", roles["silenzioso.mp4"] == "a_roll", roles)
    check(
        "la promozione e' dichiarata con i secondi",
        [item["asset_id"] for item in block["promoted"]] == ["silenzioso.mp4"]
        and block["promoted"][0]["seconds"] == 9.97,
        block["promoted"],
    )
    check(
        "il perche' porta i numeri",
        "5.260" in block["promoted"][0]["why"] and "10.000" in block["promoted"][0]["why"],
        block["promoted"][0]["why"],
    )
    check(
        "il monte prima/dopo e' dichiarato",
        block["a_roll_sec_before"] == 5.26 and block["a_roll_sec_after"] == 15.23,
        (block["a_roll_sec_before"], block["a_roll_sec_after"]),
    )
    check("non resta scoperto niente", block["still_short_sec"] == 0.0, block["still_short_sec"])


def test_never_promotes_music_or_images() -> None:
    print("\n2. musica e immagini non diventano a_roll")
    result, block = auto.promote_a_roll(entries(), assets(), 60.0)
    roles = {item["asset_id"]: item["role"] for item in result}
    check("la musica resta musica", roles["musica.wav"] == "music", roles)
    check("l'immagine resta b_roll", roles["foto.jpeg"] == "b_roll", roles)
    check(
        "e lo scoperto si dichiara prima del render",
        block["still_short_sec"] == round(60.0 - 15.23, 3),
        block["still_short_sec"],
    )


def test_nothing_to_do_when_material_is_enough() -> None:
    print("\n3. se gli a_roll bastano, nessuno si muove")
    result, block = auto.promote_a_roll(entries(), assets(), 4.0)
    check("nessuna promozione", block["promoted"] == [], block["promoted"])
    check(
        "i ruoli sono quelli di partenza",
        {item["asset_id"]: item["role"] for item in result}["silenzioso.mp4"] == "b_roll",
    )


def test_montage_mode_still_needs_the_silent_video() -> None:
    print("\n4. anche in montage il video senza audio va promosso")
    # `montage` toglie la regola "un solo a_roll", non quella "serve l'audio":
    # il secondo video resta b_roll, e senza promozione il monte non basta.
    result, block = auto.promote_a_roll(entries(), assets(), 10.0, montage=True)
    roles = {item["asset_id"]: item["role"] for item in result}
    check("il silenzioso entra lo stesso", roles["silenzioso.mp4"] == "a_roll", roles)
    check("il blocco dichiara la modalita'", block["montage"] is True, block["montage"])


def test_no_request_means_no_guessing() -> None:
    print("\n5. senza durata chiesta non si promuove niente")
    _result, block = auto.promote_a_roll(entries(), assets(), None)
    check("requested_sec e' None", block["requested_sec"] is None, block["requested_sec"])
    check("nessuna promozione", block["promoted"] == [], block["promoted"])


def test_does_not_mutate_the_input() -> None:
    print("\n6. la lista in ingresso non viene riscritta a sorpresa")
    source = entries()
    auto.promote_a_roll(source, assets(), 10.0)
    check(
        "il chiamante conserva i suoi ruoli",
        {item["asset_id"]: item["role"] for item in source}["silenzioso.mp4"] == "b_roll",
    )


def main() -> int:
    test_promotes_the_video_that_covers_the_gap()
    test_never_promotes_music_or_images()
    test_nothing_to_do_when_material_is_enough()
    test_montage_mode_still_needs_the_silent_video()
    test_no_request_means_no_guessing()
    test_does_not_mutate_the_input()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
