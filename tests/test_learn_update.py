"""``vx learn update``: i giudizi diventano soglie, con la regola in chiaro.

Il 6 novembre 2026 ``vx learn policy`` mostrava ancora ``v0 (mai salvata)`` con
due valutazioni vere nel registro (voti 2 e 1, media 1.50/5): ``policy_save``
esisteva ma nessun comando la chiamava. Questi check provano il ponte:

S7a  con zero giudizi non si scrive niente e lo si dice (exit 0);
S7b  con i due giudizi veri la policy si scrive come v1 provvisoria, ``reason``
     cita il numero di voti, ``feedback_ids`` ne ha due, ``learn policy`` lo
     mostra;
S7c  il secondo ``update`` identico non tocca il file (digest uguale) e lo dice;
S7d  ``--dry-run`` mostra le soglie proposte e lascia il file dov'e' (mtime);
S7e  la regola dichiarata non inventa ``effects_density_min``: un voto 1 con
     ``effects: 0`` e ``balance: ok`` non abbassa ne' alza il tetto degli effetti.

Nessuna rete, nessun video: due righe JSON in una cartella temporanea.
python tests/test_learn_update.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import cli, feedback, learning  # noqa: E402

PASS = 0
FAIL = 0

#: Il contesto vero dei due giudizi del 4 novembre 2026: 1 inquadratura, 15 s,
#: nessun effetto, balance "ok". E' il caso che ha mosso il minimo dei tagli.
REAL_CONTEXT = {
    "outcome": "failed",
    "balance": "ok",
    "violations": ["weak_open", "unjustified_cut"],
    "effects": 0,
    "shots": 1,
    "duration_sec": 15.0,
}


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


@contextlib.contextmanager
def register_at(path: Path):
    """``learn policy`` legge il registro di default: qui glielo si sposta."""
    previous = os.environ.get(feedback.ROOT_ENV)
    os.environ[feedback.ROOT_ENV] = str(path)
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop(feedback.ROOT_ENV, None)
        else:
            os.environ[feedback.ROOT_ENV] = previous


def cli_run(argv: list) -> tuple:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def add_verdict(
    root: Path,
    rating: int,
    *,
    job: str,
    created: str,
    context: dict,
) -> None:
    """Una riga di registro vera, scritta dall'API di ``vx.feedback``."""
    feedback.append(
        feedback.make_record(
            job_id=job,
            prompt="prova",
            rating=rating,
            note="",
            context=context,
            created_utc=created,
        ),
        root=root,
    )


def policy_file(root: Path) -> Path:
    return root / "policy" / "policy.json"


# --------------------------------------------------------------------------- #
# S7a - zero giudizi: niente da imparare, niente scritto
# --------------------------------------------------------------------------- #


def test_zero_judgments(base: Path) -> None:
    archive = base / "zero-learning"
    register = base / "zero-registry"
    register.mkdir(parents=True, exist_ok=True)

    code, out, _err = cli_run(
        ["learn", "update", "--root", str(archive), "--feedback-root", str(register)]
    )
    check("S7a: exit 0 con zero giudizi", code == 0, code)
    check("S7a: dice che non c'e' niente da imparare", "niente da imparare" in out, out.strip())
    check("S7a: nessun file di policy scritto", not policy_file(archive).exists())
    check("S7a: nessuna cartella policy creata", not (archive / "policy").exists())
    check("S7a: la policy resta v0", learning.policy_load(root=archive)["version"] == 0)


# --------------------------------------------------------------------------- #
# S7b - i due giudizi veri scrivono la v1 provvisoria
# --------------------------------------------------------------------------- #


