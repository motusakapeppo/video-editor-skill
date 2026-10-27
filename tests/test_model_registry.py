"""Model registry: pinned bytes, hard refusal on any drift, no network."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import models  # noqa: E402
from vx.contract import VxError  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:62} {detail}")


def skip(label: str, reason: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {label:62} {reason}")


def _payload(exc: BaseException) -> dict:
    payload = getattr(exc, "payload", None)
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    known = models.record_for("silero_vad")
    check(
        "record_for espone i metadati",
        all(
            key in known
            for key in ("name", "path_hint", "sha256", "size", "source_url", "licence")
        ),
        sorted(known),
    )
    check("record_for copia, non alias", known is not models.RECORDS["silero_vad"])

    silero = ROOT / "assets" / "models" / "silero_vad.onnx"
    if not silero.is_file():
        skip("verify su modello reale", f"assente: {silero}")
    else:
        verified = models.verify("silero_vad", silero)
        check(
            "hash approvato verifica", verified["matches"] is True, verified["sha256"]
        )
        check(
            "dimensione approvata",
            verified["size"] == models.RECORDS["silero_vad"]["size"],
            verified["size"],
        )

        with tempfile.TemporaryDirectory(prefix="vx-model-") as raw:
            flipped = Path(raw) / "flipped.onnx"
            data = bytearray(silero.read_bytes())
            data[len(data) // 2] ^= 0xFF
            flipped.write_bytes(bytes(data))
            try:
                models.verify("silero_vad", flipped)
            except VxError as exc:
                payload = _payload(exc)
                check(
                    "byte alterato rifiutato",
                    payload.get("expected") != payload.get("actual"),
                    str(exc)[:60],
                )
                check(
                    "payload expected/actual/size",
                    {"model", "expected", "actual", "size"} <= set(payload),
                    sorted(payload),
                )
                check(
                    "actual è l'hash del file alterato",
                    payload.get("actual") == hashlib.sha256(bytes(data)).hexdigest(),
                    str(payload.get("actual", ""))[:16],
                )
            except BaseException as exc:  # noqa: BLE001
                check("byte alterato rifiutato", False, type(exc).__name__)
            else:
                check("byte alterato rifiutato", False, "nessuna eccezione")

            ghost = Path(raw) / "ghost.onnx"
            try:
                models.verify("silero_vad", ghost)
            except VxError as exc:
                check(
                    "file assente: verify rifiuta",
                    "no file" in str(exc),
                    _payload(exc).get("actual"),
                )
            except BaseException as exc:  # noqa: BLE001
                check("file assente: verify rifiuta", False, type(exc).__name__)
            else:
                check("file assente: verify rifiuta", False, "nessuna eccezione")

            absent = models.probe_all(Path(raw))
            check("probe_all: una voce per record", len(absent) == len(models.RECORDS))
            check(
                "probe_all: nomi = chiavi del registro",
                {entry["name"] for entry in absent} == set(models.RECORDS),
            )
            check(
                "probe_all: assente -> present False",
                all(entry["present"] is False for entry in absent),
            )
            check(
                "probe_all: chiavi richieste",
                all(
                    {"name", "present", "matches", "expected", "actual"} <= set(entry)
                    for entry in absent
                ),
            )

    installed = models.probe_all(ROOT)
    check("probe_all(repo): una voce per record", len(installed) == len(models.RECORDS))
    by_name = {entry["name"]: entry for entry in installed}
    for name in ("silero_vad", "face_detection_yunet_2023mar"):
        entry = by_name.get(name) or {}
        check(
            f"probe_all(repo): {name} approvato",
            entry.get("present") is True and entry.get("matches") is True,
            entry.get("actual", "")[:16],
        )

    try:
        models.record_for("not_a_model")
    except VxError as exc:
        check("nome sconosciuto: record_for rifiuta", "unknown model" in str(exc))
    except BaseException as exc:  # noqa: BLE001
        check("nome sconosciuto: record_for rifiuta", False, type(exc).__name__)
    else:
        check("nome sconosciuto: record_for rifiuta", False, "nessuna eccezione")

    try:
        models.verify("not_a_model", ROOT / "LICENSE")
    except VxError:
        check("nome sconosciuto: verify rifiuta", True)
    except BaseException as exc:  # noqa: BLE001
        check("nome sconosciuto: verify rifiuta", False, type(exc).__name__)
    else:
        check("nome sconosciuto: verify rifiuta", False, "nessuna eccezione")

    print(f"\n{PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
