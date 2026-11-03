"""vx.auto: il cancello dei fotogrammi chiave aspetta, ma non all'infinito.

Nessuna rete e nessun render: il piano dei fotogrammi arriva da un ``propose``
finto (o da un piano EDL senza sorgenti), cosi' si prova solo il cancello.

    python tests/test_key_gate.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import auto, cli, keygate, serve  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

PROPOSAL = {
    "schema": auto.KEYFRAMES_PLAN_SCHEMA,
    "timeline_digest": "b" * 64,
    "digest": "a" * 64,
    "count": 3,
    "frames": [
        {"clip": f"c{index}", "index": index, "at": 1.0 + index, "sha256": "c" * 64}
        for index in range(3)
    ],
}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:70} {detail}")


def skip(label: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"{'SKIP':4} {label:70} {detail}")


def fake_propose(workdir, plan):
    """Un piano valido senza estrarre un solo PNG."""
    return dict(PROPOSAL)


def broken_propose(workdir, plan):
    raise RuntimeError("piano non disponibile")


def write_record(workdir: Path, digest: str, approved: bool = True) -> None:
    (Path(workdir) / keygate.RECORD_NAME).write_text(
        json.dumps(
            {
                "version": keygate.GATE_VERSION,
                "approved": approved,
                "digest": digest,
                "timeline_digest": PROPOSAL["timeline_digest"],
                "count": 3,
                "frames": PROPOSAL["frames"],
            }
        ),
        encoding="utf-8",
    )


def waiting_path(workdir: Path) -> Path:
    return Path(workdir) / auto.KEYFRAMES_DIR / auto.KEYFRAMES_WAITING_NAME


def pending_path(workdir: Path) -> Path:
    return Path(workdir) / auto.KEYFRAMES_DIR / auto.KEYFRAMES_PENDING_NAME


def wait_for(path: Path, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.is_file():
            return True
        time.sleep(0.02)
    return False


class FakeClock:
    """Un orologio che avanza solo quando qualcuno dorme: niente attese vere."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class Clip:
    def __init__(self, index: int, dst_in: float, dst_out: float, src_in: float, src_out: float):
        self.clip_id = f"v0_{index}_0"
        self.source_id = f"s{index}"
        self.src_in = src_in
        self.src_out = src_out
        self.dst_in = dst_in
        self.dst_out = dst_out
        self.layer = 0


class Track:
    def __init__(self, clips):
        self.clips = list(clips)
        self.track_id = 0
        self.role = "a_roll"


class Plan:
    """Un piano EDL minimo: serve solo a proporre tre istanti."""

    def __init__(self) -> None:
        self.fps = 30.0
        self.duration_sec = 9.0
        self.aspect_ratio = "9:16"
        self.resolution = (1080, 1920)
        self.sources = {}
        self.video_tracks = (
            Track(
                [
                    Clip(0, 0.0, 3.0, 10.0, 13.0),
                    Clip(1, 3.0, 6.0, 20.0, 23.0),
                    Clip(2, 6.0, 9.0, 30.0, 33.0),
                ]
            ),
        )


def test_no_plan() -> None:
    print("\n-- senza piano il cancello non si apre e non aspetta")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        started = time.monotonic()
        section = auto.keyframe_gate(workdir, wait=30.0, gate=True, notify=lambda _: None)
        elapsed = time.monotonic() - started
        check("nessuna attesa", elapsed < 0.5, f"{elapsed:.3f}s")
        check("required false", section["required"] is False, section["required"])
        check("waiting false", section["waiting"] is False, section["waiting"])
        check(
            "la ragione e' in italiano",
            "nessun piano di fotogrammi" in section["reason"],
            section["reason"],
        )
        check("nessun waiting.json scritto", not waiting_path(workdir).exists())
        check("nessun pending.json scritto", not pending_path(workdir).exists())
        broken = auto.keyframe_gate(
            workdir, plan=Plan(), propose=broken_propose, wait=30.0, gate=True
        )
        check(
            "propose che solleva non aspetta e non finge un piano",
            broken["required"] is False and "non disponibile" in broken["reason"],
            broken["reason"],
        )


