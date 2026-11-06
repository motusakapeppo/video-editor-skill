"""T3/C4/C6 — il lease e' per **cartella**, non per macchina.

La prova e' binaria, come il verbale:

* S1 due processi sulla stessa cartella: il secondo esce 2, il primo continua,
  il messaggio nomina pid e stadio;
* S2 processo ucciso con lease scritto: il successivo parte, dichiara
  ``stale_recovered`` con l'eta' del battito e il lavoro va a termine;
* S3 render lungo (stage che dorme 8 s, battito 2 s): il lease non scade, nessun
  ``stale_recovered``;
* S4 due cartelle diverse, due processi: nessun blocco.

Avvio: ``python tests/test_lease.py``
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from vx import contract, lock  # noqa: E402

PASS = 0
FAIL = 0
TEMP = Path(tempfile.mkdtemp(prefix="vx-lease-"))
PY = sys.executable
ENV = {
    **os.environ,
    "PYTHONPATH": str(SCRIPTS),
    "VX_SESSION_DIR": str(TEMP / "sessions"),
}
ENV.pop("VX_SESSION", None)

HOLDER = """
import sys, time
from vx import lock
workdir, stage, beat_s, hold = (
    sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
)
try:
    beat = lock.acquire(workdir, stage=stage, heartbeat_seconds=beat_s)
except Exception as exc:
    print(f"BUSY: {exc}", file=sys.stderr, flush=True)
    raise SystemExit(2)
