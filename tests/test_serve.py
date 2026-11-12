"""La UI locale: stato in JSON, fotogrammi serviti, approvazione che apre il cancello.

S1 - `/api/state` riporta fase, percentuale, fotogrammi e approvazione letti dai
     file che la skill ha gia' scritto.
S2 - i fotogrammi si servono come PNG; un nome che non esiste e' 404, non un file
     qualsiasi (nessun path traversal).
S3 - `POST /api/approve` scrive il record **nella forma di keygate**, con lo stesso
     digest del piano: `keygate.check` lo accetta.
S4 - senza un piano da approvare il POST rifiuta, invece di scrivere un record vuoto.
S15 - la vista iniziale e' il lavoro **attivo** (lock vivo o stato fresco), non
      quello col ``progress.json`` toccato per ultimo; un concluso si mostra
      solo con ``showing: "last_finished"`` e l'eta' vera accanto.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import keygate, serve, subs  # noqa: E402

PASS = 0
FAIL = 0

#: I movimenti del corpo che il report di prova dichiara: lo stato deve portarli.
MOVEMENTS = [
    {"from": 0.0, "to": 1.5, "shots": 2, "seconds": 1.5, "density": 1.3333},
]


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def get_status(url: str) -> tuple:
    """Lo status e il corpo, anche quando la risposta e' un 404."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


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
            "S12 un lavoro dedotto non inventa un racconto: journal vuoto",
            job["journal"] == [],
            job["journal"],
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
            check(
                "S12 un progress.json vecchio, senza journal, risponde lista vuota",
                normal["journal"] == [],
                normal["journal"],
            )
        finally:
            server.stop()


def check_outcome() -> None:
    """`outcome` dice come e' finita: la percentuale da sola mente.

    Il caso vero: un lavoro fermato lasciava ``percent: 100`` con un master che
    non esiste, e la UI lo annunciava concluso.
    """
    with tempfile.TemporaryDirectory(prefix="vx-outcome-") as raw:
        base = Path(raw)

        def state_for(name: str, payload: dict) -> dict:
            folder = base / name
            folder.mkdir(parents=True)
            (folder / "progress.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            return serve.state(folder)

        missing = base / "out" / "master.mp4"
        stopped = state_for(
            "fermato",
            {
                "prompt": "reel interrotto",
                "percent": 100.0,
                "current": None,
                "note": "fermato (4294967295)",
                "master": str(missing),
            },
        )
        check(
            "S13 percent 100, note 'fermato', master assente: stopped, non done",
            stopped["outcome"] == "stopped" and stopped["master_ready"] is False,
            {k: stopped[k] for k in ("outcome", "master_ready", "note")},
        )

        empty = state_for(
            "senza_master",
            {"percent": 100.0, "current": None, "note": "consegnato", "master": ""},
        )
        check(
            "S13 percent 100 senza master: incomplete",
            empty["outcome"] == "incomplete" and empty["master_ready"] is False,
            empty["outcome"],
        )

        real = base / "esiste" / "master.mp4"
        real.parent.mkdir(parents=True)
        real.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"m" * 512)
        done = state_for(
            "finito",
            {
                "percent": 100.0,
                "current": None,
                "note": "consegnato",
                "master": str(real),
            },
        )
        check(
            "S13 percent 100 con un master che esiste: done",
            done["outcome"] == "done" and done["master_ready"] is True,
            {k: done[k] for k in ("outcome", "master_ready")},
        )

        running = state_for(
            "in_corso",
            {"percent": 60.0, "current": "render", "note": ""},
        )
        check(
            "S13 una fase in corso resta running",
            running["outcome"] == "running",
            running["outcome"],
        )

        failed = state_for(
            "fallito",
            {
                "percent": 40.0,
                "current": None,
                "note": "errore: il render non e' riuscito",
            },
        )
        check(
            "S13 una nota di errore e' failed",
            failed["outcome"] == "failed",
            failed["outcome"],
        )


