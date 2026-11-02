"""La UI locale: stato in JSON, fotogrammi serviti, approvazione che apre il cancello.

S1 - `/api/state` riporta fase, percentuale, fotogrammi e approvazione letti dai
     file che la skill ha gia' scritto.
S2 - i fotogrammi si servono come PNG; un nome che non esiste e' 404, non un file
     qualsiasi (nessun path traversal).
S3 - `POST /api/approve` scrive il record **nella forma di keygate**, con lo stesso
     digest del piano: `keygate.check` lo accetta.
S4 - senza un piano da approvare il POST rifiuta, invece di scrivere un record vuoto.
"""

from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import keygate, serve  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def post(url: str) -> dict:
    request = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-serve-") as raw:
        job = Path(raw) / "lavoro"
        (job / "keyframes").mkdir(parents=True)
        (job / "progress.json").write_text(
            json.dumps(
                {
                    "prompt": "reel 30 secondi",
                    "clips": 3,
                    "percent": 25.0,
                    "elapsed_sec": 12,
                    "current": "render",
                    "note": "",
                    "phases": [{"key": "triage", "label": "Guardo le clip"},
                               {"key": "render", "label": "Renderizzo"}],
                }
            ),
            encoding="utf-8",
        )
        (job / "keyframes" / "kf0.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        digest = "a" * 64
        timeline_digest = "b" * 64
        (job / "keyframes" / "pending.json").write_text(
            json.dumps(
                {
                    "digest": digest,
                    "timeline_digest": timeline_digest,
                    "count": 1,
                    "frames": [{"clip": "c0", "at": 1.0, "sha256": "c" * 64}],
                }
            ),
            encoding="utf-8",
        )

        server = serve.Server(job).start()
        try:
            page = get(server.url).decode("utf-8")
            check("S0 the page is served and speaks Italian", "Lavoro in corso" in page, len(page))

            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S1 the state carries phase, percent, frames and approval",
                data["percent"] == 25.0
                and data["current"] == "render"
                and [f["name"] for f in data["frames"]] == ["kf0.png"]
                and data["approved"] is False,
                {k: data[k] for k in ("percent", "current", "approved")},
            )

            png = get(server.url + "frames/kf0.png")
            check("S2 the keyframe is served as a PNG", png.startswith(b"\x89PNG"), len(png))
            try:
                get(server.url + "frames/..%2Fprogress.json")
                traversal = False
            except Exception:  # noqa: BLE001
                traversal = True
            check("S2 a name outside the folder is refused", traversal, "404")

            result = post(server.url + "api/approve")
            check(
                "S3 the approval is written with the plan digest",
                result.get("ok") is True and result.get("count") == 1,
                result,
            )
            record = keygate.read(job / keygate.RECORD_NAME)
            plan = keygate.KeyframePlan(
                timeline_digest=timeline_digest, digest=digest, count=1, frames=()
            )
            opened = keygate.check(plan, job / keygate.RECORD_NAME)
            check(
                "S3 and keygate accepts it: the gate opens",
                opened["approved"] is True and record["digest"] == digest,
                opened["count"],
            )
            after = json.loads(get(server.url + "api/state").decode("utf-8"))
            check("S3 the state reflects the approval", after["approved"] is True, after["approved"])

            # -- il risultato: esiste solo quando c'e' davvero --------------- #
            master = Path(raw) / "master.mp4"
            master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 3000)
            progress = json.loads((job / "progress.json").read_text(encoding="utf-8"))
            progress["master"] = str(master)
            (job / "progress.json").write_text(json.dumps(progress), encoding="utf-8")

            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S5 the state says the result is ready, with its size",
                data["master_ready"] is True and data["master_bytes"] > 0 and data["master"] == "master.mp4",
                {k: data[k] for k in ("master", "master_ready", "master_bytes")},
            )

            with urllib.request.urlopen(server.url + "result", timeout=10) as response:
                body = response.read()
                check(
                    "S5 the result is served as video, whole",
                    response.status == 200
                    and response.headers.get("Content-Type") == "video/mp4"
                    and len(body) == master.stat().st_size,
                    f"{response.status} {len(body)}",
                )
            request = urllib.request.Request(server.url + "result", headers={"Range": "bytes=100-199"})
            with urllib.request.urlopen(request, timeout=10) as response:
                chunk = response.read()
                check(
                    "S5 a Range request returns 206 and the right slice",
                    response.status == 206 and len(chunk) == 100
                    and response.headers.get("Content-Range") == f"bytes 100-199/{master.stat().st_size}",
                    f"{response.status} {len(chunk)}",
                )

            master.unlink()
            try:
                get(server.url + "result")
                missing_master = False
            except Exception:  # noqa: BLE001
                missing_master = True
            check(
                "S5 with the file gone the result is 404, not a broken player",
                missing_master,
                "404",
            )

            (job / "keyframes" / "pending.json").unlink()
            second = post(server.url + "api/approve")
            check(
                "S4 without a plan the POST refuses instead of writing nothing",
                second.get("ok") is False,
                second,
            )
        finally:
            server.stop()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