def test_closed_gate() -> None:
    print("\n-- --no-key-gate: nessuna attesa e nessun waiting.json")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        started = time.monotonic()
        section = auto.keyframe_gate(
            workdir,
            plan=Plan(),
            propose=fake_propose,
            wait=30.0,
            gate=False,
            reason_when_closed="cancello dei fotogrammi spento (--no-key-gate)",
            notify=lambda _: None,
        )
        elapsed = time.monotonic() - started
        check("nessuna attesa", elapsed < 0.5, f"{elapsed:.3f}s")
        check("selected_by == auto", section["selected_by"] == "auto", section["selected_by"])
        check("waiting false", section["waiting"] is False, section["waiting"])
        check("required false", section["required"] is False, section["required"])
        check("nessun waiting.json", not waiting_path(workdir).exists())
        check(
            "il piano resta scritto come pending.json",
            pending_path(workdir).is_file()
            and json.loads(pending_path(workdir).read_text(encoding="utf-8"))["digest"]
            == PROPOSAL["digest"],
        )
        # Un segnale d'attesa di un giro precedente non deve sopravvivere quando
        # nessuno aspetta: lo stato direbbe il falso.
        waiting_path(workdir).write_text(
            json.dumps({"schema": "vx.keygate-wait/1", "since_utc": "x", "frames": []}),
            encoding="utf-8",
        )
        stale = auto.keyframe_gate(
            workdir, plan=Plan(), propose=fake_propose, wait=30.0, gate=False
        )
        check(
            "un waiting.json vecchio non resta quando il cancello e' chiuso",
            stale["waiting"] is False and not waiting_path(workdir).exists(),
            stale["selected_by"],
        )
        check(
            "la ragione nomina il flag",
            "no-key-gate" in section["reason"],
            section["reason"],
        )


def test_wait_then_approve() -> None:
    print("\n-- in attesa: waiting.json c'e', il POST approva e sblocca")
    with tempfile.TemporaryDirectory() as tmp:
        job = Path(tmp) / "lavoro"
        job.mkdir()
        (job / "progress.json").write_text(
            json.dumps({"prompt": "reel", "clips": 3, "percent": 10.0}), encoding="utf-8"
        )
        holder = {}
        seen = []

        def run_gate() -> None:
            holder["section"] = auto.keyframe_gate(
                job,
                plan=Plan(),
                propose=fake_propose,
                wait=10.0,
                gate=True,
                view_url="http://127.0.0.1:9999/",
                notify=seen.append,
            )

        worker = threading.Thread(target=run_gate, daemon=True)
        started = time.monotonic()
        worker.start()
        appeared = wait_for(waiting_path(job))
        waited = time.monotonic() - started
        check("waiting.json appare mentre il render aspetta", appeared, f"{waited:.3f}s")
        payload = (
            json.loads(waiting_path(job).read_text(encoding="utf-8")) if appeared else {}
        )
        check(
            "waiting.json ha schema, since_utc e i fotogrammi",
            payload.get("schema") == "vx.keygate-wait/1"
            and bool(str(payload.get("since_utc") or ""))
            and len(payload.get("frames") or []) == 3,
            {k: payload.get(k) for k in ("schema", "since_utc")},
        )
        state = serve.state(job)
        check(
            "lo stato dice waiting e required, non ancora approved",
            state["approval"]["waiting"] is True
            and state["approval"]["required"] is True
            and state["approval"]["approved"] is False,
            state["approval"],
        )
        answer = serve.approve(job)
        check(
            "il POST scrive keyframes.json col digest del piano",
            answer.get("ok") is True and answer.get("count") == 3,
            answer,
        )
        record = keygate.read(job / keygate.RECORD_NAME)
        check(
            "il record e' nella forma di keygate",
            bool(record) and record["approved"] is True and record["digest"] == PROPOSAL["digest"],
            record and record.get("digest", "")[:8],
        )
        worker.join(timeout=10)
        check("il cancello si e' sbloccato da solo", not worker.is_alive(), worker.is_alive())
        section = holder.get("section") or {}
        check("approved true", section.get("approved") is True, section.get("approved"))
        check("selected_by == user", section.get("selected_by") == "user", section.get("selected_by"))
        check("waiting false dopo l'approvazione", section.get("waiting") is False, section.get("waiting"))
        check("waiting.json rimosso", not waiting_path(job).exists())
        check(
            "lo stato finale dice approved e non waiting",
            (lambda s: s["approval"]["approved"] is True and s["approval"]["waiting"] is False)(
                serve.state(job)
            ),
        )
        check(
            "l'avviso dice dove approvare",
            any("http://127.0.0.1:9999/" in line for line in seen),
            seen[:2],
        )


def test_timeout() -> None:
    print("\n-- allo scadere il job prosegue e il report dichiara timeout")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        started = time.monotonic()
        section = auto.keyframe_gate(
            workdir,
            plan=Plan(),
            propose=fake_propose,
            wait=1.0,
            gate=True,
            notify=lambda _: None,
        )
        elapsed = time.monotonic() - started
        check("ha aspettato circa un secondo", elapsed >= 0.9, f"{elapsed:.3f}s")
        check("approved false", section["approved"] is False, section["approved"])
        check("selected_by == timeout", section["selected_by"] == "timeout", section["selected_by"])
        check("required true: un piano c'era", section["required"] is True, section["required"])
        check("waiting false allo scadere", section["waiting"] is False, section["waiting"])
        check("waiting.json rimosso allo scadere", not waiting_path(workdir).exists())
        check("waited_sec dichiarato", float(section["waited_sec"]) >= 0.9, section["waited_sec"])
        fake = FakeClock()
        section = auto.keyframe_gate(
            workdir,
            plan=Plan(),
            propose=fake_propose,
            wait=1.0,
            gate=True,
            clock=fake.clock,
            sleep=fake.sleep,
            notify=lambda _: None,
        )
        check(
            "con orologio finto il timeout resta dichiarato",
            section["selected_by"] == "timeout" and section["waited_sec"] <= 1.0,
            section["waited_sec"],
        )