def check_shots() -> None:
    """S14 - gli shot del piano e il fotogramma rappresentativo servito.

    Il piano a ``cuts`` prende gli effetti dai trattamenti globali; un documento
    EDL3 li prende dal clip. Nessun ffmpeg: il caso "miniatura gia' pronta" e il
    caso "render in corso" si provano con file finti.
    """
    with tempfile.TemporaryDirectory(prefix="vx-shots-") as raw:
        base = Path(raw)
        job = base / "lavoro"
        (job / "analysis").mkdir(parents=True)
        (job / "source.json").write_text(
            json.dumps({"path": str(base / "IMG_9930.MOV")}), encoding="utf-8"
        )
        jpeg = b"\xff\xd8\xff\xe0" + b"j" * 80 + b"\xff\xd9"
        (job / "analysis" / "thumb_00_002.500.jpg").write_bytes(jpeg)
        (job / "plan.json").write_text(
            json.dumps(
                {
                    "cuts": [
                        {"in": 0.0, "out": 3.2},
                        {"in": 5.0, "out": 7.5},
                        {"in": 9.0, "out": 10.0},
                    ],
                    "treatments": [
                        {"name": "zoom", "enabled": True, "params": {"amount": 0.15}},
                        {"name": "lut", "enabled": True, "params": {"preset": "warm"}},
                        {"name": "fade_in", "enabled": True, "params": {"duration": 0.3}},
                        {"name": "denoise", "enabled": False, "params": {}},
                    ],
                }
            ),
            encoding="utf-8",
        )
        server = serve.Server(job).start()
        try:
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            shots = data.get("shots")
            check(
                "S14 un piano con cuts espone uno shot per cut",
                isinstance(shots, list) and [s["index"] for s in shots] == [0, 1, 2],
                shots,
            )
            check(
                "S14 durata e tempi vengono dai cuts",
                [s["dur"] for s in shots] == [3.2, 2.5, 1.0]
                and [s["t_in"] for s in shots] == [0.0, 5.0, 9.0]
                and [s["t_out"] for s in shots] == [3.2, 7.5, 10.0],
                [(s["t_in"], s["t_out"], s["dur"]) for s in shots],
            )
            check(
                "S14 gli effetti vengono dai trattamenti globali accesi",
                all(s["effects"] for s in shots)
                and "zoom 1.15" in shots[0]["effects"]
                and "lut warm" in shots[0]["effects"]
                and "fade in 0.3s" in shots[0]["effects"]
                and all("denoise" not in s["effects"] for s in shots),
                shots[0]["effects"],
            )
            check(
                "S14 source e' il nome del file, non un percorso inventato",
                {s["source"] for s in shots} == {"IMG_9930.MOV"},
                {s["source"] for s in shots},
            )
            check(
                "S14 thumb punta a /shots/<i> e thumb_ready segue il file",
                all(s["thumb"] == f"/shots/{s['index']}" and s["thumb_ready"] is True
                    for s in shots),
                [(s["thumb"], s["thumb_ready"]) for s in shots],
            )

            status, body = get_status(server.url + "shots/0")
            check(
                "S14 /shots/0 serve la miniatura gia' in analysis come JPEG",
                status == 200 and body == jpeg,
                (status, len(body)),
            )
            status, body = get_status(server.url + "shots/9")
            check(
                "S14 indice fuori intervallo: 404",
                status == 404,
                (status, body[:60]),
            )

            (job / "shots").mkdir()
            cached = b"\xff\xd8\xff\xe0" + b"c" * 40 + b"\xff\xd9"
            (job / "shots" / "0.jpg").write_bytes(cached)
            (job / "analysis" / "thumb_00_002.500.jpg").unlink()
            status, body = get_status(server.url + "shots/0")
            check(
                "S14 la cache estratta serve la seconda volta, senza ffmpeg",
                status == 200 and body == cached,
                (status, len(body)),
            )
            status, body = get_status(server.url + "shots/1")
            check(
                "S14 senza miniatura e senza sorgente: 404 italiano",
                status == 404 and "sorgente" in body.decode("utf-8", "replace"),
                (status, body.decode("utf-8", "replace")[:60]),
            )

            (job / "plan.json").write_text(
                json.dumps(
                    {
                        "version": "vx.edl3/1.2",
                        "clips": [
                            {
                                "id": "c0",
                                "source": "b.mov",
                                "src_in": 1.0,
                                "src_out": 3.0,
                                "dst_in": 0.0,
                                "dst_out": 2.0,
                                "layer": 0,
                                "effects": [
                                    {"kind": "lut", "params": {"preset": "warm"}},
                                    {"kind": "fade_in", "params": {"duration": 0.3}},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            edl3 = data["shots"]
            check(
                "S14 un piano EDL3 prende gli effetti dal clip",
                len(edl3) == 1
                and edl3[0]["dur"] == 2.0
                and edl3[0]["source"] == "b.mov"
                and "lut warm" in edl3[0]["effects"]
                and "fade in 0.3s" in edl3[0]["effects"],
                edl3,
            )
        finally:
            server.stop()

    with tempfile.TemporaryDirectory(prefix="vx-shots-render-") as raw:
        job = Path(raw) / "in_render"
        (job / "render").mkdir(parents=True)
        (job / "plan.json").write_text(
            json.dumps({"cuts": [{"in": 0.0, "out": 1.5}]}), encoding="utf-8"
        )
        (job / "render" / "ffmpeg-progress.txt").write_text(
            "out_time_us=0\nspeed=0.0x\nprogress=continue\n", encoding="utf-8"
        )
        server = serve.Server(job).start()
        try:
            status, body = get_status(server.url + "shots/0")
            reason = body.decode("utf-8", "replace")
            check(
                "S14 durante un render non si estrae: 404 e il motivo",
                status == 404 and "durante il render" in reason,
                (status, reason[:60]),
            )
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S14 durante il render thumb_ready resta falso",
                data["shots"] and data["shots"][0]["thumb_ready"] is False,
                data["shots"],
            )
        finally:
            server.stop()


def check_activity() -> None:
    """S15 - la vista iniziale e' cio' che gira, non cio' che e' stato toccato tardi.

    Il caso vero: il lavoro di analisi era concluso, ma un file di stato toccato
    per ultimo lo faceva sembrare "il piu' recente"; la pagina mostrava il suo
    100% mentre il render nuovo lavorava. Qui l'attivo si riconosce dal lock vivo
    e l'eta' vera resta scritta accanto alla percentuale.
    """
    now = time.time()

    def panel(folder: Path, payload: dict, age_sec: float) -> Path:
        """Un ``progress.json`` con un'eta' dichiarata, non lasciata al caso."""
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "progress.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        if age_sec > 0:
            os.utime(target, (now - age_sec, now - age_sec))
        return target

    def master_for(folder: Path) -> Path:
        master = folder / "out" / "master.mp4"
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"m" * 512)
        return master

    # -- 1. A e' concluso ma toccato adesso; B gira, con un lock vivo --------- #
    with tempfile.TemporaryDirectory(prefix="vx-active-") as raw:
        base = Path(raw)
        finished = base / "analisi_conclusa"
        panel(
            finished,
            {
                "prompt": "analisi",
                "percent": 100.0,
                "current": None,
                "note": "consegnato",
                "master": str(master_for(finished)),
            },
            0.0,
        )
        working = base / "render_attivo"
        panel(
            working,
            {"prompt": "reel in corso", "percent": 40.0, "current": "render", "note": ""},
            120.0,
        )
        (working / ".lock").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "token": "prova-attivo",
                    "created": now - 120,
                    "updated": now,
                    "stage": "render",
                    "host": "",
                    # 0.0 = creation time non nota: vale il pid, che e' vivo.
                    "created_process": 0.0,
                }
            ),
            encoding="utf-8",
        )

        view = serve.state(base)
        check(
            "S15 la vista iniziale e' il lavoro col lock vivo, non l'ultimo mtime",
            view["job"] == "render_attivo" and view["showing"] == "active",
            (view["job"], view["showing"]),
        )
        check(
            "S15 il lavoro attivo dichiara active, running e l'eta' vera",
            view["active"] is True
            and view["locked"] is True
            and view["state"] == "running"
            and view["updated_sec"] is not None
            and 100 <= view["updated_sec"] <= 200
            and view["updated_at"].endswith("+00:00"),
            {k: view[k] for k in ("active", "locked", "state", "updated_sec", "updated_at")},
        )
        by_name = {item["job"]: item for item in view["jobs"]}
        check(
            "S15 jobs[] porta active e updated_sec per ogni lavoro",
            all(
                "active" in item and "updated_sec" in item and "updated_at" in item
                for item in view["jobs"]
            )
            and by_name["render_attivo"]["active"] is True
            and by_name["analisi_conclusa"]["updated_sec"] is not None,
            [(item["job"], item["active"], item["updated_sec"]) for item in view["jobs"]],
        )
        check(
            "S15 l'analisi ha l'mtime piu' fresco ma non e' la vista: vince il lock",
            by_name["analisi_conclusa"]["updated_sec"]
            < by_name["render_attivo"]["updated_sec"]
            and view["selected"] == by_name["render_attivo"]["index"],
            (
                by_name["analisi_conclusa"]["updated_sec"],
                by_name["render_attivo"]["updated_sec"],
                view["selected"],
            ),
        )

    # -- 2. nessun attivo: l'ultimo concluso si mostra, ma lo dichiara -------- #
    with tempfile.TemporaryDirectory(prefix="vx-finished-") as raw:
        base = Path(raw)
        last = base / "ultimo"
        panel(
            last,
            {
                "prompt": "ultimo finito",
                "percent": 100.0,
                "current": None,
                "note": "consegnato",
                "master": str(master_for(last)),
            },
            1200.0,
        )
        view = serve.state(base)
        check(
            "S15 senza niente in corso la vista mostra l'ultimo concluso, e lo dice",
            view["job"] == "ultimo"
            and view["showing"] == "last_finished"
            and view["active"] is False
            and view["percent"] == 100.0
            and view["state"] == "finished",
            {k: view[k] for k in ("job", "showing", "active", "state", "percent")},
        )
        check(
            "S15 l'eta' non mente: un 100% vecchio si legge vecchio",
            view["updated_sec"] is not None
            and 1150 <= view["updated_sec"] <= 1300,
            view["updated_sec"],
        )

    # -- 3. dieci minuti di silenzio, nessun lock: non e' attivo -------------- #
    with tempfile.TemporaryDirectory(prefix="vx-quiet-") as raw:
        base = Path(raw)
        panel(
            base / "fermo",
            {"prompt": "fermo", "percent": 55.0, "current": "render", "note": ""},
            600.0,
        )
        quiet = serve.state(base)
        check(
            "S15 dieci minuti senza lock: non attivo, ed e' l'eta' a dirlo",
            quiet["active"] is False
            and quiet["locked"] is False
            and quiet["updated_sec"] is not None
            and 570 <= quiet["updated_sec"] <= 630,
            {k: quiet[k] for k in ("active", "locked", "updated_sec")},
        )


def check_artifact_verdict() -> None:
    """D15/D16 - l'esito viene dagli artefatti, e con piu' lavori c'e' il selettore.

    Il caso vero: un job con ``out/master.mp4`` ma senza ``progress.json``
    appariva "Non riuscito" perche' lo stato si deduceva dalla *presenza del
    panel*. Qui il master esiste e si legge: il lavoro e' concluso. La vista
    iniziale resta il lavoro con l'attivita' piu' recente, e il selettore lo dice.
    """
    with tempfile.TemporaryDirectory(prefix="vx-artifacts-") as raw:
        base = Path(raw)
        now = time.time()

        completed = base / "con_master"
        (completed / "out").mkdir(parents=True)
        master = completed / "out" / "master.mp4"
        master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"m" * 4096)
        os.utime(master, (now, now))

        stale = base / "in_corso"
        stale.mkdir()
        panel = stale / "progress.json"
        panel.write_text(
            json.dumps(
                {
                    "prompt": "render fermo",
                    "percent": 10.0,
                    "current": "render",
                    "note": "",
                }
            ),
            encoding="utf-8",
        )
        os.utime(panel, (now - 600, now - 600))

        view = serve.state(base)
        check(
            "D15 un master che esiste e si legge e' un lavoro completato",
            view["outcome"] == "done"
            and view["master_ready"] is True
            and view["progress_source"] == "derived",
            {key: view[key] for key in ("outcome", "master_ready", "progress_source")},
        )
        check(
            "D15 la nota dice che il master c'e', non che il panel manca",
            "master presente" in view["note"] and "senza progresso" in view["note"],
            view["note"],
        )
        by_name = {item["job"]: item for item in view["jobs"]}
        check(
            "D16 due lavori: il selettore e' acceso e sceglie l'attivita' piu' recente",
            view["selector"] is True
            and view["selected"] == by_name["con_master"]["index"]
            and view["job"] == "con_master",
            (view["job"], view["selected"], view["selector"]),
        )
        check(
            "D16 ogni voce del selettore dichiara il suo esito",
            by_name["con_master"]["outcome"] == "done"
            and by_name["in_corso"]["outcome"] == "stopped",
            {name: item["outcome"] for name, item in by_name.items()},
        )
        check(
            "D15 un lavoro scelto a mano mostra il suo esito, non quello del primo",
            serve.state(base, by_name["in_corso"]["index"])["outcome"] == "stopped",
            serve.state(base, by_name["in_corso"]["index"])["outcome"],
        )

        bogus = base / "segnaposto"
        (bogus / "out").mkdir(parents=True)
        (bogus / "out" / "master.mp4").write_bytes(b"non e' un video\n")
        check(
            "D15 un master illeggibile non viene venduto come completato",
            serve.state(bogus)["outcome"] != "done",
            serve.state(bogus)["outcome"],
        )


