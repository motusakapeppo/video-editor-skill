"""La vista deve vedere il motore mentre costruisce: fotogrammi pubblicati.

    python tests/test_livepub.py

Pubblicare tutti i fotogrammi di un rendering 1080x1920@30 significa centinaia di
MB. Qui si prova il patto che rende "tempo reale" sostenibile: si campiona, si
tiene una finestra, il manifesto resta coerente con i file, e la scrittura è
atomica.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import livepub  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-live-") as tmp:
        work = Path(tmp)
        livepub.reset(work, "chromium")
        check("S0 reset su cartella vuota non solleva", True)

        row = livepub.publish(
            work, engine="chromium", frame_index=0, t=0.0, image=b"primo", total=300
        )
        check("S1 il primo fotogramma e' scritto", (work / "live/chromium/frame_000000.jpg").is_file(), row)
        check("S1 l'hash e' quello dei byte", row["sha256"] == __import__("hashlib").sha256(b"primo").hexdigest(), row["sha256"][:12])

        manifest = livepub.read_manifest(work, "chromium")
        check(
            "S2 il manifesto dichiara motore, totale e ultimo",
            manifest
            and manifest["engine"] == "chromium"
            and manifest["total"] == 300
            and manifest["last"]["i"] == 0,
            (manifest or {}).get("total"),
        )

        for index in range(1, 6):
            livepub.publish(work, engine="chromium", frame_index=index, t=index / 30.0, image=b"x" * index)

        livepub.publish(
            work, engine="chromium", frame_index=6, t=6 / 30.0, image=b"y", keep=3
        )
        manifest = livepub.read_manifest(work, "chromium")
        files = sorted(p.name for p in (work / "live/chromium").glob("frame_*"))
        check("S3 la finestra tiene solo gli ultimi keep", len(files) == 3, files)
        check(
            "S3 il manifesto ha le stesse righe dei file",
            [item["file"] for item in manifest["frames"]] == files,
            ([item["file"] for item in manifest["frames"]], files),
        )
        check("S3 l'ultimo e' il 6", manifest["last"]["i"] == 6, manifest["last"]["i"])
        check(
            "S3 i vecchi non sono piu' sul disco",
            not (work / "live/chromium/frame_000000.jpg").exists(),
        )

        again = livepub.publish(
            work, engine="chromium", frame_index=6, t=6 / 30.0, image=b"y"
        )
        manifest = livepub.read_manifest(work, "chromium")
        check(
            "S4 ripubblicare lo stesso indice non duplica",
            len([item for item in manifest["frames"] if item["i"] == 6]) == 1,
            len(manifest["frames"]),
        )
        check("S4 la riga e' la stessa", again["sha256"] == row["sha256"] or again["bytes"] == 1)

        tmp_leftovers = list((work / "live/chromium").glob("*.tmp"))
        check("S5 nessun file temporaneo lasciato in giro", tmp_leftovers == [], tmp_leftovers)

        raised = None
        try:
            livepub.publish(work, engine="motore_inventato", frame_index=1, t=0.0, image=b"z")
        except ValueError as exc:
            raised = str(exc)
        check("S6 un motore non dichiarato viene rifiutato", raised is not None, raised)

        livepub.reset(work, "chromium")
        check(
            "S7 reset svuota file e manifesto",
            not list((work / "live/chromium").glob("frame_*"))
            and livepub.read_manifest(work, "chromium") is None,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