def test_real_judgments(base: Path) -> Path:
    archive = base / "real-learning"
    register = base / "real-registry"
    register.mkdir(parents=True, exist_ok=True)
    add_verdict(
        register,
        2,
        job="job2",
        created="2026-11-04T18:52:02+00:00",
        context=REAL_CONTEXT,
    )
    add_verdict(
        register,
        1,
        job="job2",
        created="2026-11-04T19:31:10+00:00",
        context={**REAL_CONTEXT, "outcome": "done"},
    )

    code, out, _err = cli_run(
        ["learn", "update", "--root", str(archive), "--feedback-root", str(register)]
    )
    policy = learning.policy_load(root=archive)
    check("S7b: exit 0", code == 0, code)
    check("S7b: policy scritta come v1", policy["version"] == 1, policy["version"])
    check("S7b: v1 e' provvisoria (2 < 10)", policy["provisional"] is True, policy["provisional"])
    check("S7b: reason cita 2 valutazioni", "2 valutazioni" in policy["reason"], policy["reason"])
    check("S7b: reason dice provvisoria", "provvisoria" in policy["reason"], policy["reason"])
    check(
        "S7b: feedback_ids ha 2 elementi distinti",
        len(policy["feedback_ids"]) == 2 and len(set(policy["feedback_ids"])) == 2,
        policy["feedback_ids"],
    )
    check(
        "S7b: cut_rate_min sale di un passo (0.18 -> 0.23)",
        policy["thresholds"]["cut_rate_min"] == 0.23,
        policy["thresholds"],
    )
    check(
        "S7b: le altre soglie restano di partenza",
        policy["thresholds"]["cut_rate_max"] == 0.75
        and policy["thresholds"]["effects_density_max"] == 0.35
        and policy["thresholds"]["variety_min"] == 0.55,
        policy["thresholds"],
    )
    check(
        "S7b: l'update stampa version, motivo e voti citati",
        "v1" in out and "2 valutazioni" in out and "giudizi citati: 2" in out,
        out.strip().splitlines()[:2],
    )
    history = json.loads(
        (archive / "policy" / "history" / "v001.json").read_text(encoding="utf-8")
    )
    check(
        "S7b: la storia ha l'hash delle righe citate",
        len(history["feedback_hashes"]) == 2
        and all(len(value) == 64 for value in history["feedback_hashes"].values()),
        history["feedback_hashes"],
    )
    check(
        "S7b: la storia nomina la soglia mossa",
        "thresholds.cut_rate_min" in history["diff"]["changed"],
        history["diff"],
    )

    with register_at(register):
        code, out, _err = cli_run(["learn", "policy", "--root", str(archive)])
    check(
        "S7b: learn policy mostra la v1 e le nuove soglie",
        code == 0 and "policy v1" in out and "0.23" in out,
        out.strip().splitlines()[:2],
    )
    check("S7b: learn policy dice provvisoria", "provvisoria" in out)
    return archive


# --------------------------------------------------------------------------- #
# S7c - idempotenza: stessi giudizi, stesso digest, file intatto
# --------------------------------------------------------------------------- #


def test_second_update_is_a_noop(archive: Path, register: Path) -> None:
    path = policy_file(archive)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    code, out, _err = cli_run(
        ["learn", "update", "--root", str(archive), "--feedback-root", str(register)]
    )
    after = path.read_bytes()
    check("S7c: exit 0", code == 0, code)
    check("S7c: il file non cambia (byte identici)", before == after)
    check("S7c: il mtime non cambia", mtime == path.stat().st_mtime_ns)
    check("S7c: lo dice (nessuna novita' e digest)", "nessuna novita'" in out and "digest" in out, out.strip())
    check("S7c: la versione resta 1", learning.policy_load(root=archive)["version"] == 1)

    # Anche la chiamata diretta a policy_save e' idempotente sullo stesso digest.
    ids = learning.policy_load(root=archive)["feedback_ids"]
    code_saved = learning.policy_save(
        {"thresholds": {"variety_min": 0.9}},
        reason="prova di idempotenza",
        feedback_ids=ids,
        root=archive,
    )
    check(
        "S7c: policy_save con lo stesso digest non riscrive",
        code_saved["version"] == 1
        and learning.policy_load(root=archive)["thresholds"]["variety_min"] == 0.55,
        code_saved["version"],
    )


# --------------------------------------------------------------------------- #
# S7d - dry-run: soglie proposte, mtime invariato
# --------------------------------------------------------------------------- #


def test_dry_run_does_not_write(archive: Path, register: Path) -> None:
    # Un terzo giudizio cambia il digest: la proposta e' nuova, la scrittura no.
    add_verdict(
        register,
        3,
        job="job3",
        created="2026-11-05T10:00:00+00:00",
        context={**REAL_CONTEXT, "outcome": "done"},
    )
    path = policy_file(archive)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    code, out, _err = cli_run(
        [
            "learn",
            "update",
            "--root",
            str(archive),
            "--feedback-root",
            str(register),
            "--dry-run",
        ]
    )
    check("S7d: exit 0", code == 0, code)
    check("S7d: il file non viene scritto (byte identici)", path.read_bytes() == before)
    check("S7d: il mtime resta invariato", mtime == path.stat().st_mtime_ns)
    check(
        "S7d: l'output elenca le soglie proposte",
        "cut_rate_min" in out and "cut_rate_max" in out and "variety_min" in out,
        out.strip().splitlines()[:1],
    )
    check("S7d: l'output dice che e' una proposta", "dry-run" in out and "non scritta" in out, out.strip())
    check("S7d: la versione resta 1", learning.policy_load(root=archive)["version"] == 1)