def check_render_workdir() -> None:
    """D12/D25 - live, domande e decisioni vivono nella workdir del report."""
    with tempfile.TemporaryDirectory(prefix="vx-render-wd-") as raw:
        base = Path(raw)
        job = base / "job"
        (job / "out").mkdir(parents=True)
        (job / "out" / "master.mp4").write_bytes(
            b"\x00\x00\x00\x18ftypmp42" + b"m" * 1024
        )
        render_dir = base / "altrove" / "render"
        live = render_dir / "live"
        (render_dir / "questions").mkdir(parents=True)
        live.mkdir(parents=True)
        (live / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "vx.live/1",
                    "chunks": [
                        {"index": 0, "file": "chunk_000.mp4", "t_in": 0.0,
                         "t_out": 2.0, "bytes": 5, "sha256": "-", "ready": True}
                    ],
                    "total": 1,
                    "ready": 1,
                }
            ),
            encoding="utf-8",
        )
        (live / "chunk_000.mp4").write_bytes(b"piece")
        (render_dir / "questions" / "subtitles.json").write_text(
            json.dumps(
                {
                    "id": "subtitles",
                    "question": "Vuoi i sottotitoli nel master?",
                    "options": [
                        {"value": "no", "label": "No", "detail": ""},
                        {"value": "yes", "label": "Si'", "detail": ""},
                    ],
                    "default": "no",
                }
            ),
            encoding="utf-8",
        )
        decisions = [
            {"id": "subtitles", "question": "sottotitoli?", "chosen": "no"},
            {"id": "transition_kinds", "question": "transizioni?", "chosen": "crossfade"},
        ]
        (job / "report.json").write_text(
            json.dumps(
                {
                    "workdir": str(render_dir),
                    "chunks": {"workdir": str(render_dir)},
                    "decisions": decisions,
                }
            ),
            encoding="utf-8",
        )

        state = serve.state(job)
        check(
            "D12 lo stato live arriva dalla workdir dichiarata dal report",
            state["live"]["ready"] == 1
            and state["live"]["workdir"] == str(render_dir.resolve())
            and state["live"]["mismatch"] is True,
            {key: state["live"].get(key) for key in ("ready", "workdir", "mismatch")},
        )
        check(
            "D25 le decisioni del report arrivano alla vista",
            [item["id"] for item in state["decisions"]]
            == ["subtitles", "transition_kinds"],
            state["decisions"],
        )
        check(
            "D25 la domanda scritta nella workdir del render e' in pagina",
            [item["id"] for item in state["questions"]] == ["subtitles"],
            state["questions"],
        )

        server = serve.Server(job).start()
        try:
            status, body = get_status(server.url + "live/chunk_000.mp4")
            check(
                "D12 il chunk si serve dalla workdir del report, non da accanto al master",
                status == 200 and body == b"piece",
                (status, body),
            )
            answer = post(server.url + "api/answer?job=0&id=subtitles&value=yes")
            check(
                "D25 la risposta e' scritta dove la domanda esiste",
                answer.get("ok") is True
                and (render_dir / "questions" / "subtitles.answer.json").is_file()
                and not (render_dir / "questions" / "subtitles.json").exists(),
                answer,
            )
        finally:
            server.stop()


