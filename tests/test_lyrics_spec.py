"""La spec del testo animato: i dieci controlli che il caso Scendi ha pagato.

Il caso: sette versioni, e ogni giro rifiutato per una cosa che una macchina
poteva vedere prima del render -- una parola sbagliata, un effetto su un colpo su
due, una battuta che non torna, un loop che non chiude.

S1 - una spec buona passa: finestre, righe, enfasi, colpi, battute, testo.
S2 - una parola sbagliata si nomina, con il numero della parola.
S3 - due finestre sovrapposte sono un "no" secco (L3).
S4 - un'enfasi che non e' nella riga si vede (L6).
S5 - i colpi fuori finestra sono un "no" (L7).
S6 - le parole scritte e i loro colpi devono contare uguale (L8).
S7 - `bars * barLen` deve dare la durata della finestra, e i colpi per battuta
     devono tornare (L9).
S8 - un loop dichiarato con la coda sbagliata e' un "no" (L4); senza `loop`, un
     avviso.
S9 - `frames` incoerente e' un "no" (L2).
S10 - una riga che non sta nel quadro e' un avviso con la stima (L11).
S11 - quello che non si puo' misurare si dichiara `skip` col motivo, mai `ok`.
S12 - sul master: durata, loudness e loop si misurano davvero (L12-L14).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import lyrics  # noqa: E402
from vx.ffmpeg import ffmpeg  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def row(rows, ident):
    for item in rows:
        if item["id"] == ident:
            return item
    return {"status": "assente", "detail": ""}


def good_spec() -> dict:
    return {
        "fps": 25,
        "duration": 4.0,
        "frames": 100,
        "structure": {
            "head": {"master": [0.0, 1.7647], "bars": "5-8"},
            "tail": {"master": [2.0, 4.0], "bars": "1-2"},
            "loop": {"from_master": 4.0, "to_master": 0.0},
        },
        "events": [
            {
                "t0": 0.0,
                "t1": 1.7647,
                "style": "chant",
                "size": 180,
                "y": 340,
                "lines": ["SCENDI", "SCENDI"],
                "barLen": 0.88235,
                "bars": 2,
                "hits": [0.2, 0.7, 1.1, 1.6],
                "hitsPerBar": 2,
                "wordHits": [0.2, 1.1],
            },
            {
                "t0": 1.7647,
                "t1": 4.0,
                "style": "hero",
                "size": 96,
                "y": "low",
                "lines": [
                    {"text": "Ho gia' vissuto", "em": "VISSUTO"},
                    {"text": "questo posto"},
                ],
            },
        ],
    }


TEXT = "prima scendi scendi ho gia' vissuto questo posto e altro"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-lyrics-") as raw:
        work = Path(raw)

        # -- S1: la spec buona ---------------------------------------------- #
        rows = lyrics.check(good_spec(), text=TEXT)
        report = lyrics.summarise(rows)
        failed = [item for item in rows if item["status"] == "fail"]
        check("S1 la spec buona non ha fallimenti", not failed, failed)
        check("S1 le finestre sono pulite", row(rows, "L3")["status"] == "ok")
        check("S1 le battute tornano", row(rows, "L9")["status"] == "ok", row(rows, "L9"))
        check("S1 il testo combacia", row(rows, "L10")["status"] == "ok", row(rows, "L10"))
        check("S1 il loop chiude", row(rows, "L4")["status"] == "ok", row(rows, "L4"))
        check("S1 il report dice ok", report["ok"] is True, report["counts"])

        # -- S2: la parola sbagliata ---------------------------------------- #
        broken = good_spec()
        broken["events"][1]["lines"][1]["text"] = "questo pasta"
        rows = lyrics.check(broken, text=TEXT)
        detail = row(rows, "L10")
        check("S2 una parola sbagliata e' un no", detail["status"] == "fail", detail["detail"])
        check(
            "S2 il dettaglio nomina la parola sbagliata",
            "pasta" in detail["detail"] and "non compare" in detail["detail"],
            detail["detail"],
        )

        # -- S2b: la spec puo' saltare una parola, non sostituirla ---------- #
        omission = good_spec()
        omission["events"][1]["lines"][0]["text"] = "gia' vissuto"
        passed = row(lyrics.check(omission, text=TEXT), "L10")
        check(
            "S2b una parola saltata non e' un errore",
            passed["status"] == "ok",
            passed["detail"],
        )

        # -- S2c: il testo doppiamente codificato si ripristina -------------- #
        repaired, note = lyrics.repair_text("meglio cosÃ¬, perchÃ©")
        check(
            "S2c il mojibake si ripristina",
            repaired == "meglio così, perché" and "ripristinate" in note,
            (repaired, note),
        )

        # -- S3: finestre sovrapposte --------------------------------------- #
        overlap = good_spec()
        overlap["events"][1]["t0"] = 1.2
        check(
            "S3 una sovrapposizione e' un no",
            row(lyrics.check(overlap), "L3")["status"] == "fail",
            row(lyrics.check(overlap), "L3")["detail"],
        )

        # -- S4: enfasi fuori riga ------------------------------------------ #
        emphasis = good_spec()
        emphasis["events"][1]["lines"][0]["em"] = "POLTRONA"
        check(
            "S4 un'enfasi che non e' nella riga si vede",
            row(lyrics.check(emphasis), "L6")["status"] == "fail",
            row(lyrics.check(emphasis), "L6")["detail"],
        )

        # -- S5: colpi fuori finestra --------------------------------------- #
        outside = good_spec()
        outside["events"][0]["hits"] = [0.2, 0.7, 3.1]
        outside["events"][0]["wordHits"] = [0.2, 3.1]
        check(
            "S5 un colpo fuori finestra e' un no",
            row(lyrics.check(outside), "L7")["status"] == "fail",
            row(lyrics.check(outside), "L7")["detail"],
        )

        # -- S6: parole e colpi --------------------------------------------- #
        count = good_spec()
        count["events"][0]["wordHits"] = [0.2]
        check(
            "S6 una parola scritta senza il suo colpo si vede",
            row(lyrics.check(count), "L8")["status"] == "fail",
            row(lyrics.check(count), "L8")["detail"],
        )

        # -- S7: la matematica delle battute -------------------------------- #
        bars = good_spec()
        bars["events"][1]["barLen"] = 1.0
        bars["events"][1]["bars"] = 4  # 4 s su una finestra di 2.235 s
        check(
            "S7 battute e durata devono coincidere",
            row(lyrics.check(bars), "L9")["status"] == "fail",
            row(lyrics.check(bars), "L9")["detail"],
        )

        # -- S8: il loop dichiarato ----------------------------------------- #
        loop = good_spec()
        loop["events"][1]["t1"] = 3.8
        loop["duration"] = 4.0
        check(
            "S8 un loop dichiarato e non chiuso e' un no",
            row(lyrics.check(loop), "L4")["status"] == "fail",
            row(lyrics.check(loop), "L4")["detail"],
        )
        loose = good_spec()
        loose.pop("structure")
        loose["events"][1]["t1"] = 3.8
        check(
            "S8 senza loop dichiarato e' un avviso, non un no",
            row(lyrics.check(loose), "L4")["status"] == "warn",
            row(lyrics.check(loose), "L4")["detail"],
        )

        # -- S9: i fotogrammi ----------------------------------------------- #
        frames = good_spec()
        frames["frames"] = 90
        check(
            "S9 frames incoerente e' un no",
            row(lyrics.check(frames), "L2")["status"] == "fail",
            row(lyrics.check(frames), "L2")["detail"],
        )

        # -- S10: lo spazio ------------------------------------------------- #
        wide = good_spec()
        wide["events"][1]["size"] = 200
        wide["events"][1]["lines"][0]["text"] = "una riga davvero molto lunga che non ci sta"
        check(
            "S10 una riga che non sta nel quadro e' un avviso",
            row(lyrics.check(wide, canvas=(540, 960)), "L11")["status"] == "warn",
            row(lyrics.check(wide, canvas=(540, 960)), "L11")["detail"],
        )

        # -- S11: i controlli che non si possono fare ----------------------- #
        bare = lyrics.check(good_spec())
        for ident in ("L10", "L12", "L13", "L14"):
            check(
                f"S11 senza strumenti {ident} si dichiara skip",
                row(bare, ident)["status"] == "skip",
                row(bare, ident)["detail"],
            )

        # -- S12: il master -------------------------------------------------- #
        master = work / "loop.mp4"
        done = subprocess.run(
            [
                ffmpeg(),
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=160x160:r=25:d=4",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=24000:duration=4",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(master),
            ],
            capture_output=True,
            check=False,
        )
        if done.returncode != 0 or not master.is_file():
            print("  [SKIP] master di prova non generato:", done.stderr.decode("utf-8", "replace")[-200:])
        else:
            rows = lyrics.check(good_spec(), text=TEXT, master=master)
            check(
                "S12 la durata del master coincide",
                row(rows, "L12")["status"] == "ok",
                row(rows, "L12")["detail"],
            )
            check(
                "S12 il loop e' a filo (fondo nero, primo = ultimo)",
                row(rows, "L14")["status"] == "ok",
                row(rows, "L14")["detail"],
            )
            check(
                "S12 la loudness e' misurata, non supposta",
                row(rows, "L13")["status"] in ("ok", "fail"),
                row(rows, "L13")["detail"],
            )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
