"""I sottotitoli si chiedono anche a parole, non solo col flag.

    python tests/test_subs_requested.py

Difetto del 2026-11-06: il prompt diceva "con sottotitoli", il lavoro usciva
senza testo e il report dichiarava "no speech" — falso, perche' lo stadio del
parlato non era mai partito: `captions` si accendeva solo con `--subs`.
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


def main() -> int:
    check(
        "S1 «con sottotitoli» nel prompt accende i sottotitoli",
        auto.subs_requested(SimpleNamespace(prompt="reel 15 secondi, con sottotitoli")),
    )
    check(
        "S2 l'inglese conta lo stesso",
        auto.subs_requested(SimpleNamespace(prompt="with subtitles, 15s")),
    )
    check(
        "S3 senza richiesta non si inventa testo",
        auto.subs_requested(SimpleNamespace(prompt="un reel di 10 secondi")) is False,
    )
    check(
        "S4 --subs vince anche senza parole nel prompt",
        auto.subs_requested(SimpleNamespace(prompt="un reel", subs=True)),
    )
    check(
        "S5 --no-subs vince su tutto, anche sul prompt",
        auto.subs_requested(
            SimpleNamespace(prompt="con sottotitoli", subs=True, no_subs=True)
        )
        is False,
    )
    check(
        "S6 un prompt vuoto non accende niente",
        auto.subs_requested(SimpleNamespace(prompt="")) is False,
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
