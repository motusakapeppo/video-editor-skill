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
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import keygate, serve, subs  # noqa: E402

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


def style_line(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style:"):
            return line
    return ""


def check_font_gate(raw: str) -> None:
    """La proposta accende il cancello, il campione si serve, la scelta si scrive."""
    job = Path(raw) / "lavoro"
    (job / "fonts").mkdir(parents=True)
    (job / "progress.json").write_text(
        json.dumps({"prompt": "reel font", "percent": 10.0, "current": "font"}),
        encoding="utf-8",
    )
    sample = job / "fonts" / "playfairdisplay-sample.png"
    sample.write_bytes(b"\x89PNG\r\n\x1a\n" + b"s" * 32)
    proposal = {
        "schema": "vx.fontproposal/1",
        "requirement": "elegante, editoriale",
        "sample": "PROVA",
        "network": False,
        "pool": "local",
        "chosen": "Playfair Display",
        "reason_if_none": "",
        "candidates": [
            {
                "family": "Playfair Display",
                "licence": "commercial",
                "licence_url": "",
                "score": 0.91,
                "because": "serif editoriale",
                "file": str(Path(raw) / "manca.ttf"),
                "sample_png": str(sample),
                "source": "bundle",
                "downloaded": False,
            },
            {
                "family": "Inter",
                "licence": "commercial",
                "licence_url": "",
                "score": 0.42,
                "because": "neutro",
                "file": str(Path(raw) / "manca2.ttf"),
                # Il PNG non c'e': il candidato resta, il campione e' vuoto.
                "sample_png": str(job / "fonts" / "inter-sample.png"),
                "source": "bundle",
                "downloaded": False,
            },
        ],
    }
    (job / "font_proposal.json").write_text(json.dumps(proposal), encoding="utf-8")

    server = serve.Server(job).start()
    try:
        data = json.loads(get(server.url + "api/state").decode("utf-8"))
        font = data["font"]
        check(
            "S9 la proposta accende il cancello con i suoi candidati",
            font["pending"] is True
            and font["chosen"] == ""
            and len(font["candidates"]) == 2
            and font["candidates"][0]["family"] == "Playfair Display",
            font,
        )
        check(
            "S9 il campione vale un URL solo se il PNG esiste",
            font["candidates"][0]["sample"] == "/fonts/sample/playfairdisplay-sample.png"
            and font["candidates"][1]["sample"] == "",
            [candidate["sample"] for candidate in font["candidates"]],
        )

        with urllib.request.urlopen(
            server.url + "fonts/sample/playfairdisplay-sample.png?job=0", timeout=10
        ) as response:
            png = response.read()
            check(
                "S9 il campione si serve come PNG",
                response.headers.get("Content-Type") == "image/png"
                and png.startswith(b"\x89PNG"),
                f"{response.status} {len(png)}",
            )
        try:
            get(server.url + "fonts/sample/..%2F..%2Fetc%2Fpasswd.png?job=0")
            escaped = False
        except Exception:  # noqa: BLE001
            escaped = True
        check("S9 il traversal non esce dalla cartella fonts", escaped, "404")

        refused = post(
            server.url + "api/font-pick?job=0&family=" + urllib.parse.quote("Comic Sans")
        )
        check(
            "S9 una famiglia inventata non scrive niente",
            refused.get("ok") is False
            and refused.get("reason") == "famiglia non proposta"
            and not (job / "font_choice.json").is_file(),
            refused,
        )

        picked = post(
            server.url
            + "api/font-pick?job=0&family="
            + urllib.parse.quote("Playfair Display")
        )
        choice = json.loads((job / "font_choice.json").read_text(encoding="utf-8"))
        check(
            "S9 la scelta e' scritta nella forma promessa",
            picked.get("ok") is True
            and picked.get("chosen") == "Playfair Display"
            and choice.get("schema") == "vx.fontchoice/1"
            and choice.get("family") == "Playfair Display"
            and choice.get("from") == "ui"
            and bool(choice.get("chosen_utc")),
            choice,
        )
        after = json.loads(get(server.url + "api/state").decode("utf-8"))
        check(
            "S9 scelto: il cancello si chiude e chosen si legge",
            after["font"]["pending"] is False
            and after["font"]["chosen"] == "Playfair Display",
            after["font"],
        )
    finally:
        server.stop()


def check_ass_bridge() -> None:
    """La scelta arriva al pixel: il Fontname dell'ASS e' la famiglia scelta."""
    from vx import cli
    from vx.contract import Cue

    with tempfile.TemporaryDirectory(prefix="vx-fontass-") as raw:
        work = Path(raw) / "lavoro"
        work.mkdir()
        bundled = ROOT / "assets" / "fonts" / "PlayfairDisplay.ttf"
        (work / "font_proposal.json").write_text(
            json.dumps(
                {
                    "schema": "vx.fontproposal/1",
                    "requirement": "elegante",
                    "sample": "PROVA",
                    "network": False,
                    "pool": "local",
                    "chosen": "Playfair Display",
                    "reason_if_none": "",
                    "candidates": [
                        {
                            "family": "Playfair Display",
                            "licence": "commercial",
                            "licence_url": "",
                            "score": 0.9,
                            "because": "serif",
                            "file": str(bundled),
                            "sample_png": "",
                            "source": "bundle",
                            "downloaded": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        cues = [
            Cue(
                start=0.0,
                end=1.5,
                text="CIAO MONDO",
                words=[{"start": 0.0, "end": 1.5, "word": "CIAO"}],
                style="karaoke",
                line_count=1,
            )
        ]
        bare = Path(raw) / "vuoto"
        bare.mkdir()
        preset = Path(
            cli._write_job_ass(
                cues,
                bare,
                bare / "preset.ass",
                aspect="9:16",
                platform="tiktok",
                style="karaoke",
                out_w=1080,
                out_h=1920,
            )
        )
        check(
            "S10 senza proposta ne' scelta resta il preset (Inter)",
            "Vx,Inter," in preset.read_text(encoding="utf-8"),
            style_line(preset),
        )

        (work / "font_choice.json").write_text(
            json.dumps(
                {
                    "schema": "vx.fontchoice/1",
                    "family": "Playfair Display",
                    "chosen_utc": "2026-01-01T00:00:00+00:00",
                    "from": "ui",
                }
            ),
            encoding="utf-8",
        )
        chosen = Path(
            cli._write_job_ass(
                cues,
                work,
                work / "scelto.ass",
                aspect="9:16",
                platform="tiktok",
                style="karaoke",
                out_w=1080,
                out_h=1920,
            )
        )
        text = chosen.read_text(encoding="utf-8")
        check(
            "S10 il Fontname dell'ASS e' la famiglia scelta",
            "Vx,Playfair Display," in text,
            style_line(chosen),
        )
        check(
            "S10 il TTF scelto e' copiato in <workdir>/fonts",
            (work / "fonts" / "PlayfairDisplay.ttf").is_file(),
            sorted(path.name for path in (work / "fonts").glob("*.ttf")),
        )
        check(
            "S10 il filtro di burn punta alla cartella font del lavoro",
            "fonts" in subs.burn_filter(chosen, fontsdir=work / "fonts"),
            subs.burn_filter(chosen, fontsdir=work / "fonts"),
        )


def check_derived_jobs() -> None:
    """Un lavoro senza ``progress.json`` compare, ma lo dice: stato dedotto.

    Il riconoscimento e' stretto: i segni veri della skill bastano, un video
    sciolto no. Lo stato dedotto non finge percentuali ne' master.
    """
    with tempfile.TemporaryDirectory(prefix="vx-derived-") as raw:
        base = Path(raw)

        derived = base / "mixed_reel"
        (derived / "stage_00").mkdir(parents=True)
        (derived / "stage_00" / "stage_00_ingest_IMG_9929.mp4").write_bytes(
            b"\x00\x00\x00\x18ftypmp42" + b"i" * 64
        )
        (derived / "proxy").mkdir()
        (derived / "audio").mkdir()
        (derived / "out").mkdir()
        # La workdir di un lavoro non e' un secondo lavoro.
        (derived / "work").mkdir()
        (derived / "work" / "plan.json").write_text(
            json.dumps({"timeline": {"duration_sec": 4.0}}), encoding="utf-8"
        )
        master = derived / "out" / "master.mp4"
        master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"d" * 512)

        bare = base / "video_sciolto"
        bare.mkdir()
        (bare / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 32)

        empty_out = base / "senza_uscita"
        (empty_out / "stage_00").mkdir(parents=True)

        published = base / "pubblicato"
        published.mkdir()
        (published / "progress.json").write_text(
            json.dumps(
                {
                    "prompt": "reel pubblicato",
                    "percent": 42.0,
                    "current": "render",
                    "phases": [{"key": "render", "label": "Renderizzo"}],
                }
            ),
            encoding="utf-8",
        )

        names = [item["job"] for item in serve.state(base)["jobs"]]
        check(
            "S11 i segni veri bastano: mixed_reel compare anche senza progress.json",
            "mixed_reel" in names and "pubblicato" in names,
            names,
        )
        check(
            "S11 una cartella con solo un video non e' un lavoro",
            "video_sciolto" not in names,
            names,
        )
        check(
            "S11 la workdir interna (plan.json) non e' un secondo lavoro",
            names.count("work") == 0,
            names,
        )

        index = names.index("mixed_reel")
        job = serve.state(base, index)
        check(
            "S11 lo stato dedotto dichiara la sua fonte",
            job["progress_source"] == "derived",
            job["progress_source"],
        )
        check(
            "S11 lo stato dedotto e' parziale, non finto",
            job["percent"] == 0.0
            and job["current"] is None
            and "senza progresso" in job["note"]
            and job["timeline"] == []
            and job["render"] == {
                "out_time_sec": 0.0, "duration_sec": 0.0, "percent": 0.0, "speed": ""
            }
            and job["font"]["pending"] is False
            and job["font"]["candidates"] == [],
            {k: job[k] for k in ("percent", "current", "note")},
        )
        check(
            "S11 le fasi sono quelle che i file presenti raccontano",
            [phase["key"] for phase in job["phases"]] == ["stage_00", "proxy", "audio"],
            job["phases"],
        )
        check(
            "S11 il master vale solo perche' il file esiste",
            job["master_ready"] is True
            and job["master"] == "master.mp4"
            and job["master_bytes"] == master.stat().st_size,
            {k: job[k] for k in ("master", "master_ready", "master_bytes")},
        )

        server = serve.Server(base).start()
        try:
            with urllib.request.urlopen(
                server.url + f"result?job={index}", timeout=10
            ) as response:
                body = response.read()
                check(
                    "S11 /result serve il master dedotto",
                    response.headers.get("Content-Type") == "video/mp4"
                    and body == master.read_bytes(),
                    f"{response.status} {len(body)}",
                )

            opened = []
            original = serve.REVEAL
            serve.REVEAL = lambda where: opened.append(Path(where))
            try:
                answer = post(server.url + f"api/reveal?job={index}")
            finally:
                serve.REVEAL = original
            check(
                "S11 /api/reveal apre il master dedotto che esiste",
                answer.get("ok") is True and opened == [master],
                answer,
            )

            missing = names.index("senza_uscita")
            missing_state = serve.state(base, missing)
            check(
                "S11 senza file d'uscita il master resta vuoto",
                missing_state["progress_source"] == "derived"
                and missing_state["master_ready"] is False
                and missing_state["master"] == "",
                {k: missing_state[k] for k in ("master", "master_ready")},
            )
            try:
                get(server.url + f"result?job={missing}")
                no_master = False
            except Exception:  # noqa: BLE001
                no_master = True
            check("S11 senza master /result resta 404", no_master, "404")

            master.unlink()
            try:
                get(server.url + f"result?job={index}")
                gone = False
            except Exception:  # noqa: BLE001
                gone = True
            check("S11 tolto il file, /result torna 404", gone, "404")

            # L'ordine puo' cambiare (l'unlink ha toccato ``out/``): si rilegge.
            fresh = [item["job"] for item in serve.state(base)["jobs"]]
            normal = serve.state(base, fresh.index("pubblicato"))
            check(
                "S11 il lavoro con progress.json resta pubblicato",
                normal["progress_source"] == "json"
                and normal["percent"] == 42.0
                and normal["current"] == "render"
                and normal["phases"] == [{"key": "render", "label": "Renderizzo"}],
                {k: normal[k] for k in ("progress_source", "percent", "current")},
            )
        finally:
            server.stop()


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

            # -- regia: il progresso di ffmpeg e gli shot del piano ----------- #
            zero = {"out_time_sec": 0.0, "duration_sec": 0.0, "percent": 0.0, "speed": ""}
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S7 without the ffmpeg progress file the render stays at zero",
                data["render"] == zero and data["timeline"] == [],
                {"render": data["render"], "timeline": data["timeline"]},
            )

            (job / "plan.json").write_text(
                json.dumps(
                    {
                        "contract_version": "2.2.0",
                        "timeline": {
                            "fps": 30.0,
                            "duration_sec": 4.0,
                            "aspect_ratio": "9:16",
                            "resolution": [1080, 1920],
                        },
                        "sources": {},
                        "tracks": {
                            "video": [
                                {
                                    "track_id": 0,
                                    "role": "a_roll",
                                    # Fuori ordine apposta: la UI ordina per destinazione.
                                    "clips": [
                                        {"clip_id": "v0_2_0", "source_id": "sano.mp4",
                                         "src_in": 10.0, "src_out": 11.0,
                                         "dst_in": 3.0, "dst_out": 4.0,
                                         "layer": 0, "role": "main_talking_head"},
                                        {"clip_id": "v0_0_0", "source_id": "sano.mp4",
                                         "src_in": 0.0, "src_out": 1.0,
                                         "dst_in": 0.0, "dst_out": 1.0,
                                         "layer": 0, "role": "main_talking_head"},
                                        {"clip_id": "v0_1_0", "source_id": "sano.mp4",
                                         "src_in": 5.0, "src_out": 7.0,
                                         "dst_in": 1.0, "dst_out": 3.0,
                                         "layer": 0, "role": "main_talking_head"},
                                    ],
                                }
                            ],
                            "audio": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (job / "render").mkdir()
            (job / "render" / "ffmpeg-progress.txt").write_text(
                "frame=90\nfps=30.0\nout_time_us=1500000\nout_time_ms=1500000\n"
                "out_time=00:00:01.500000\nspeed=1.20x\nprogress=continue\n",
                encoding="utf-8",
            )
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            render = data["render"]
            check(
                "S7 the render block reads ffmpeg and the plan duration",
                render["out_time_sec"] == 1.5
                and render["duration_sec"] == 4.0
                and render["percent"] == 37.5
                and render["speed"] == "1.20x",
                render,
            )
            shots = data["timeline"]
            check(
                "S8 the timeline holds every shot, ordered by destination time",
                [shot["index"] for shot in shots] == [0, 1, 2]
                and [shot["t_in"] for shot in shots] == [0.0, 1.0, 3.0]
                and [shot["t_out"] for shot in shots] == [1.0, 3.0, 4.0]
                and [shot["dur"] for shot in shots] == [1.0, 2.0, 1.0],
                shots,
            )

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

            # -- aprire la cartella: il percorso non arriva mai dal client ---- #
            opened = []
            original = serve.REVEAL
            serve.REVEAL = lambda where: opened.append(Path(where))
            try:
                answer = post(server.url + "api/reveal")
            finally:
                serve.REVEAL = original
            check(
                "S5 the folder of the master is opened, and it is the recorded one",
                answer.get("ok") is True
                and opened == [master]
                and answer.get("folder") == str(master.parent),
                {"ok": answer.get("ok"), "opened": [str(where) for where in opened]},
            )

            master.unlink()
            answer = post(server.url + "api/reveal")
            check(
                "S5 without the file no folder is opened",
                answer.get("ok") is False and "master" in str(answer.get("reason", "")),
                answer,
            )
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

    # -- piu' lavori in parallelo ------------------------------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-multi-") as raw2:
        base = Path(raw2)
        for name, prompt, pct in (("alfa", "reel alfa", 25.0), ("beta", "reel beta", 80.0)):
            job_dir = base / name
            (job_dir / "keyframes").mkdir(parents=True)
            master = job_dir / "master.mp4"
            master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"m" * 1000)
            (job_dir / "progress.json").write_text(
                json.dumps(
                    {
                        "prompt": prompt,
                        "clips": 1,
                        "percent": pct,
                        "elapsed_sec": 5,
                        "current": "render",
                        "note": "",
                        "phases": [{"key": "render", "label": "Renderizzo"}],
                        "master": str(master),
                    }
                ),
                encoding="utf-8",
            )
            if name == "beta":
                # Un solo lavoro ha piano e progresso di ffmpeg: ?job deve
                # riportare quello giusto, e l'altro restare a zero.
                (job_dir / "plan.json").write_text(
                    json.dumps(
                        {
                            "timeline": {"fps": 30.0, "duration_sec": 2.0},
                            "tracks": {
                                "video": [
                                    {
                                        "track_id": 0,
                                        "role": "a_roll",
                                        "clips": [
                                            {"clip_id": "v0_0_0", "source_id": "beta.mp4",
                                             "src_in": 0.0, "src_out": 2.0,
                                             "dst_in": 0.0, "dst_out": 2.0,
                                             "layer": 0, "role": "main_talking_head"},
                                        ],
                                    }
                                ]
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                (job_dir / "render").mkdir()
                (job_dir / "render" / "ffmpeg-progress.txt").write_text(
                    "out_time_ms=500000\nspeed=1.0x\nprogress=continue\n",
                    encoding="utf-8",
                )

        multi = serve.Server(base).start()
        try:
            data = json.loads(get(multi.url + "api/state").decode("utf-8"))
            check(
                "S6 two jobs are discovered, both visible",
                len(data["jobs"]) == 2 and {j["prompt"] for j in data["jobs"]} == {"reel alfa", "reel beta"},
                [j["prompt"] for j in data["jobs"]],
            )
            first = json.loads(get(multi.url + "api/state?job=0").decode("utf-8"))
            second = json.loads(get(multi.url + "api/state?job=1").decode("utf-8"))
            check(
                "S6 ?job picks the right one",
                first["prompt"] != second["prompt"] and second["selected"] == 1,
                (first["prompt"], second["prompt"]),
            )
            sizes = {len(get(multi.url + f"result?job={i}")) for i in (0, 1)}
            expected = (base / "beta" / "master.mp4").stat().st_size
            check(
                "S6 each job serves its own result, whole",
                sizes == {expected},
                sizes,
            )
            by_prompt = {item["prompt"]: item for item in (first, second)}
            check(
                "S7 ?job carries each job's own render block and timeline",
                by_prompt["reel beta"]["render"]["out_time_sec"] == 0.5
                and by_prompt["reel beta"]["render"]["percent"] == 25.0
                and len(by_prompt["reel beta"]["timeline"]) == 1
                and by_prompt["reel alfa"]["render"]["out_time_sec"] == 0.0
                and by_prompt["reel alfa"]["timeline"] == [],
                {name: (item["render"], len(item["timeline"])) for name, item in by_prompt.items()},
            )
            broken = json.loads(get(multi.url + "api/state?job=99").decode("utf-8"))
            check(
                "S6 an index that does not exist falls back instead of breaking",
                broken["selected"] == 1 and broken["prompt"] in {"reel alfa", "reel beta"},
                broken["selected"],
            )
        finally:
            multi.stop()

    # -- il cancello dei font e il ponte fino all'ASS ---------------------- #
    with tempfile.TemporaryDirectory(prefix="vx-fontgate-") as raw3:
        check_font_gate(raw3)
    check_ass_bridge()
    check_derived_jobs()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
