"""vx.fontproposal: licensed fonts, a PNG specimen each, one valid json.

No network. The pool is the bundled ``assets/fonts`` set plus a temporary
``fontlib.Store`` seeded with three of those same faces. Catalogue intents are
checked against the real ``catalogue.json`` without fetching anything. Run:
python tests/test_fontproposal.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import fontlib, fontproposal, fontstore, fonts  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

REQUIREMENT = "horror reel, tagliente, condensato, titoli aggressivi"
ENERGY = 0.82
STORE_SEEDS = (
    ("Antonio-Regular.ttf", "Antonio"),
    ("BebasNeue.ttf", "Bebas Neue"),
    ("Inter-Variable.ttf", "Inter"),
)


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:68} {detail}")


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"{'SKIP':4} {label:68} {detail}")


def seed_store(root: Path) -> fontlib.Store:
    """A real store with three bundled faces, licence read from their OFL text."""
    store = fontlib.Store(root / "store")
    bundle = fonts.package_font_dir()
    ofl = bundle / "OFL.txt"
    licence_url = str(ofl.resolve().as_uri()) if ofl.is_file() else ""
    for name, family in STORE_SEEDS:
        path = bundle / name
        if not path.is_file():
            continue
        store.add_file(
            path,
            family=family,
            licence="commercial",
            licence_url=licence_url,
            source_url=path.resolve().as_uri(),
        )
    return store


def test_intents() -> None:
    print("\n-- intenzioni: mappa verificata contro il catalogo")
    families = sorted(
        {name for group in fontproposal.INTENT_FAMILIES.values() for name in group}
    )
    missing = [name for name in families if fontstore.find(name) is None]
    check(
        "ogni famiglia della mappa esiste nel catalogo",
        not missing,
        missing,
    )
    found = fontproposal.intent_families(REQUIREMENT)
    check(
        "horror/tagliente/condensato/aggressivi accendono le intenzioni",
        {"condensed", "horror", "aggressive"}.issubset(set(found["matched"])),
        found["matched"],
    )
    check("nessuna famiglia scartata dal catalogo reale", found["discarded"] == [],
          found["discarded"])
    check("selezione non vuota", bool(found["selected"]), len(found["selected"]))
    check(
        "solo id verificati nella selezione",
        all(fontstore.find(name) is not None for name in found["selected"]),
    )
    limited = fontproposal.intent_families(REQUIREMENT, limit=3)
    check("limit rispettato", len(limited["selected"]) <= 3, len(limited["selected"]))

    fake = fontproposal.intent_families(
        "condensato", mapping={"condensed": ("antonio", "famigliainventata")}
    )
    check("la famiglia inesistente viene scartata", fake["selected"] == ["antonio"],
          fake["selected"])
    check(
        "lo scarto e' dichiarato in italiano",
        any("famigliainventata" in reason for reason in fake["discarded"]),
        fake["discarded"],
    )

    # S11: il tono documentario/sobrio deve pesare. Il 4 novembre un prompt che
    # diceva "tono sobrio e documentario" proponeva Archivo Black, Black Ops One
    # e Butcherman, tutti a punteggio 3.0: quelle parole non erano in tabella.
    editorial = fontproposal.intent_families("un taglio sobrio e documentario")
    check(
        "sobrio/documentario accende 'editorial' e non 'aggressive'",
        "editorial" in editorial["matched"] and "aggressive" not in editorial["matched"],
        editorial["matched"],
    )
    check(
        "i segnali dicono quale parola ha deciso",
        any(item.startswith("editorial:") for item in editorial["signals"]),
        editorial["signals"],
    )
    check(
        "la shortlist parte da una famiglia editoriale, non da un display",
        bool(editorial["selected"])
        and editorial["selected"][0]
        in {name for name in fontproposal.INTENT_FAMILIES["editorial"]},
        editorial["selected"][:3],
    )
    check(
        "il ranking chiede un peso regolare, non nero",
        "regular" in fontproposal._rank_requirement("tono sobrio e documentario").split()
        and "black" not in fontproposal._rank_requirement("tono sobrio e documentario").split(),
        fontproposal._rank_requirement("tono sobrio e documentario"),
    )
    check(
        "senza tono nel brief non si inventa un intento",
        fontproposal.intent_families("un video qualunque")["matched"] == ["default"],
        fontproposal.intent_families("un video qualunque")["matched"],
    )


def test_candidates(payload: dict, workdir: Path) -> None:
    print("\n-- proposta: tre famiglie, tre campioni, punteggi decrescenti")
    check("schema vx.fontproposal/1", payload.get("schema") == "vx.fontproposal/1")
    candidates = payload.get("candidates") or []
    check("tre candidati", len(candidates) == 3, len(candidates))
    families = [str(item.get("family") or "") for item in candidates]
    check("tre famiglie distinte", len(set(families)) == 3, families)
    check("chosen e' la prima famiglia", payload.get("chosen") == (families[0] if families else None))
    check("reason_if_none vuoto quando propone", payload.get("reason_if_none") == "")

    scores = [item.get("score") for item in candidates]
    check(
        "score decrescente",
        all(scores[index] >= scores[index + 1] for index in range(len(scores) - 1)),
        scores,
    )
    check(
        "licenza dichiarata, mai unknown",
        all(
            str(item.get("licence") or "").strip()
            and str(item.get("licence")) != "unknown"
            for item in candidates
        ),
        [item.get("licence") for item in candidates],
    )
    check(
        "licence_url presente",
        all(str(item.get("licence_url") or "").strip() for item in candidates),
        [item.get("licence_url") for item in candidates],
    )
    check(
        "because e' quello di rank, una riga",
        all(str(item.get("because") or "") and "\n" not in str(item["because"])
            for item in candidates),
    )
    check(
        "file assoluto e leggibile",
        all(Path(str(item.get("file"))).is_absolute() and Path(str(item["file"])).is_file()
            for item in candidates),
    )
    check(
        "downloaded false offline",
        all(item.get("downloaded") is False for item in candidates),
        [item.get("downloaded") for item in candidates],
    )
    check(
        "source locale (bundle|cache), mai catalogo offline",
        all(item.get("source") in ("bundle", "cache") for item in candidates),
        [item.get("source") for item in candidates],
    )

    fonts_dir = (workdir / "fonts").resolve()
    for item in candidates:
        png = Path(str(item.get("sample_png") or ""))
        check(
            f"{item.get('family')}: PNG in <workdir>/fonts",
            png.is_absolute() and png.parent == fonts_dir and png.is_file(),
            png.name,
        )
        check(
            f"{item.get('family')}: PNG non vuoto (> 2000 byte)",
            png.is_file() and png.stat().st_size > 2000,
            png.stat().st_size if png.is_file() else 0,
        )
        check(
            f"{item.get('family')}: nome file <family>-sample.png",
            png.name.endswith("-sample.png") and "sample" in png.name,
            png.name,
        )


def test_json_and_pool(workdir: Path, payload: dict) -> None:
    print("\n-- json: scritto, offline dichiarato, sample onesto")
    target = workdir / fontproposal.PROPOSAL_NAME
    check("font_proposal.json scritto", target.is_file(), target.name)
    if target.is_file():
        stored = json.loads(target.read_text(encoding="utf-8"))
    else:
        stored = {}
    check("il file contiene la stessa proposta", stored == payload)
    check("network=False dichiarato", payload.get("network") is False)
    check("pool local dichiarato", payload.get("pool") == "local", payload.get("pool"))
    check("sample nel json", payload.get("sample") == "PROVA", payload.get("sample"))
    check("requirement originale nel json", payload.get("requirement") == REQUIREMENT)
    check("created_utc presente", bool(payload.get("created_utc")), payload.get("created_utc"))
    check("discarded e' una lista", isinstance(payload.get("discarded"), list))
    rank_text = str(payload.get("rank_requirement") or "")
    check(
        "condensato e aggressivo traducono il requisito di rank",
        "condensed" in rank_text and ("black" in rank_text or "bold" in rank_text),
        rank_text,
    )
    # Cambiato il 2026-11-06: offline il brief viene **letto** anche senza rete,
    # perché il report deve dichiarare quale parola ha orientato la proposta.
    # L'indice è locale: nessuna richiesta esce dalla macchina (il test del
    # pool lo verifica già con `network: false`).
    check(
        "intents e' una lista letta dal brief, anche offline",
        isinstance(payload.get("intents"), list)
        and "condensed" in (payload.get("intents") or [])
        and isinstance(payload.get("signals"), list),
        payload.get("intents"),
    )


def test_sample_changes(root: Path) -> None:
    print("\n-- campione: cambiando sample cambia il PNG")
    first = fontproposal.propose(
        root / "sample_a", REQUIREMENT, ENERGY, count=1, allow_network=False, sample="PROVA"
    )
    second = fontproposal.propose(
        root / "sample_b", REQUIREMENT, ENERGY, count=1, allow_network=False,
        sample="ALTRA PROVA",
    )
    first_candidates = first.get("candidates") or []
    second_candidates = second.get("candidates") or []
    check("un candidato con sample=PROVA", len(first_candidates) == 1)
    check("un candidato con sample=ALTRA PROVA", len(second_candidates) == 1)
    check("count=1 restituisce un solo candidato", len(first_candidates) == 1,
          len(first_candidates))
    if first_candidates and second_candidates:
        check(
            "la stessa famiglia viene scelta (deterministico)",
            first_candidates[0]["family"] == second_candidates[0]["family"],
            first_candidates[0]["family"],
        )
        a = Path(str(first_candidates[0]["sample_png"])).read_bytes()
        b = Path(str(second_candidates[0]["sample_png"])).read_bytes()
        check("PNG diverso con sample diverso", a != b, f"{len(a)} vs {len(b)} byte")
        check("sample registrato nel json", second.get("sample") == "ALTRA PROVA")
    else:
        skip("PNG diverso con sample diverso", "nessun candidato")


def main() -> int:
    test_intents()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workdir = root / "job"
        previous = os.environ.get(fontproposal.ENV_STORE)
        try:
            os.environ[fontproposal.ENV_STORE] = str(root / "store")
            store = seed_store(root)
            check("store di prova con tre famiglie", len(store.all()) == 3, len(store.all()))
            payload = fontproposal.propose(
                workdir, REQUIREMENT, ENERGY, count=3, allow_network=False, sample="PROVA"
            )
            test_candidates(payload, workdir)
            test_json_and_pool(workdir, payload)
            test_sample_changes(root)
        finally:
            if previous is None:
                os.environ.pop(fontproposal.ENV_STORE, None)
            else:
                os.environ[fontproposal.ENV_STORE] = previous
    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
