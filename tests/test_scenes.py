"""Le composizioni complesse: chieste a parole, dichiarate, deterministiche.

    python tests/test_scenes.py

Il browser costa 50-200 ms per fotogramma, quindi non si usa sempre: solo quando
la composizione lo chiede (split screen, confronto, sottopancia). Qui si prova la
prima metà, quella che non renderizza: la scena si riconosce, si scrive uguale a
se stessa, e non porta dentro niente che venga da fuori.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import scenes  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def main() -> int:
    split = scenes.wants("reel con split screen, 10 secondi")
    check("S1 split screen riconosciuto", (split or {}).get("id") == "split_screen", split)
    check(
        "S1 lo split screen lo fa ffmpeg (flussi video, non grafica)",
        (split or {}).get("engine") == "ffmpeg",
        split,
    )
    check(
        "S1 il sottopancia lo fa Chromium (grafica)",
        (scenes.wants("sottopancia con nome e ruolo") or {}).get("engine") == "chromium",
        scenes.wants("sottopancia con nome e ruolo"),
    )
    check(
        "S1 le parole che l'hanno deciso sono citate",
        "split screen" in (split or {}).get("signals", []),
        (split or {}).get("signals"),
    )

    compare = scenes.wants("confronto prima e dopo")
    check("S2 confronto riconosciuto", (compare or {}).get("id") == "compare", compare)

    third = scenes.wants("sottopancia con nome e ruolo")
    check("S3 sottopancia riconosciuto", (third or {}).get("id") == "lower_third", third)

    check(
        "S4 un reel normale non chiede scene",
        scenes.wants("un reel di dieci secondi") is None,
        scenes.wants("un reel di dieci secondi"),
    )

    html_a = scenes.build_html({"id": "compare", "left": "PRIMA", "right": "DOPO"})
    html_b = scenes.build_html({"id": "compare", "left": "PRIMA", "right": "DOPO"})
    check(
        "S5 stesso input, stesso documento (byte per byte)",
        scenes.document_sha256(html_a) == scenes.document_sha256(html_b),
        scenes.document_sha256(html_a)[:12],
    )
    check(
        "S6 niente rete, niente script, niente font esterni",
        "http" not in html_a and "<script" not in html_a and "@import" not in html_a,
        len(html_a),
    )
    check(
        "S7 il confronto ha due riquadri e due etichette",
        html_a.count("pane") >= 2 and "PRIMA" in html_a and "DOPO" in html_a,
        (html_a.count("pane"), "PRIMA" in html_a),
    )

    evil = scenes.build_html({"id": "lower_third", "name": "<script>x</script>", "role": "a&b"})
    check(
        "S8 il testo e' escapato, non iniettato",
        "<script>x</script>" not in evil and "&lt;script&gt;" in evil and "a&amp;b" in evil,
        "escaping",
    )

    declared = scenes.declare("split screen, 10s")
    check(
        "S9 la scena dichiarata porta id, motore, identita' e stato",
        declared
        and declared[0]["status"] == "declared"
        and len(declared[0]["document_sha256"]) == 64,
        declared,
    )
    check("S10 nessuna scena, nessuna dichiarazione", scenes.declare("reel normale") == [])

    raised = None
    try:
        scenes.build_html({"id": "scena_inventata"})
    except ValueError as exc:
        raised = str(exc)
    check("S11 una scena non dichiarata viene rifiutata", raised is not None, raised)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