def test_already_approved() -> None:
    print("\n-- un'approvazione gia' coerente non fa aspettare")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        write_record(workdir, PROPOSAL["digest"])
        started = time.monotonic()
        section = auto.keyframe_gate(
            workdir, plan=Plan(), propose=fake_propose, wait=30.0, gate=True
        )
        elapsed = time.monotonic() - started
        check("nessuna attesa", elapsed < 0.5, f"{elapsed:.3f}s")
        check("approved true", section["approved"] is True, section["approved"])
        check("selected_by == user", section["selected_by"] == "user", section["selected_by"])
        check("required true", section["required"] is True, section["required"])
        check("nessun waiting.json", not waiting_path(workdir).exists())

    print("\n-- un'approvazione di un altro montaggio non apre il cancello")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        write_record(workdir, "d" * 64)
        section = auto.keyframe_gate(
            workdir,
            plan=Plan(),
            propose=fake_propose,
            wait=0.2,
            gate=True,
            notify=lambda _: None,
        )
        check("approved false", section["approved"] is False, section["approved"])
        check("prova ad aspettare davvero", section["selected_by"] == "timeout", section["selected_by"])


def test_default_proposal_without_media() -> None:
    print("\n-- il piano di default propone tre istanti anche senza sorgenti")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        proposal = auto.propose_keyframes(workdir, Plan())
        check("tre fotogrammi", proposal.get("count") == 3, proposal.get("count"))
        check(
            "gli istanti stanno dentro i clip giusti",
            [item["clip"] for item in proposal["frames"]] == ["v0_0_0", "v0_1_0", "v0_2_0"],
            [item["clip"] for item in proposal["frames"]],
        )
        same = auto.propose_keyframes(workdir, Plan())
        check("il digest e' stabile a parita' di timeline", same["digest"] == proposal["digest"])
        changed = Plan()
        changed.duration_sec = 8.0
        check(
            "cambiando la timeline cambia il digest",
            auto.propose_keyframes(workdir, changed)["digest"] != proposal["digest"],
        )
        check("senza clip non c'e' piano", auto.propose_keyframes(workdir, None) == {})


def test_flags() -> None:
    print("\n-- flag di vx auto")
    parser = cli.build_parser()
    base = ["auto", "--prompt", "p", "--assets", ".", "--out", "o.mp4"]
    plain = parser.parse_args(base)
    check("default: deciso dalla vista (None)", plain.key_gate is None, plain.key_gate)
    check("default: attesa decisa dal settings (None)", plain.key_wait is None, plain.key_wait)
    waited = parser.parse_args(base + ["--key-wait", "1"])
    check("--key-wait 1 vale 1.0 s", waited.key_wait == 1.0, waited.key_wait)
    opened = parser.parse_args(base + ["--key-gate"])
    check("--key-gate apre il cancello", opened.key_gate is True)
    closed = parser.parse_args(base + ["--no-key-gate"])
    check("--no-key-gate lo chiude", closed.key_gate is False)

    print("\n-- _run_key_gate legge i flag prima di decidere")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        args = argparse.Namespace(key_gate=False, key_wait=30.0)
        section = auto._run_key_gate(args, workdir, Plan(), view_active=True)
        check(
            "il flag vince sulla vista: nessuna attesa",
            section["selected_by"] == "auto" and not waiting_path(workdir).exists(),
            section["selected_by"],
        )
        check(
            "il flag chiuso e' dichiarato",
            "no-key-gate" in section["reason"],
            section["reason"],
        )
        args = argparse.Namespace(key_gate=None, key_wait=0.2)
        started = time.monotonic()
        section = auto._run_key_gate(args, workdir, Plan(), view_active=True)
        elapsed = time.monotonic() - started
        check(
            "senza flag e con la vista il cancello aspetta",
            section["selected_by"] == "timeout" and elapsed >= 0.15,
            f"{section['selected_by']} {elapsed:.3f}s",
        )
        args = argparse.Namespace(key_gate=None, key_wait=30.0)
        started = time.monotonic()
        section = auto._run_key_gate(args, workdir, Plan(), view_active=False)
        elapsed = time.monotonic() - started
        check(
            "senza vista non si aspetta nessuno",
            section["selected_by"] == "auto" and elapsed < 0.5,
            f"{section['selected_by']} {elapsed:.3f}s",
        )


def main() -> int:
    test_no_plan()
    test_closed_gate()
    test_wait_then_approve()
    test_timeout()
    test_already_approved()
    test_default_proposal_without_media()
    test_flags()
    print(f"\n{PASS} PASS, {FAIL} FAIL, {SKIP} SKIP")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
