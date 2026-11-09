"""I movimenti del corpo: 1-3, letti dalla densità di taglio misurata.

    python tests/test_movements.py

M1  montaggio fitto (>= 0,50 inquadrature/s): tre movimenti;
M2  montaggio medio (>= 0,25): due movimenti;
M3  montaggio rado: un movimento solo;
M4  i confini cadono su un taglio: il ``to`` di un movimento è il ``from`` del
    successivo, e la somma dei secondi è la durata del corpo;
M5  nessuna inquadratura: nessun movimento (non si inventa una struttura).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import sections  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def clip(dst_in: float, length: float) -> SimpleNamespace:
    return SimpleNamespace(dst_in=dst_in, dst_out=dst_in + length, duration=length, layer=0)


def plan(clips) -> SimpleNamespace:
    return SimpleNamespace(video_tracks=(SimpleNamespace(clips=tuple(clips)),))


def main() -> int:
    print("\n1. M1 montaggio fitto: tre movimenti")
    dense = sections.movements(plan([clip(i * 0.5, 0.5) for i in range(8)]))
    check("M1 tre movimenti", len(dense) == 3, dense)
    check(
        "M1 ogni movimento ha inquadrature",
        all(item["shots"] >= 1 for item in dense),
        [item["shots"] for item in dense],
    )
    check(
        "M1 la densità è quella del corpo",
        abs(dense[0]["density"] - 2.0) <= 1e-6,
        dense[0]["density"],
    )

    print("\n2. M2 montaggio medio: due movimenti")
    medium = sections.movements(plan([clip(0.0, 4.0), clip(4.0, 4.0)]))
    check("M2 due movimenti", len(medium) == 2, medium)
    check(
        "M2 uno shot per movimento",
        [item["shots"] for item in medium] == [1, 1],
        [item["shots"] for item in medium],
    )

    print("\n3. M3 montaggio rado: un movimento")
    sparse = sections.movements(plan([clip(0.0, 5.0)]))
    check("M3 un movimento", len(sparse) == 1, sparse)
    check("M3 copre tutta la durata", sparse[0]["to"] == 5.0, sparse[0])

    print("\n4. M4 confini sui tagli e durata conservata")
    body = sections.movements(plan([clip(i * 0.4, 0.4) for i in range(10)]))
    check(
        "M4 i confini sono contigui",
        all(
            abs(a["to"] - b["from"]) <= 1e-6
            for a, b in zip(body, body[1:])
        ),
        [(item["from"], item["to"]) for item in body],
    )
    check(
        "M4 la somma dei secondi è la durata",
        abs(sum(item["seconds"] for item in body) - 4.0) <= 1e-6,
        [item["seconds"] for item in body],
    )
    check(
        "M4 il primo parte da zero e l'ultimo chiude",
        abs(body[0]["from"]) <= 1e-6 and abs(body[-1]["to"] - 4.0) <= 1e-6,
        (body[0]["from"], body[-1]["to"]),
    )

    print("\n5. M5 nessuna inquadratura: nessun movimento")
    check("M5 piano vuoto", sections.movements(plan([])) == [], sections.movements(plan([])))

    print("\n6. M6 la sezione beat porta i movimenti")
    block = sections.compile_sections(
        {
            "measured": {"duration_sec": 4.0, "clips": 10},
            "coherence": {"metrics": {"shots": 10}},
            "movements": body,
        }
    )
    beat = next(row for row in block["sections"] if row["id"] == "beat")
    check(
        "M6 binding con i movimenti",
        (beat.get("binding") or {}).get("movements") == body,
        beat.get("binding"),
    )
    check(
        "M6 e il perche' li cita",
        "movimenti" in str(beat.get("why")) and "inquadrature" in str(beat.get("why")),
        beat.get("why"),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