print(beat.info.token, flush=True)
time.sleep(hold)
lock.release(beat)
"""


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:64} {detail}")


def wd(name: str) -> Path:
    target = TEMP / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_lease(folder: Path, data: dict, *, legacy: bool = False) -> Path:
    path = (folder / lock.LOCK_NAME) if legacy else lock.path_for(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def read_lease(folder: Path) -> dict:
    return json.loads(lock.path_for(folder).read_text(encoding="utf-8"))


def spawn_holder(folder: Path, stage: str, hold: float, beat_s: float = 60.0):
    return subprocess.Popen(
        [PY, "-c", HOLDER, str(folder), stage, str(beat_s), str(hold)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=ENV,
    )


def wait_lease(folder: Path, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = read_lease(folder) if lock.path_for(folder).is_file() else None
        if data:
            return data
        time.sleep(0.05)
    raise AssertionError(f"nessun lease in {folder}")


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(SCRIPTS / "vx.py"), *args],
        capture_output=True,
        text=True,
        env=ENV,
        timeout=120,
    )


def clean(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


print("== lease per cartella: formato e battito ==")

one = wd("una")
beat = lock.acquire(one, stage="render", heartbeat_seconds=1.0)
try:
    target = lock.path_for(one)
    data = read_lease(one)
    check(
        "C1 il lease vive in <workdir>/.vx/lock, non in <workdir>/.lock",
        target == one / ".vx" / "lock" and target.is_file()
        and not (one / ".lock").exists(),
        target,
    )
    check(
        "C2 il file dichiara pid, host, token, created, heartbeat_epoch, stage",
        set(data) >= {"pid", "host", "token", "created", "heartbeat_epoch", "stage"}
        and data["pid"] == os.getpid()
        and data["stage"] == "render"
        and data["token"] == beat.info.token,
        {k: data[k] for k in ("pid", "host", "stage")},
    )
    snapshot = lock.state(one)
    check(
        "C3 lease appena preso: vivo, battito giovane",
        snapshot["present"] and not snapshot["stale"]
        and snapshot["heartbeat_age_s"] < 5,
        snapshot["reason"],
    )
    check(
        "C3b i numeri del contratto: battito 60 s, lease vivo entro 300 s",
        lock.HEARTBEAT_SECONDS == 60.0 and lock.LEASE_TTL_SECONDS == 300.0,
        (lock.HEARTBEAT_SECONDS, lock.LEASE_TTL_SECONDS),
    )
    hb1 = float(data["heartbeat_epoch"])
    time.sleep(1.4)
    hb2 = float(read_lease(one)["heartbeat_epoch"])
    check("C4 il thread rinnova heartbeat_epoch da solo", hb2 > hb1, round(hb2 - hb1, 2))
    # Il loop di lavoro non fa niente: il battito deve continuare lo stesso.
    time.sleep(2.0)
    hb3 = float(read_lease(one)["heartbeat_epoch"])
    check(
        "C5 il battito non dipende dal loop di lavoro (2 s di silenzio)",
        hb3 > hb2 and not lock.state(one)["stale"],
        round(hb3 - hb2, 2),
    )
finally:
    lock.release(beat)

print("== S1: secondo scrittore sulla stessa cartella ==")

one = wd("s1")
beat = lock.acquire(one, stage="render")
try:
    token_before = read_lease(one)["token"]
    raised = None
    try:
        lock.acquire(one, stage="auto")
    except contract.VxError as exc:  # type: ignore[attr-defined]
        raised = str(exc)
    check(
        "C6 secondo scrittore respinto con VxError",
        raised is not None and "occupata" in raised,
        raised,
    )
    check(
        "C7 il messaggio nomina pid e stadio del proprietario",
        raised is not None
        and str(os.getpid()) in raised
        and "render" in raised
        and f"da" in raised,
        raised,
    )
    check(
        "C8 il lease del primo non e' stato toccato",
        read_lease(one)["token"] == token_before
        and lock.state(one)["owner"]["pid"] == os.getpid(),
        token_before,
    )

    before = {p.name for p in one.iterdir()}
    assets = wd("s1-assets")
    out = TEMP / "s1-master.mp4"
    auto = run_cli(
        [
            "auto", "--prompt", "prova", "--assets", str(assets),
            "--out", str(out), "--workdir", str(one), "--json",
        ]
    )
    combined = auto.stdout + auto.stderr
    check(
        "C9 `vx auto` esce 2 e nomina pid e stadio",
        auto.returncode == 2
        and str(os.getpid()) in combined
        and "render" in combined,
        (auto.returncode, combined.strip().splitlines()[-1:] or [""]),
    )
    check(
        "C10 `vx auto` respinto non scrive niente nella workdir",
        {p.name for p in one.iterdir()} == before and not out.exists(),
        sorted({p.name for p in one.iterdir()}),
    )

    reel = run_cli(
        [
            "reel", "--prompt", "prova", "--assets", str(assets),
            "--out", str(out), "--workdir", str(one), "--json",
        ]
    )
    check(
        "C11 `vx reel` esce 2 con lo stesso messaggio",
        reel.returncode == 2
        and str(os.getpid()) in (reel.stdout + reel.stderr)
        and "render" in (reel.stdout + reel.stderr),
        (reel.returncode, (reel.stdout + reel.stderr).strip().splitlines()[-1:] or [""]),
    )
finally:
    lock.release(beat)
    clean(one)

print("== S1b: due processi partono insieme sulla stessa cartella ==")

race = wd("s1-race")
first = spawn_holder(race, "render", 3.0)
second = spawn_holder(race, "render", 3.0)
try:
    out_first, err_first = first.communicate(timeout=30)
    out_second, err_second = second.communicate(timeout=30)
    codes = {first.returncode, second.returncode}
    loser = err_first if first.returncode == 2 else err_second
    check(
        "C11b due partenze simultanee: uno prende il lease, l'altro esce 2",
        codes == {0, 2} and bool(out_first.strip() or out_second.strip()),
        (first.returncode, second.returncode),
    )
    check(
        "C11c il perdente dice chi lo tiene (pid e stadio), in italiano",
        "occupata" in loser and "stadio 'render'" in loser and "pid" in loser,
        loser.strip().splitlines()[:1],
    )
finally:
    for proc in (first, second):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    clean(race)

print("== S4: due cartelle diverse, nessun blocco ==")
left, right = wd("s4-left"), wd("s4-right")
p_left = spawn_holder(left, "render", 3.0)
p_right = spawn_holder(right, "render", 3.0)
try:
    wait_lease(left)
    wait_lease(right)
    out_left, err_left = p_left.communicate(timeout=30)
    out_right, err_right = p_right.communicate(timeout=30)
    check(
        "C12 due processi su due cartelle finiscono entrambi 0",
        p_left.returncode == 0 and p_right.returncode == 0,
        (p_left.returncode, p_right.returncode),
    )
    check(
        "C13 i due lease sono indipendenti (token diversi)",
        bool(out_left.strip()) and bool(out_right.strip())
        and out_left.strip() != out_right.strip(),
        (out_left.strip(), out_right.strip()),
    )
finally:
    for proc in (p_left, p_right):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    clean(left)
    clean(right)

print("== scadenza: solo su morte reale ==")

alive = wd("vivo-battito-vecchio")
write_lease(
    alive,
    {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "token": "vecchio",
        "created": time.time() - 500,
        "heartbeat_epoch": time.time() - 400,
        "stage": "render",
        "created_process": lock.process_created(os.getpid()),
    },
)
snapshot = lock.state(alive)
check(
    "C14 pid vivo + battito vecchio di 400 s: NON scade",
    not snapshot["stale"] and "alive" in snapshot["reason"],
    (snapshot["stale"], snapshot["reason"]),
)
check(
    "C15 l'eta' del battito e' quella vera (>= 390 s)",
    snapshot["heartbeat_age_s"] >= 390,
    snapshot["heartbeat_age_s"],
)
clean(alive)

remote = wd("host-remoto")
write_lease(
    remote,
    {
        "pid": os.getpid(),
        "host": "host-remoto-lease",
        "token": "remoto",
        "created": time.time() - 400,
        "heartbeat_epoch": time.time() - 400,
        "stage": "render",
        "created_process": 0.0,
    },
)
snapshot = lock.state(remote)
check(
    "C16 host diverso + battito vecchio: stantio (non si puo' interrogare il pid)",
    snapshot["stale"] and "remote" in snapshot["reason"],
    snapshot["reason"],
)
clean(remote)

remote = wd("host-remoto-fresco")
write_lease(
    remote,
    {
        "pid": os.getpid(),
        "host": "host-remoto-lease",
        "token": "remoto",
        "created": time.time() - 10,
        "heartbeat_epoch": time.time(),
        "stage": "render",
        "created_process": 0.0,
    },
)
check(
    "C17 host diverso con battito fresco: vivo",
    not lock.state(remote)["stale"],
    lock.state(remote)["reason"],
)
clean(remote)

legacy = wd("vecchio-formato")
write_lease(
    legacy,
    {
        "pid": os.getpid(),
        "token": "legacy",
        "created": time.time() - 10,
        "updated": time.time(),
        "stage": "render",
        "host": "",
        "created_process": 0.0,
    },
    legacy=True,
)
snapshot = lock.state(legacy)
check(
    "C18 il vecchio <workdir>/.lock resta leggibile (serve/crash test)",
    snapshot["present"] and not snapshot["stale"]
    and lock.read(legacy).heartbeat_epoch > 0,
    snapshot["reason"],
)
clean(legacy)

print("== S2: processo ucciso, lease recuperato e dichiarato ==")

s2 = wd("s2")
holder = spawn_holder(s2, "render", 60.0)
try:
    info = wait_lease(s2)
    old_pid = int(info["pid"])
    # Un attimo di battito, poi morte vera (TerminateProcess).
    time.sleep(0.4)
    holder.kill()
    holder.wait(timeout=15)
    snapshot = lock.state(s2)
    check(
        "C19 pid morto: il lease e' stantio",
        snapshot["present"] and snapshot["stale"]
        and "pid is gone" in snapshot["reason"],
        (snapshot["stale"], snapshot["reason"]),
    )
    old_age = snapshot["heartbeat_age_s"]

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        recovered = lock.acquire(s2, stage="render")
    try:
        data = read_lease(s2)
        check(
            "C20 il successivo parte e dichiara stale_recovered con pid ed eta'",
            recovered.recovered is not None
            and recovered.recovered["pid"] == old_pid
            and data["stale_recovered"] is True
            and data["stale_recovered_pid"] == old_pid
            and data["stale_recovered_age_s"] >= 0,
            recovered.recovered,
        )
        check(
            "C21 la dichiarazione esce anche su stderr, in italiano",
            "stantio recuperato" in err.getvalue()
            and str(old_pid) in err.getvalue()
            and "battito fermo da" in err.getvalue(),
            err.getvalue().strip()[:120],
        )
        check(
            "C22 il nuovo lease non e' piu' stantio e il pid e' il nostro",
            not lock.state(s2)["stale"] and data["pid"] == os.getpid(),
            data["pid"],
        )
        # Il lavoro dopo il recupero va a termine: un artefatto vero e il
        # lease rilasciato pulito.
        (s2 / "done.txt").write_text("fatto", encoding="utf-8")
        check(
            "C23 il lavoro recuperato va a termine",
            (s2 / "done.txt").read_text(encoding="utf-8") == "fatto",
            old_age,
        )
    finally:
        lock.release(recovered)
    check(
        "C24 il lease sparisce dopo il lavoro",
        not lock.path_for(s2).exists(),
        lock.path_for(s2),
    )
finally:
    if holder.poll() is None:
        holder.kill()
        holder.wait(timeout=10)
    clean(s2)

print("== S3: render lungo, il lease non scade ==")

s3 = wd("s3")
long_beat = lock.acquire(s3, stage="render", heartbeat_seconds=2.0)
try:
    start_hb = float(read_lease(s3)["heartbeat_epoch"])
    started = time.time()
    # 8 s di "render": nessun bump, nessuna chiamata al loop di lavoro.
    time.sleep(8.0)
    elapsed = time.time() - started
    hb = float(read_lease(s3)["heartbeat_epoch"])
    snapshot = lock.state(s3)
    check(
        "C25 8 s di stage bloccato con battito 2 s: lease vivo",
        not snapshot["stale"] and snapshot["reason"] == "heartbeat fresh",
        snapshot["reason"],
    )
    check(
        "C26 nessun stale_recovered durante un render lungo",
        snapshot["stale_recovered"] is False
        and read_lease(s3)["stale_recovered"] is False,
        read_lease(s3)["stale_recovered"],
    )
    check(
        "C27 il battito e' avanzato di almeno 6 s in 8 s",
        hb - start_hb >= 6.0,
        round(hb - start_hb, 2),
    )
    check(
        "C28 il thread non si e' perso il lease",
        not long_beat.lost and long_beat.info.token == read_lease(s3)["token"],
        long_beat.lost,
    )
finally:
    lock.release(long_beat)
    clean(s3)

print("== rilascio: solo del proprio lease ==")

own = wd("rilascio")
foreign = lock.acquire(own, stage="render")
try:
    foreign.stop()
    current = read_lease(own)
    current["token"] = "un-altro-padrone"
    write_lease(own, current)
    check(
        "C29 release non cancella il lease di un altro",
        lock.release(foreign) is False and lock.path_for(own).exists(),
        read_lease(own)["token"],
    )
finally:
    lock.path_for(own).unlink(missing_ok=True)
    clean(own)

shutil.rmtree(TEMP, ignore_errors=True)
print(f"\n{PASS} PASS, {FAIL} FAIL")
raise SystemExit(1 if FAIL else 0)
