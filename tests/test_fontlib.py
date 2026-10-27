"""vx.fontlib: records carry the licence, the store dedupes, the selector refuses.

Run: python tests/test_fontlib.py
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, fontlib, fonts  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:64} {detail}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def font_bytes(tag: str) -> bytes:
    return (tag * 64).encode("ascii")


def record(family: str, licence: str, name: str, styles=()) -> fontlib.FontRecord:
    return fontlib.FontRecord(
        family=family,
        licence=licence,
        file=f"/fonts/{name}",
        sha256=hashlib.sha256(f"{family}:{name}".encode()).hexdigest(),
        styles=list(styles),
    )


def test_record() -> None:
    print("\n-- FontRecord")
    for bad in ("gpl", "", "commercial-use", "PROPRIETARY", None):
        try:
            fontlib.FontRecord(family="X", licence=bad, file="a.ttf", sha256="0" * 64)
            check(f"licence {bad!r} rifiutata", False, "nessuna eccezione")
        except contract.VxError:
            check(f"licence {bad!r} rifiutata", True)

    try:
        fontlib.FontRecord.from_dict(
            {"family": "X", "licence": "mit", "file": "a.ttf", "sha256": "0" * 64}
        )
        check("from_dict rifiuta una licence ignota", False, "nessuna eccezione")
    except contract.VxError as exc:
        check("from_dict rifiuta una licence ignota", True, str(exc)[:48])

    try:
        fontlib.FontRecord(
            family=" ", licence="commercial", file="a.ttf", sha256="0" * 64
        )
        check("family vuota rifiutata", False, "nessuna eccezione")
    except contract.VxError:
        check("family vuota rifiutata", True)

    try:
        fontlib.FontRecord(family="X", licence="commercial", file="a.ttf", sha256="xyz")
        check("sha256 non esadecimale rifiutato", False, "nessuna eccezione")
    except contract.VxError:
        check("sha256 non esadecimale rifiutato", True)

    good = fontlib.FontRecord(
        family="Antonio",
        licence="commercial",
        file="/fonts/Antonio-Bold.ttf",
        sha256="a" * 64,
        styles="Bold;Regular",
        licence_url="https://example.test/licence",
        source_url="https://example.test/antonio.font",
        retrieved_at="2026-10-27T00:00:00+00:00",
    )
    check("licence normalizzata in minuscolo", good.licence == "commercial")
    check(
        "styles da stringa separate da ; e ,",
        good.styles == ["Bold", "Regular"],
        good.styles,
    )
    check("commercial e' auto-selezionabile", good.auto_selectable is True)
    check(
        "personal non e' auto-selezionabile",
        record("P", "personal", "p.ttf").auto_selectable is False,
    )
    decoded = fontlib.FontRecord.from_dict(good.to_dict())
    check("to_dict/from_dict round-trip", decoded == good, decoded)


def test_store() -> None:
    print("\n-- Store")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "Condensed-Bold.ttf"
        payload = font_bytes("a")
        source.write_bytes(payload)
        store = fontlib.Store(base / "store")

        first = store.add_file(
            source,
            family="Condensed Bold",
            licence="commercial",
            licence_url="https://example.test/terms",
            source_url="https://example.test/condensed.font",
            styles=["Bold"],
        )
        digest = sha(payload)
        check(
            "sha256 calcolato dal contenuto", first.sha256 == digest, first.sha256[:16]
        )
        check(
            "file copiato sotto <root>/<sha[:16]>",
            digest[:16] in first.file,
            first.file,
        )
        check("file del record esiste", Path(first.file).is_file())
        check("index.json scritto", (base / "store" / "index.json").is_file())
        check("retrieved_at popolato", bool(first.retrieved_at), first.retrieved_at)
        check("licence_url conservata", first.licence_url.endswith("/terms"))

        before = (base / "store" / "index.json").stat().st_mtime_ns
        second = store.add_file(
            source,
            family="Condensed Bold",
            licence="commercial",
            licence_url="https://example.test/terms",
            source_url="https://example.test/condensed.font",
            styles=["Bold"],
        )
        check("stesso file due volte = un record", len(store.all()) == 1, store.all())
        check("stesso sha alla seconda aggiunta", second.sha256 == first.sha256)
        check(
            "index non riscritto per un duplicato identico",
            (base / "store" / "index.json").stat().st_mtime_ns == before,
        )

        reloaded = fontlib.Store(base / "store")
        check("ricaricando da index.json c'e' un record", len(reloaded.all()) == 1)
        check("get(sha) ritrova il record", reloaded.get(digest) is not None)
        check("get(prefisso) ritrova il record", reloaded.get(digest[:12]) is not None)
        check("get(sha ignoto) e' None", reloaded.get("f" * 64) is None)
        check(
            "find() e' insensibile a maiuscole e punteggiatura",
            len(reloaded.find("condensed bold")) == 1,
            reloaded.find("CONDENSED-BOLD"),
        )

        refined_src = base / "Refined.ttf"
        refined_src.write_bytes(font_bytes("b"))
        refined = reloaded.add_file(refined_src, family="Refined", licence="unknown")
        check("record unknown accettato e dichiarato", refined.licence == "unknown")
        upgraded = reloaded.add_file(
            refined_src, family="Refined", licence="commercial"
        )
        check(
            "unknown puo' essere raffinato a commercial",
            upgraded.licence == "commercial",
        )
        check("il raffinamento non duplica", len(reloaded.all()) == 2)
        downgraded = reloaded.add_file(
            refined_src, family="Refined", licence="personal"
        )
        check(
            "una licence nota non viene declassata in silenzio",
            downgraded.licence == "commercial",
            downgraded.licence,
        )

        try:
            reloaded.add_file(base / "missing.ttf", family="X", licence="commercial")
            check("add_file su file mancante: VxError", False, "nessuna eccezione")
        except contract.VxError:
            check("add_file su file mancante: VxError", True)


def test_select() -> None:
    print("\n-- select")
    condensed = record(
        "Alpha Condensed", "commercial", "Alpha-Condensed-Bold.ttf", ["Bold"]
    )
    wide = record("Beta Wide", "commercial", "Beta-Wide-Regular.ttf", ["Regular"])
    personal = record(
        "Gamma Condensed", "personal", "Gamma-Condensed-Bold.ttf", ["Bold"]
    )
    unknown = record(
        "Delta Condensed", "unknown", "Delta-Condensed-Black.ttf", ["Black"]
    )

    chosen = fontlib.select(
        [wide, personal, unknown, condensed],
        requirement="condensed bold",
        energy=0.9,
    )
    check(
        "sceglie il commercial piu' aderente",
        chosen is not None and chosen.sha256 == condensed.sha256,
        chosen,
    )
    check(
        "non sceglie mai un personal",
        chosen is not None and chosen.licence == "commercial",
    )
    again = fontlib.select(
        [wide, personal, unknown, condensed],
        requirement="condensed bold",
        energy=0.9,
    )
    check(
        "stesso input -> stessa scelta",
        again is not None and again.sha256 == chosen.sha256,
    )

    only_bad = fontlib.select(
        [personal, unknown], requirement="condensed bold", energy=1.0
    )
    check("nessun commercial -> None", only_bad is None)
    check(
        "lista vuota -> None",
        fontlib.select([], requirement="bold", energy=0.5) is None,
    )

    light = record("Light Sans", "commercial", "LightSans-Light.ttf", ["Light"])
    black = record("Black Sans", "commercial", "BlackSans-Black.ttf", ["Black"])
    low = fontlib.select([light, black], requirement="", energy=0.0)
    high = fontlib.select([light, black], requirement="", energy=1.0)
    check(
        "energy bassa preferisce light",
        low is not None and low.sha256 == light.sha256,
        low,
    )
    check(
        "energy alta preferisce black",
        high is not None and high.sha256 == black.sha256,
        high,
    )

    hinted = fontlib.FontRecord(
        family="Hinted",
        licence="commercial",
        file="/fonts/Hinted.ttf",
        sha256=hashlib.sha256(b"hinted").hexdigest(),
        styles=["Condensed", "Bold"],
    )
    by_hint = fontlib.select([wide, hinted], requirement="condensed bold", energy=1.0)
    check(
        "gli styles dichiarati contano quanto il nome file",
        by_hint is not None and by_hint.sha256 == hinted.sha256,
        by_hint,
    )

    try:
        fontlib.select([condensed], requirement="bold", energy="molta")
        check("energy non numerica: VxError", False, "nessuna eccezione")
    except contract.VxError:
        check("energy non numerica: VxError", True)


def test_job_fonts_dir() -> None:
    print("\n-- job_fonts_dir")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        workdir = base / "work"
        workdir.mkdir()
        source = base / "Condensed-Bold.ttf"
        source.write_bytes(font_bytes("c"))
        store = fontlib.Store(base / "store")
        rec = store.add_file(source, family="Condensed Bold", licence="commercial")

        parent_before = sorted(p.name for p in base.iterdir())
        target = fontlib.job_fonts_dir(workdir, [rec])
        names = sorted(p.name for p in target.iterdir())
        check("la directory e' <workdir>/fonts", target == workdir / "fonts", target)
        check("contiene il font scelto", "Condensed-Bold.ttf" in names, names)
        check("contiene Antonio-Regular", "Antonio-Regular.ttf" in names, names)
        check("contiene Antonio-Bold", "Antonio-Bold.ttf" in names, names)
        check(
            "OFL.txt del bundle non viene copiato come font",
            "OFL.txt" not in names,
            names,
        )
        check(
            "il font scelto ha gli stessi byte",
            sha((target / "Condensed-Bold.ttf").read_bytes()) == rec.sha256,
        )
        bundle = fonts.package_font_dir()
        check(
            "i bundled hanno gli stessi byte del bundle",
            sha((target / "Antonio-Bold.ttf").read_bytes())
            == sha((bundle / "Antonio-Bold.ttf").read_bytes()),
        )

        snapshot = {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size, sha(p.read_bytes()))
            for p in target.iterdir()
        }
        fontlib.job_fonts_dir(workdir, [rec])
        check(
            "idempotente: nessuna riscrittura",
            {
                p.name: (p.stat().st_mtime_ns, p.stat().st_size, sha(p.read_bytes()))
                for p in target.iterdir()
            }
            == snapshot,
        )
        check(
            "nessun file scritto fuori da workdir",
            sorted(p.name for p in base.iterdir()) == parent_before,
            sorted(p.name for p in base.iterdir()),
        )
        check(
            "ogni destinazione e' dentro workdir",
            all(
                p.resolve().parent == (workdir / "fonts").resolve()
                for p in target.iterdir()
            ),
        )

        only = fontlib.job_fonts_dir(base / "work2", [rec], bundled=False)
        check(
            "bundled=False copia solo lo scelto",
            sorted(p.name for p in only.iterdir()) == ["Condensed-Bold.ttf"],
            sorted(p.name for p in only.iterdir()),
        )

        other = base / "elsewhere"
        other.mkdir()
        twin_a = other / "Twin.ttf"
        twin_a.write_bytes(font_bytes("d"))
        twin_b = base / "Twin.ttf"
        twin_b.write_bytes(font_bytes("e"))
        rec_a = store.add_file(twin_a, family="Twin A", licence="commercial")
        rec_b = store.add_file(twin_b, family="Twin B", licence="commercial")
        twins = fontlib.job_fonts_dir(base / "work3", [rec_a, rec_b], bundled=False)
        twin_names = sorted(p.name for p in twins.iterdir())
        check("due omonimi non si sovrascrivono", len(twin_names) == 2, twin_names)
        check(
            "il secondo omonimo porta lo sha nel nome",
            any(name.startswith("Twin-") for name in twin_names),
            twin_names,
        )

        relative = fontlib.FontRecord(
            family="Rel",
            licence="commercial",
            file="Rel.ttf",
            sha256="b" * 64,
        )
        try:
            fontlib.job_fonts_dir(base / "work4", [relative], bundled=False)
            check("file relativo rifiutato", False, "nessuna eccezione")
        except contract.VxError:
            check("file relativo rifiutato", True)


def test_matrix() -> None:
    print("\n-- licences_matrix")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        store = fontlib.Store(base / "store")
        expected = []
        for family, licence, tag in (
            ("Zeta Serif", "personal", "a"),
            ("Alpha Sans", "commercial", "b"),
            ("Mu Display", "unknown", "c"),
        ):
            src = base / f"{family.replace(' ', '-')}.ttf"
            src.write_bytes(font_bytes(tag))
            store.add_file(src, family=family, licence=licence)
            expected.append((licence, family))

        rows = store.licences_matrix()
        check("una riga per record", len(rows) == 3, len(rows))
        check(
            "ogni record espone la sua licence",
            {(row["family"], row["licence"]) for row in rows}
            == {(family, licence) for licence, family in expected},
            rows,
        )
        check(
            "righe ordinate",
            rows
            == sorted(
                rows, key=lambda r: (r["licence"], r["family"].lower(), r["sha256"])
            ),
        )
        check(
            "solo commercial e' auto_selectable",
            all(
                row["auto_selectable"] == (row["licence"] == "commercial")
                for row in rows
            ),
        )

        path = store.write_matrix(base / "LICENSES_MATRIX.md")
        text = path.read_text(encoding="utf-8")
        check("write_matrix scrive il file", path.is_file(), str(path))
        check(
            "il file nomina ogni famiglia",
            all(family in text for _, family in expected),
        )
        check(
            "il file nomina ogni licence",
            all(licence in text for licence, _ in expected),
        )


def main() -> int:
    test_record()
    test_store()
    test_select()
    test_job_fonts_dir()
    test_matrix()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