def check_skipped_stage() -> None:
    """Uno stadio saltato si dichiara saltato, con il suo motivo.

    Prima la vista non aveva un ramo per ``event: skip``: lo stadio finiva in
    "mancante", cioè sembrava un buco, e il motivo scritto nel log non arrivava
    mai a schermo.
    """
    from vx import runlog

    with tempfile.TemporaryDirectory(prefix="vx-runskip-") as raw:
        work = Path(raw)
        for stage in runlog.STAGES:
            if stage == "analyze":
                runlog.append(
                    work,
                    stage=stage,
                    event="skip",
                    contract="analisi richiesta",
                    value=None,
                    ok=True,
                    why="--no-analysis: il girato ha già i metadati",
                )
                continue
            runlog.append(
                work,
                stage=stage,
                event="sign",
                contract=f"{stage} fatto",
                value=1,
                threshold=1,
                ok=True,
            )
        state = serve.run_state(work)
        stages = state["stages"]
        check(
            "S14 lo stadio saltato non è 'mancante'",
            stages["analyze"]["state"] == "saltato"
            and "analyze" in state["skipped"]
            and "analyze" not in state["missing"]
            and bool(stages["analyze"]["why"]),
            (stages["analyze"], state["skipped"], state["missing"]),
        )
        check(
            "S14 il lavoro con uno stadio saltato non è completo",
            state["complete"] is False and state["count"] == 7,
            (state["complete"], state["count"]),
        )


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
                    "journal": [
                        {"t": 1.0, "phase": "triage",
                         "line": "Misuro 12 clip: 4 scartate, 8 buone",
                         "at": "2026-01-01T00:00:01+00:00"},
                        {"t": 8.5, "phase": "render",
                         "line": "Scrivo il master: 30,0 secondi",
                         "at": "2026-01-01T00:00:08+00:00"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (job / "keyframes" / "kf0.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        # Il report di un lavoro con la scaletta composta: `/api/state` deve
        # portarla, perché la pagina legge solo quello.
        (job / "auto_report.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "composition": {
                        "schema": "vx.composition/1",
                        "proposed": {},
                        "rules": {},
                        "declined": [],
                        "applied": {},
                    },
                    "sections": {
                        "total": 6,
                        "filled": 1,
                        "sections": [{"id": f"s{index}"} for index in range(6)],
                    },
                    "movements": MOVEMENTS,
                    # Quello che è rimasto fuori: il report lo diceva, la
                    # pagina non lo mostrava. Lo stato deve portarlo.
                    "warnings": ["coda non raggiunta: 0,4 s"],
                    "assets": {
                        "total": 5,
                        "selected": 3,
                        "rejected": 2,
                        "ignored": [
                            {"asset_id": "notes.txt", "reason": "unknown_extension", "suffix": ".txt"}
                        ],
                    },
                    "rejected": [
                        {
                            "asset_id": "buio.mp4",
                            "rejection_reason": "TOO_DARK",
                            "details": {"luma": 0.03},
                        }
                    ],
                    "bridge": {
                        "enabled": True,
                        "cuts": 2,
                        "cuts_removed_sec": 1.4,
                        "not_applied": 2,
                        "not_applied_reasons": {
                            "broll_below_threshold": {"count": 2, "seconds": 0.0, "words": 0}
                        },
                    },
                    "timeline": {
                        "tail": {"requested": 20.0, "actual": 19.6, "reachable": False}
                    },
                    "plan": {"excluded": [{"clip_id": "c9", "why": "sotto la soglia"}]},
                }
            ),
            encoding="utf-8",
        )
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
            check(
                "S1b gate closed: not required, not waiting, and the state says why",
                data["approval"]["required"] is False
                and data["approval"]["waiting"] is False
                and data["approval"]["approved"] is False
                and bool(data["approval"]["reason"]),
                data["approval"],
            )
            # La scaletta composta viaggia con lo stato: la pagina non ha un
            # endpoint suo, e se queste tre chiavi sparissero la sezione si
            # svuoterebbe senza un test rosso.
            check(
                "S1c lo stato porta composition, sections e movements",
                (data.get("composition") or {}).get("schema") == "vx.composition/1"
                and len((data.get("sections") or {}).get("sections") or []) == 6
                and data.get("movements") == MOVEMENTS,
                ((data.get("composition") or {}).get("schema"), data.get("movements")),
            )
            out = data.get("out_of_frame") or {}
            check(
                "S1d lo stato porta quello che è rimasto fuori",
                out.get("empty") is False
                and out.get("warnings") == ["coda non raggiunta: 0,4 s"]
                and [row["asset_id"] for row in out.get("ignored") or []] == ["notes.txt"]
                and [row["reason"] for row in out.get("rejected") or []] == ["TOO_DARK"]
                and (out.get("not_applied") or {}).get("broll_below_threshold", {}).get("count") == 2
                and (out.get("tail") or {}).get("requested") == 20.0
                and [row["clip_id"] for row in out.get("excluded") or []] == ["c9"]
                and out.get("cuts") == 2
                and out.get("cuts_removed_sec") == 1.4,
                out,
            )

            journal = data.get("journal")
            check(
                "S12 /api/state carries the journal written in progress.json",
                isinstance(journal, list)
                and [row["line"] for row in journal]
                == ["Misuro 12 clip: 4 scartate, 8 buone",
                    "Scrivo il master: 30,0 secondi"]
                and [row["phase"] for row in journal] == ["triage", "render"]
                and journal[1]["t"] == 8.5,
                journal,
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

            # -- il cancello vero: il render fermo, e il POST che lo sblocca --- #
            (job / "keyframes" / "waiting.json").write_text(
                json.dumps(
                    {
                        "schema": "vx.keygate-wait/1",
                        "since_utc": "2026-11-03T01:00:00+00:00",
                        "frames": [{"clip": "c0", "at": 1.0}],
                    }
                ),
                encoding="utf-8",
            )
            (job / keygate.RECORD_NAME).unlink()
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S3b waiting: the render is stopped there right now",
                data["approval"] == {
                    "required": True,
                    "approved": False,
                    "waiting": True,
                    "reason": "",
                },
                data["approval"],
            )
            result = post(server.url + "api/approve")
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S3b the POST approves and clears the wait",
                result.get("ok") is True
                and data["approval"]["approved"] is True
                and data["approval"]["waiting"] is False
                and data["approval"]["required"] is True
                and not (job / "keyframes" / "waiting.json").exists(),
                data["approval"],
            )
            mismatched = json.loads((job / keygate.RECORD_NAME).read_text(encoding="utf-8"))
            mismatched["digest"] = "f" * 64
            (job / keygate.RECORD_NAME).write_text(
                json.dumps(mismatched), encoding="utf-8"
            )
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S3b an approval of another montage does not open this gate",
                data["approval"]["approved"] is False
                and data["approval"]["required"] is False
                and bool(data["approval"]["reason"]),
                data["approval"],
            )
            post(server.url + "api/approve")

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
            # Anche questa chiamata va con REVEAL finto: senza patch partiva
            # Explorer vero sulla cartella fixture in TEMP, e la suite apriva
            # una finestra "vuota" a ogni giro (visto dall'utente, 2026-11-06).
            opened = []
            original = serve.REVEAL
            serve.REVEAL = lambda where: opened.append(Path(where))
            try:
                answer = post(server.url + "api/reveal")
            finally:
                serve.REVEAL = original
            # Con il master sparito non si apre il nulla in silenzio: si apre la
            # **cartella del lavoro** e lo si dice. La proprieta' di sicurezza
            # resta: il percorso aperto sta dentro il lavoro, mai uno scelto dal
            # client.
            opened_folder = Path(str(answer.get("folder") or ""))
            check(
                "S5 senza il file si apre la cartella del lavoro, dichiarandolo",
                answer.get("ok") is True
                and opened_folder.is_dir()
                and str(opened_folder).startswith(str(job))
                and opened == [opened_folder]
                and "master" in str(answer.get("note", "")),
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
            data = json.loads(get(server.url + "api/state").decode("utf-8"))
            check(
                "S4 without a plan the state says the gate does not apply",
                data["approval"]["required"] is False
                and data["approval"]["waiting"] is False
                and "nessun piano di fotogrammi" in data["approval"]["reason"],
                data["approval"],
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
    check_outcome()
    check_shots()
    check_activity()
    check_artifact_verdict()
    check_render_workdir()
    check_skipped_stage()

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
