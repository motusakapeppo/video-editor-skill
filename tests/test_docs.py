"""Il cancello della documentazione: il testo deve combaciare col ledger.

python tests/test_docs.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import brief as brief_mod, docs, prove  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:46} {detail}")


def _brief() -> dict:
    """A fully-applied brief whose executed names match its declarations."""
    b = brief_mod.new_brief("docs-test", "a" * 64, "b" * 64)
    b["measurements"] = {
        "transient_ratio": 1.4,
        "transient_p33": 1.1,
        "transient_p67": 1.8,
        "hue_mass_top3": [
            {"hue": 12, "mass": 0.42},
            {"hue": 210, "mass": 0.18},
            {"hue": 96, "mass": 0.09},
        ],
        "ink_coverage_pct": 0.62,
        "subject_offset": {"x": 0.07, "y": -0.02},
        "onsets_per_s": 2.4,
        "first_speech_onset_s": 0.8,
        "first3s_motion": 0.22,
        "face_present": True,
        "luma_p50": 0.48,
        "contrast": 0.71,
    }
    values = {
        "palette": ("hue_mass_top3", "dual", "corpus_hash:" + "b" * 64),
        "grade": ("luma_p50", "curve", "corpus_hash:" + "b" * 64),
        "animation": ("transient_ratio", "fade", "corpus_hash:" + "b" * 64),
        "cut_density": ("onsets_per_s", 2.0, "corpus_hash:" + "b" * 64),
        "composition": (
            "subject_offset",
            {"x": 0.07, "y": -0.02},
            "declared:1/20 width",
        ),
        "typography": ("ink_coverage_pct", 8, "measured:delivery"),
    }
    executed_names = {
        "palette": "none",
        "grade": "curve",
        "animation": "fade",
        "cut_density": "snap",
        "composition": "crop",
        "typography": "none",
    }
    filters = {
        "palette": "null",
        "grade": "curves=m='0/0 0.5/0.55 1/1'",
        "animation": "fade=t=in:st=0:d=0.2500",
        "cut_density": "concat=n=2:v=1:a=1",
        "composition": "crop=607:1080:100:0",
        "typography": "null",
    }
    for axis, (driver, value, provenance) in values.items():
        choice = b["choices"][axis]
        choice.update(
            driver=driver,
            value=value,
            provenance=provenance,
            inaction=False,
            confidence=0.9,
            applied=True,
        )
        if axis == "animation":
            choice.update(name="fade", count=1, direction_basis="asserted")
        brief_mod.add_reason(
            b,
            axis,
            driver,
            value,
            {"p33": 0.1, "p67": 0.2},
            provenance,
            "misura sopra soglia",
            direction_basis="asserted" if axis == "animation" else None,
        )
        brief_mod.add_executed(
            b, axis, executed_names[axis], "ffmpeg", filters[axis], 0, 125, at=0.0
        )
    b["docs_match"] = {"ok": False, "doc_sha256": "", "ledger_sha256": ""}
    return b


def main() -> int:
    b = _brief()
    text = docs.render(b)
    b["docs_match"]["doc_sha256"] = prove._sha256_text(text)
    check("docs generati combaciano", docs.match(b, text) is True)
    check("l'hash del ledger e' nel testo", docs.hash_ledger(b) in text)

    unexecuted = copy.deepcopy(b)
    unexecuted["choices"]["animation"]["name"] = "punch"  # dichiarato, mai eseguito
    unexecuted_text = docs.render(unexecuted)
    unexecuted["docs_match"]["doc_sha256"] = prove._sha256_text(unexecuted_text)
    check(
        "effetto dichiarato e non eseguito rifiutato",
        docs.match(unexecuted, unexecuted_text) is False,
    )

    missing_sentence = copy.deepcopy(b)
    lines = [ln for ln in text.splitlines() if not ln.startswith("Riduzione di scope")]
    missing_text = "\n".join(lines)
    missing_sentence["docs_match"]["doc_sha256"] = prove._sha256_text(missing_text)
    check(
        "frase obbligatoria mancante rifiutata",
        docs.match(missing_sentence, missing_text) is False,
    )

    stale = copy.deepcopy(b)
    stale["executed"][0]["frames"] = 99
    stale_text = docs.render(stale)
    stale["docs_match"]["doc_sha256"] = prove._sha256_text(text)
    check(
        "ledger mutato non combacia piu' col testo vecchio",
        docs.match(stale, text) is False,
        "hash del ledger diverso",
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
