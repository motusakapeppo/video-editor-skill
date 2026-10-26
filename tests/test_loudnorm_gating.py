"""Lo stage 04 esiste solo se il piano ha chiesto loudnorm.

Se nessun finding chiede la normalizzazione, il render non la inventa: rimuxa
con -c copy e registra 04_remux. Il gate verify_treatments impedisce che un
trattamento dichiarato resti senza esecuzione.

python tests/test_loudnorm_gating.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import contract, render  # noqa: E402

PASS = 0
FAIL = 0
CALLS: list[list[str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:56} {detail}")


class _Proc:
    ok = True
    rc = 0
    stdout = ""
    stderr = ""

    def tail(self, lines: int = 25) -> str:
        return ""


def _install_fakes() -> None:
    def fake_run_ff(args, **kwargs):
        argv = [str(a) for a in args]
        CALLS.append(argv)
        target = Path(argv[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        return _Proc()

    render.ff.run_ff = fake_run_ff
    render._measure_loudness = lambda path: {}
    render._frame_count = lambda path: 0
    render._encode_args = lambda plan, precision: ["-c:v", "libx264"]


def _run(plan: contract.EditPlan, root: Path) -> dict:
    source = root / "src.mp4"
    source.write_bytes(b"")
    media = {
        "path": str(source),
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "has_audio": True,
        "duration": 4.0,
    }
    return render.render(plan, media, precision="proxy", workdir=root / "work")


def _plan(treatments: list[dict]) -> contract.EditPlan:
    return contract.EditPlan(
        output={"aspect": "9:16", "loudness": "social"},
        cuts=[{"in": 0.0, "out": 2.0}],
        render={"precision": "proxy"},
        treatments=treatments,
    )


def main() -> int:
    _install_fakes()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        CALLS.clear()
        enabled = _run(
            _plan(
                [
                    {
                        "name": "loudnorm",
                        "enabled": True,
                        "params": {"I": -14.0, "TP": -1.0, "LRA": 11.0},
                    }
                ]
            ),
            root,
        )
        check(
            "loudnorm attivo -> 04_loudnorm",
            "04_loudnorm" in enabled["stages"],
            str(enabled["stages"]),
        )
        check("loudnorm attivo -> niente 04_remux", "04_remux" not in enabled["stages"])
        script04 = root / "work" / "04_loudnorm.txt"
        check("il grafo loudnorm e' su disco", script04.exists())
        check(
            "il grafo loudnorm contiene il filtro",
            script04.exists() and "loudnorm=" in script04.read_text(encoding="utf-8"),
        )
        last = CALLS[-1]
        check("stage 04 usa -filter_complex_script", "-filter_complex_script" in last)
        check("stage 04 non usa -af", "-af" not in last)
        check("stage 04 mappa [aout]", "[aout]" in last)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        CALLS.clear()
        disabled = _run(_plan([]), root)
        check(
            "loudnorm assente -> 04_remux",
            "04_remux" in disabled["stages"],
            str(disabled["stages"]),
        )
        check(
            "loudnorm assente -> niente 04_loudnorm",
            "04_loudnorm" not in disabled["stages"],
        )
        check(
            "nessun 04_loudnorm.txt", not (root / "work" / "04_loudnorm.txt").exists()
        )
        last = CALLS[-1]
        check(
            "il remux usa -c copy", "-c" in last and "copy" in last, " ".join(last[-6:])
        )
        check("il remux non filtra", "-filter_complex_script" not in last)

    compress = contract.EditPlan(
        treatments=[{"name": "compress", "enabled": True, "params": {}}]
    )
    raised = False
    payload = None
    try:
        render.verify_treatments(compress, ["[0:a]aresample=48000[aout]"])
    except contract.RenderFailure as exc:
        raised = True
        payload = exc.args[0] if exc.args else None
    check("token mancante -> RenderFailure", raised)
    check(
        "payload step",
        isinstance(payload, dict) and payload.get("step") == "verify_treatments",
        str(payload),
    )
    check(
        "payload treatment",
        isinstance(payload, dict) and payload.get("treatment") == "compress",
    )
    check(
        "payload missing",
        isinstance(payload, dict) and payload.get("missing") == "acompressor",
    )

    ok = True
    try:
        render.verify_treatments(
            compress, ["[0:a]acompressor=threshold=-18.0:ratio=3.00[aout]"]
        )
    except contract.RenderFailure:
        ok = False
    check("token presente -> nessuna eccezione", ok)

    off = contract.EditPlan(
        treatments=[{"name": "compress", "enabled": False, "params": {}}]
    )
    ok = True
    try:
        render.verify_treatments(off, ["[0:a]aresample=48000[aout]"])
    except contract.RenderFailure:
        ok = False
    check("trattamento spento -> nessuna eccezione", ok)

    exempt = contract.EditPlan(
        treatments=[{"name": "cut_silence", "enabled": True, "params": {}}]
    )
    ok = True
    try:
        render.verify_treatments(exempt, ["[0:a]aresample=48000[aout]"])
    except contract.RenderFailure:
        ok = False
    check("cut_silence e' esente", ok)

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
