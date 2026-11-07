"""La vista mostra il motore mentre costruisce: manifesto e fotogrammi.

    python tests/test_scene_live.py

La striscia MOTORE della pagina legge `/api/scene` e le immagini da
`/scene/chromium/frame_000003.jpg`. Qui si prova la parte server: il manifesto
diventa stato leggibile, senza percorso scelto dal client e senza inventare
niente quando il motore non ha ancora pubblicato.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import livepub, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-scene-live-") as tmp:
        work = Path(tmp)
        empty = serve.scene_live(work)
        check("S1 senza manifesto: engine None", empty["engine"] is None, empty)
        check("S1 e nessun fotogramma inventato", empty["frames"] == [] and empty["last"] is None, empty)

        livepub.publish(
            work, engine="chromium", frame_index=0, t=0.0, image=b"uno", total=4
        )
        livepub.publish(
            work, engine="chromium", frame_index=1, t=0.33, image=b"due", total=4
        )
        state = serve.scene_live(work)
        check("S2 il motore e' dichiarato", state["engine"] == "chromium", state["engine"])
        check("S2 due fotogrammi nel manifesto", len(state["frames"]) == 2, len(state["frames"]))
        check(
            "S2 ogni fotogramma ha una url servibile",
            all(item["url"].startswith("/scene/chromium/") for item in state["frames"]),
            [item["url"] for item in state["frames"]],
        )
        check(
            "S2 l'ultimo e' il piu' recente, con tempo e hash",
            state["last"]["i"] == 1
            and state["last"]["t"] == 0.33
            and len(state["last"]["sha256"]) == 64,
            state["last"],
        )
        check(
            "S3 il manifesto su disco non e' riscritto dalla lettura",
            json.loads((work / "live" / "chromium" / "manifest.json").read_text(encoding="utf-8"))["last"]["i"] == 1,
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