# --------------------------------------------------------------------------- #
# S7e - la regola dichiarata: nessuna effects_density_min inventata
# --------------------------------------------------------------------------- #


def test_effects_zero_does_not_lower_the_ceiling(base: Path) -> None:
    archive = base / "effects-learning"
    register = base / "effects-registry"
    register.mkdir(parents=True, exist_ok=True)
    add_verdict(
        register,
        1,
        job="job-effects",
        created="2026-11-05T11:00:00+00:00",
        context={"balance": "ok", "effects": 0, "shots": 3, "duration_sec": 15.0},
    )
    result = learning.learn_update(root=archive, feedback_root=register)
    thresholds = result["thresholds"]
    check(
        "S7e: effects: 0 con balance ok non abbassa effects_density_max",
        thresholds["effects_density_max"] == learning.DEFAULT_THRESHOLDS["effects_density_max"],
        thresholds,
    )
    check(
        "S7e: la regola non introduce effects_density_min",
        "effects_density_min" not in thresholds,
        sorted(thresholds),
    )
    check(
        "S7e: cut_rate 0.2 e' dentro i limiti: nessuna soglia si muove",
        result["moves"] == [] and thresholds["cut_rate_min"] == 0.18,
        result["moves"],
    )
    check(
        "S7e: reason cita 1 valutazione al singolare",
        "1 valutazione" in result["reason"] and "provvisoria" in result["reason"],
        result["reason"],
    )


# --------------------------------------------------------------------------- #
# interfaccia di policy_load e la soglia dei 10
# --------------------------------------------------------------------------- #


def test_policy_contract(base: Path) -> None:
    fresh = learning.policy_load(root=base / "contract")
    required = {"version", "thresholds", "reason", "feedback_ids", "provisional"}
    check("policy_load: le cinque chiavi promesse ci sono", required <= set(fresh), sorted(fresh))
    check(
        "policy_load: tipi dichiarati",
        isinstance(fresh["version"], int)
        and isinstance(fresh["thresholds"], dict)
        and isinstance(fresh["reason"], str)
        and isinstance(fresh["feedback_ids"], list)
        and isinstance(fresh["provisional"], bool),
        {key: type(fresh[key]).__name__ for key in required},
    )
    check("policy_load: la regola e' ispezionabile", "cut_rate" in str(fresh.get("rule", "")))


def test_ten_judgments_are_not_provisional(base: Path) -> None:
    archive = base / "ten-learning"
    register = base / "ten-registry"
    register.mkdir(parents=True, exist_ok=True)
    for index in range(10):
        add_verdict(
            register,
            4,
            job=f"job-{index}",
            created=f"2026-11-0{1 + index // 5}T1{index % 5}:00:00+00:00",
            context={"balance": "low", "effects": 1, "shots": 4, "duration_sec": 30.0},
        )
    result = learning.learn_update(root=archive, feedback_root=register)
    check(
        "10 giudizi: la calibrazione non e' piu' provvisoria",
        result["provisional"] is False and "calibrazione" in result["reason"],
        result["reason"],
    )
    check(
        "10 giudizi: il minimo dei tagli si allenta (voto alto sotto il minimo)",
        result["thresholds"]["cut_rate_min"] == 0.13,
        result["thresholds"],
    )
    check(
        "10 giudizi: balance low gradito abbassa variety_min",
        result["thresholds"]["variety_min"] == 0.50,
        result["thresholds"],
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-learn-update-") as tmp:
        base = Path(tmp)
        test_zero_judgments(base)
        archive = test_real_judgments(base)
        test_second_update_is_a_noop(archive, base / "real-registry")
        test_dry_run_does_not_write(archive, base / "real-registry")
        test_effects_zero_does_not_lower_the_ceiling(base)
        test_policy_contract(base)
        test_ten_judgments_are_not_provisional(base)
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
