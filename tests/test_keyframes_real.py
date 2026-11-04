"""vx.auto: i fotogrammi chiave sono PNG veri, e il cancello non si apre sul vuoto.

Il difetto vero: il temporaneo finiva in ``.png.tmp``, ffmpeg non riconosceva
l'estensione, ogni estrazione falliva in silenzio e la vista chiedeva di
approvare tre immagini che non esistevano. Qui si estrae da un video fatto con
lavfi, si controllano i byte e gli sha, e si forza il caso senza sorgente.

    python tests/test_keyframes_real.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, ffmpeg as ff, keygate, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:70} {detail}")


def get_status(url: str) -> tuple:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Ref:
    def __init__(self, path: Path) -> None:
        self.path = str(path)


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
    def __init__(self, source: Path) -> None:
        self.fps = 25.0
        self.duration_sec = 9.0
        self.aspect_ratio = "9:16"
        self.resolution = (1080, 1920)
        self.sources = {f"s{index}": Ref(source) for index in range(3)}
        self.video_tracks = (
            Track(
                [
                    Clip(0, 0.0, 3.0, 1.0, 4.0),
                    Clip(1, 3.0, 6.0, 4.0, 7.0),
                    Clip(2, 6.0, 9.0, 7.0, 10.0),
                ]
            ),
        )


def make_video(path: Path, seconds: float = 10.0) -> bool:
    proc = subprocess.run(
        [
            ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25",
            "-t", f"{seconds:.1f}", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
    )
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 0


def test_extract_and_propose() -> None:
    print("\n-- l'estrazione scrive PNG veri, con il temporaneo giusto")
    with tempfile.TemporaryDirectory(prefix="vx-kf-real-") as raw:
        root = Path(raw)
        source = root / "src.mp4"
        if not make_video(source):
            print("SKIP: ffmpeg non riesce a creare il video di prova")
            return
        single = root / "single.png"
        sha, error = auto._extract_keyframe(str(source), 2.0, single)
        check("un fotogramma estratto ha uno sha256", bool(sha) and not error, (sha[:8], error))
        check(
            "il PNG esiste e pesa piu' di zero byte",
            single.is_file() and single.stat().st_size > 0,
            single.stat().st_size if single.is_file() else "assente",
        )
        check(
            "nessun temporaneo .png.tmp lasciato dietro",
            not list(root.glob("*.png.tmp")) and not list(root.glob("*.tmp.png")),
            [item.name for item in root.iterdir()],
        )
        check(
            "lo sha e' quello del file scritto",
            sha == hashlib.sha256(single.read_bytes()).hexdigest(),
            sha[:8],
        )

        proposal = auto.propose_keyframes(root, Plan(source))
        frames = proposal["frames"]
        pngs = sorted((root / "keyframes").glob("*.png"))
        check("tre PNG scritti in keyframes/", len(pngs) == 3, [p.name for p in pngs])
        check(
            "ogni frame ha sha non vuoto e nessun errore",
            len(frames) == 3
            and all(item["sha256"] and not item["error"] for item in frames),
            [(item["sha256"][:8], item["error"]) for item in frames],
        )
        check(
            "ogni sha corrisponde al PNG dichiarato",
            all(
                item["sha256"]
                == hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
                for item in frames
            ),
            [item["path"] for item in frames],
        )
        check(
            "il digest copre i tre fotogrammi veri",
            proposal["with_images"] == 3 and bool(proposal["digest"]),
            proposal["with_images"],
        )


def test_gate_with_real_frames() -> None:
    print("\n-- il cancello si apre solo sulle immagini che ci sono")
    with tempfile.TemporaryDirectory(prefix="vx-kf-gate-") as raw:
        root = Path(raw)
        source = root / "src.mp4"
        if not make_video(source):
            print("SKIP: video di prova non creato")
            return
        (root / "progress.json").write_text(
            json.dumps({"prompt": "reel", "clips": 3, "percent": 10.0}), encoding="utf-8"
        )
        holder = {}
        worker = threading.Thread(
            target=lambda: holder.setdefault(
                "section",
                auto.keyframe_gate(
                    root,
                    plan=Plan(source),
                    wait=10.0,
                    gate=True,
                    notify=lambda _line: None,
                ),
            ),
            daemon=True,
        )
        started = time.monotonic()
        worker.start()
        waiting = root / "keyframes" / "waiting.json"
        while time.monotonic() - started < 5.0 and not waiting.is_file():
            time.sleep(0.02)
        check("il cancello scrive waiting.json", waiting.is_file())
        check(
            "lo stato della vista chiede davvero",
            (lambda approval: approval["required"] is True and approval["waiting"] is True)(
                serve.state(root)["approval"]
            ),
            serve.state(root)["approval"],
        )
        answer = serve.approve(root)
        check("l'approvazione registra i tre frame veri", answer.get("ok") is True, answer)
        worker.join(timeout=10)
        section = holder.get("section") or {}
        elapsed = time.monotonic() - started
        check("con i PNG il cancello chiede", section.get("asked") is True, section.get("asked"))
        check("count riflette i tre fotogrammi veri", section.get("count") == 3, section.get("count"))
        check(
            "il report non ha frame vuoti",
            all(item.get("sha256") for item in section.get("frames") or []),
            [item.get("sha256", "")[:8] for item in section.get("frames") or []],
        )
        check(
            "il cancello si e' sbloccato dall'approvazione",
            section.get("approved") is True and elapsed >= 0.0,
            (section.get("approved"), f"{elapsed:.3f}s"),
        )
        check(
            "pending.json porta i tre frame veri",
            json.loads(
                (root / "keyframes" / "pending.json").read_text(encoding="utf-8")
            )["count"]
            == 3,
        )


def test_no_frames_no_gate() -> None:
    print("\n-- senza nessun fotogramma estraibile il cancello non si apre")
    with tempfile.TemporaryDirectory(prefix="vx-kf-empty-") as raw:
        root = Path(raw)
        missing = root / "non_esiste.mp4"
        (root / "progress.json").write_text(
            json.dumps({"prompt": "reel", "clips": 3, "percent": 10.0}), encoding="utf-8"
        )
        started = time.monotonic()
        section = auto.keyframe_gate(
            root,
            plan=Plan(missing),
            wait=30.0,
            gate=True,
            notify=lambda _line: None,
        )
        elapsed = time.monotonic() - started
        check(
            "non ha aspettato (solo il tempo di tre ffmpeg falliti)",
            float(section.get("waited_sec") or 0.0) < 0.1 and elapsed < 10.0,
            (section.get("waited_sec"), f"{elapsed:.3f}s"),
        )
        check("asked false", section["asked"] is False, section["asked"])
        check("required false", section["required"] is False, section["required"])
        check("count zero", section.get("count") == 0, section.get("count"))
        check(
            "la ragione e' dichiarata",
            section.get("reason") == "nessun fotogramma estraibile",
            section.get("reason"),
        )
        # La vista legge il report dell'ultimo giro: e' li' che il perche' resta.
        (root / "report.json").write_text(
            json.dumps({"keyframes_gate": section}), encoding="utf-8"
        )
        check(
            "gli errori restano nel report, uno per frame",
            len(section.get("frames") or []) == 3
            and all(item.get("error") for item in section["frames"]),
            [(item.get("sha256"), item.get("error", "")[:40]) for item in section["frames"]],
        )
        check(
            "il pending resta a count 0, senza digest e senza attesa",
            (lambda payload: payload.get("count") == 0 and payload.get("digest") == "")
            (json.loads((root / "keyframes" / "pending.json").read_text(encoding="utf-8")))
            and not (root / "keyframes" / "waiting.json").exists(),
            sorted(p.name for p in (root / "keyframes").glob("*")),
        )
        check(
            "la vista non accende il pulsante e dice perche'",
            (lambda approval: approval["required"] is False
             and "estraibile" in approval["reason"])(serve.state(root)["approval"]),
            serve.state(root)["approval"],
        )


def test_approve_without_frames() -> None:
    print("\n-- il digest non si calcola su frame vuoti")
    with tempfile.TemporaryDirectory(prefix="vx-kf-approve-") as raw:
        root = Path(raw)
        (root / "progress.json").write_text(
            json.dumps({"prompt": "reel", "clips": 1, "percent": 10.0}), encoding="utf-8"
        )
        (root / "keyframes").mkdir()
        (root / "keyframes" / "pending.json").write_text(
            json.dumps(
                {
                    "schema": auto.KEYFRAMES_PLAN_SCHEMA,
                    "digest": "",
                    "count": 0,
                    "frames": [
                        {"clip": "c0", "index": 0, "at": 1.0, "sha256": "",
                         "error": "estrazione fallita"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        answer = serve.approve(root)
        check("approve rifiuta, non registra il nulla", answer.get("ok") is False, answer)
        check("approved false nel rifiuto", answer.get("approved") is False, answer)
        check(
            "nessun keyframes.json scritto",
            not (root / keygate.RECORD_NAME).exists(),
            sorted(p.name for p in root.iterdir()),
        )


def test_http_serves_only_real_files() -> None:
    print("\n-- /shots e /frames non servono un file che non c'e'")
    with tempfile.TemporaryDirectory(prefix="vx-kf-http-") as raw:
        job = Path(raw) / "lavoro"
        job.mkdir()
        (job / "progress.json").write_text(
            json.dumps({"prompt": "reel", "clips": 1, "percent": 10.0}), encoding="utf-8"
        )
        (job / "plan.json").write_text(
            json.dumps({"cuts": [{"in": 0.0, "out": 2.0}]}), encoding="utf-8"
        )
        (job / "keyframes").mkdir()
        (job / "keyframes" / "kf0.png").write_bytes(b"")
        server = serve.Server(job).start()
        try:
            status, body = get_status(server.url + "shots/0")
            check(
                "/shots/0 senza file: 404, non un PNG vuoto",
                status == 404,
                (status, body[:50]),
            )
            status, body = get_status(server.url + "frames/kf0.png")
            check(
                "/frames/kf0.png da zero byte: 404",
                status == 404,
                (status, body[:50]),
            )
            state = json.loads(get_status(server.url + "api/state")[1].decode("utf-8"))
            check(
                "la griglia non elenca il PNG vuoto",
                state.get("frames") == [],
                state.get("frames"),
            )
        finally:
            server.stop()


def main() -> int:
    test_extract_and_propose()
    test_gate_with_real_frames()
    test_no_frames_no_gate()
    test_approve_without_frames()
    test_http_serves_only_real_files()
    print(f"\n{PASS} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
