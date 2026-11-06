"""vx.runlog — il contratto di stato: una riga, una firma, una catena.

    python tests/test_runlog.py

No ffmpeg, no render: solo file e JSON. Ogni check e' un fatto sul log.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import runlog  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:66} {detail}")


def _line_count(workdir: Path) -> int:
    return len(
        [line for line in runlog.path(workdir).read_text(encoding="utf-8").split("\n") if line]
    )


def _sign_all(workdir: Path) -> None:
    for index, stage in enumerate(runlog.STAGES, start=1):
        runlog.append(
            workdir,
            stage=stage,
            event="sign",
            contract=f"contratto {stage}",
            value=index,
            threshold=None if index < 3 else float(index),
            ok=True,
            why=f"{stage} misurato {index}",
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-runlog-") as raw:
        work = Path(raw)

        # -- percorso e forma della riga ---------------------------------- #
        check(
            "path e' <workdir>/.vx/run.jsonl",
            runlog.path(work) == work / ".vx" / "run.jsonl",
            runlog.path(work),
        )
        check("read su lavoro vuoto -> lista vuota", runlog.read(work) == [])
        check(
            "verify su lavoro vuoto: mancano tutti",
            runlog.verify(work)["missing"] == list(runlog.STAGES),
        )

        first = runlog.append(
            work,
            stage="doctor",
            event="sign",
            contract="verdetto != broken",
            value="ready",
            threshold=None,
            ok=True,
            why="ambiente pronto",
        )
        check("prima riga: v == 1", first.get("v") == 1, first.get("v"))
        check("prima riga: seq == 1", first.get("seq") == 1, first.get("seq"))
        check("sha della riga presente", len(str(first.get("sha"))) == 64, first.get("sha"))
        check("una riga su disco", _line_count(work) == 1, _line_count(work))
        check(
            "threshold null resta null",
            json.loads(runlog.path(work).read_text(encoding="utf-8"))["threshold"] is None,
        )
        raw_first = runlog.path(work).read_bytes()

        second = runlog.append(
            work,
            stage="ingest",
            event="sign",
            contract="asset usati >= 1",
            value=5,
            threshold=None,
            ok=True,
            why="5 asset usati",
        )
        check("seconda riga: seq == 2", second.get("seq") == 2, second.get("seq"))
        check(
            "append non riscrive la prima riga",
            runlog.path(work).read_bytes().startswith(raw_first),
        )
        check("due righe su disco", _line_count(work) == 2, _line_count(work))
        check("read restituisce due eventi", len(runlog.read(work)) == 2)
        check(
            "checksum catena: sha dipende dalla precedente",
            second["sha"] != first["sha"],
        )
        check(
            "signed contiene gli stadi firmati",
            runlog.signed(work) == {"doctor", "ingest"},
            runlog.signed(work),
        )

        # -- otto stadi, verify verde ------------------------------------- #
        _sign_all(work)
        verdict = runlog.verify(work)
        check("verify: ok con gli 8 stadi", verdict["ok"] is True, verdict)
        check("verify: stages ordinati come il contratto", verdict["stages"] == list(runlog.STAGES))
        check("verify: nessuno mancante", verdict["missing"] == [])
        check("verify: nessuna corruzione", verdict["corrupt"] is None)
        check("signed: 8 stadi", runlog.signed(work) == set(runlog.STAGES))
        check("eventi totali 10 (8 stadi + 2 iniziali)", len(runlog.read(work)) == 10)

        # -- ultima riga tronca: si scarta -------------------------------- #
        data = runlog.path(work).read_bytes()
        broken_tail = data[:-12]
        runlog.path(work).write_bytes(broken_tail)
        check(
            "ultima riga tronca: scartata, il resto si legge",
            len(runlog.read(work)) == 9,
            len(runlog.read(work)),
        )
        after = runlog.append(
            work,
            stage="deliver",
            event="sign",
            contract="contrasto >= 4.5",
            value=7.91,
            threshold=4.5,
            ok=True,
            why="contrasto 7.91 >= 4.5",
        )
        check(
            "append dopo coda tronca: seq riparte dal valido",
            after.get("seq") == 10,
            after.get("seq"),
        )
        check(
            "append dopo coda tronca: il log torna valido",
            runlog.verify(work)["ok"] is True,
            runlog.verify(work),
        )

        # -- riga centrale corrotta: errore dichiarato -------------------- #
        middle = work / "middle"
        _sign_all(middle)
        lines = runlog.path(middle).read_text(encoding="utf-8").splitlines()
        lines[1] = "{ questa non e' una riga json"
        runlog.path(middle).write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            runlog.read(middle)
            middle_ok = False
        except runlog.RunLogCorrupt as exc:
            middle_ok = exc.line == 2 and "riga 2" in str(exc)
        check("riga centrale non JSON -> RUN_LOG_CORRUPT riga 2", middle_ok)

        # -- checksum invalido: errore dichiarato -------------------------- #
        checksum = work / "checksum"
        _sign_all(checksum)
        lines = runlog.path(checksum).read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[3])
        record["value"] = 999  # payload cambiato, sha vecchio
        lines[3] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        runlog.path(checksum).write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            runlog.read(checksum)
            checksum_ok = False
        except runlog.RunLogCorrupt as exc:
            checksum_ok = exc.line == 4 and "checksum" in str(exc)
        check("checksum invalido -> RUN_LOG_CORRUPT riga 4", checksum_ok)
        check(
            "verify dichiara la corruzione",
            runlog.verify(checksum)["corrupt"] is not None
            and runlog.verify(checksum)["ok"] is False,
            runlog.verify(checksum)["corrupt"],
        )

        # -- schema e stadi sconosciuti ------------------------------------ #
        schema = work / "schema"
        _sign_all(schema)
        lines = runlog.path(schema).read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["v"] = 2
        lines[0] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        runlog.path(schema).write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            runlog.read(schema)
            schema_ok = False
        except runlog.RunLogCorrupt:
            schema_ok = True
        check("schemaVersion diversa -> RUN_LOG_CORRUPT", schema_ok)

        try:
            runlog.append(work, stage="montaggio", event="sign", contract="x", value=1, ok=True)
            stage_ok = False
        except ValueError:
            stage_ok = True
        check("stadio fuori contratto rifiutato", stage_ok)

        check(
            "nessun file temporaneo lasciato",
            list((work / ".vx").glob("*.tmp.*")) == [],
        )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
