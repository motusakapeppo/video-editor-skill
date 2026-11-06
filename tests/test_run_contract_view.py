"""T1/T2 — la pagina legge il **contratto** del lavoro, non solo il progresso.

S1 - un lavoro con otto firme su otto: `/api/state` porta `run.complete: true`,
     ogni stadio col valore misurato e la soglia, e nulla e' inventato.
S2 - consegna fallita sul contrasto: lo stadio e' `fallito` con la sua frase, il
     master e' `NON-PUBBLICABILE-*` e la testata lo puo' dire senza aprire il
     report; il file esiste, quindi "Guarda il risultato" resta attivo.
S3 - log corrotto a meta': la risposta dichiara **la riga** e non firma niente;
     il troncamento in coda invece si dichiara e lascia le firme vere.
S4 - nessun log: otto stadi mancanti, mai otto verdi per gentilezza.
S5 - la pagina sa disegnarlo: sezione, timbro e tabella delle scelte nel codice.
S6 - a caldo il blocco `run` costa millisecondi: la cache e' sull'mtime.

    python tests/test_run_contract_view.py
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import runlog, serve  # noqa: E402

PASS = 0
FAIL = 0

CONTRACT = {
    "doctor": ("verdetto dell'ambiente non 'broken'", "ready", None, True, "verdetto: ready"),
    "ingest": ("asset usati >= 1", 5, None, True, "5 asset usati nella timeline"),
    "analyze": ("sorgenti misurate >= 1", 2, None, True, "2 sorgenti misurate"),
    "decide": ("densita' di taglio >= 1.0 tagli/10s", 4, 1.0, True, "4 clip, densita' 21.49 tagli/10s >= 1.0"),
    "gates": ("attesa totale <= 5.0 s", 0.0, 5.0, True, "0.0 s di attesa totale (limite 5.0 s)"),
    "render": ("durata master = richiesta +/- 1 frame", 10.0, 10.0, True,
               "durata master 10.000 s, richiesti 10.000 s (+/- 1 frame = 0.033 s)"),
    "qc": ("LUFS = target +/- 1.5", -14.05, -14.0, True, "-14.05 LUFS, target -14.00 +/- 1.5 LU"),
    "deliver": ("contrasto >= 4.5", 9.5, 4.5, True, "contrasto 9.50 >= 4.5"),
}

DELIVER_FAIL = dict(CONTRACT)
DELIVER_FAIL["deliver"] = (
    "contrasto >= 4.5",
    3.56,
    4.5,
    False,
    "titolo illeggibile: contrasto 3.56, serve 4.5",
)


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:60} {detail}")


def write_log(workdir: Path, stages: dict) -> None:
    for name in runlog.STAGES:
        contract, value, threshold, ok, why = stages[name]
        runlog.append(
            workdir,
            stage=name,
            event="sign" if ok else "fail",
            contract=contract,
            value=value,
            threshold=threshold,
            ok=ok,
            why=why,
        )


def make_job(
    root: Path,
    stages: dict,
    *,
    name: str = "lavoro",
    prompt: str = "reel di prova",
    note: str = "consegnato",
    stamp: bool = False,
    log: bool = True,
) -> Path:
    """Un lavoro con panel, master e (se chiesto) il log del contratto."""
    work = root / name
    (work / "out").mkdir(parents=True)
    master = work / "out" / "master.mp4"
    master.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
    (work / "progress.json").write_text(
        json.dumps(
            {
                "prompt": prompt,
                "percent": 100.0,
                "current": None,
                "note": note,
                "master": str(master),
                "clips": 4,
                "elapsed_sec": 300,
            }
        ),
        encoding="utf-8",
    )
    declared = master
    if stamp:
        declared = root / f"NON-PUBBLICABILE-{name}.mp4"
        declared.write_bytes(master.read_bytes())
    (work / "auto_report.json").write_text(
        json.dumps(
            {
                "outcome": "failed" if stamp else "delivered",
                "master": str(declared),
                "deliverable": str(declared),
                "run": {"log": str(runlog.path(work))},
            }
        ),
        encoding="utf-8",
    )
    if log:
        write_log(work, stages)
    return work


def break_log(workdir: Path, *, line: int = 4, truncate_tail: bool = True) -> None:
    """Rompe una riga **centrale** e (se chiesto) tronca la coda."""
    target = runlog.path(workdir)
    rows = target.read_bytes().split(b"\n")
    if rows and rows[-1] == b"":
        rows = rows[:-1]
    if truncate_tail:
        rows[-1] = rows[-1][: len(rows[-1]) // 2]
    rows[line - 1] = b'{"v":1,"seq":' + str(line).encode() + b',"stage":"decide","event":"sig'
    target.write_bytes(b"\n".join(rows))


def state_of(root: Path) -> dict:
    return serve.state(root, 0)


def check_complete(raw: str) -> None:
    """S1: otto firme, valori e soglie veri, `complete` vero."""
    root = Path(raw) / "completo"
    work = make_job(root, CONTRACT, prompt="reel completo")
    data = state_of(root)
    run = data["run"]
    check("S1 run.present: il log del contratto e' stato letto", run["present"] is True)
    check("S1 run.complete: otto firme su otto", run["complete"] is True, run["count"])
    check("S1 otto stadi, tutti `firmato`",
          [run["stages"][name]["state"] for name in runlog.STAGES] == ["firmato"] * 8)
    check("S1 run.failed vuoto e run.corrupt nullo",
          run["failed"] == [] and run["corrupt"] is None)
    check("S1 il valore misurato e' quello del log",
          run["stages"]["render"]["value"] == 10.0
          and run["stages"]["render"]["threshold"] == 10.0,
          (run["stages"]["render"]["value"], run["stages"]["render"]["threshold"]))
    check("S1 ogni stadio porta contratto e frase",
          all(
              run["stages"][name]["contract"] and run["stages"][name]["why"]
              for name in runlog.STAGES
          ))
    check("S1 run.log e' il percorso del log vero",
          Path(run["log"]) == runlog.path(work), run["log"])
    check("S1 l'esito del lavoro resta `done`", data["outcome"] == "done", data["outcome"])
    check("S1 un master pubblicabile non e' timbrato",
          run["master"]["stamped"] is False and run["master"]["name"] == "master.mp4")


def check_failed_delivery(raw: str) -> None:
    """S2: `deliver` fallito, frase in italiano, timbro in testata, file attivo."""
    root = Path(raw) / "fallito"
    make_job(
        root,
        DELIVER_FAIL,
        prompt="reel illeggibile",
        note="consegna rifiutata: il titolo non si legge",
        stamp=True,
    )
    data = state_of(root)
    run = data["run"]
    deliver = run["stages"]["deliver"]
    check("S2 run.complete falso con una firma mancante", run["complete"] is False)
    check("S2 run.failed nomina `deliver`", run["failed"] == ["deliver"], run["failed"])
    check("S2 lo stadio fallito dice stato, valore e soglia",
          deliver["state"] == "fallito"
          and deliver["value"] == 3.56
          and deliver["threshold"] == 4.5,
          (deliver["state"], deliver["value"], deliver["threshold"]))
    check("S2 la frase del fallimento e' quella misurata",
          "contrasto 3.56" in deliver["why"] and "serve 4.5" in deliver["why"],
          deliver["why"])
    check("S2 il master e' timbrato NON-PUBBLICABILE",
          run["master"]["stamped"] is True
          and run["master"]["name"] == "NON-PUBBLICABILE-lavoro.mp4",
          run["master"]["name"])
    check("S2 il file timbrato esiste: il pulsante resta attivo",
          run["master"]["exists"] is True and data["master_ready"] is True)
    check("S2 l'esito non e' `done`: il contratto toglie la promessa",
          data["outcome"] == "failed", data["outcome"])
    check("S2 gli altri sette stadi restano firmati",
          [name for name in runlog.STAGES if run["stages"][name]["state"] == "firmato"]
          == [name for name in runlog.STAGES if name != "deliver"])


def check_corrupt(raw: str) -> None:
    """S3: riga rotta a meta' -> la riga si dichiara, nessuno stadio e' verde."""
    root = Path(raw) / "corrotto"
    work = make_job(root, CONTRACT, prompt="reel corrotto")
    break_log(work)
    data = state_of(root)
    run = data["run"]
    corrupt = run["corrupt"] or {}
    check("S3 run.corrupt dichiara la riga rotta", corrupt.get("line") == 4, corrupt)
    check("S3 run.corrupt dice anche perche'",
          bool(str(corrupt.get("why") or "").strip()), corrupt.get("why"))
    check("S3 nessuno stadio e' firmato con un log rotto", run["count"] == 0, run["count"])
    check("S3 otto stadi mancanti, non otto verdi",
          [run["stages"][name]["state"] for name in runlog.STAGES] == ["mancante"] * 8)
    check("S3 run.complete resta falso", run["complete"] is False)
    check("S3 la coda troncata e' dichiarata a parte",
          run["tail_truncated"] is True, run["tail_truncated"])


def check_tail(raw: str) -> None:
    """S3b: solo la coda troncata: sette firme vere, l'ottava mancante."""
    root = Path(raw) / "coda"
    work = make_job(root, CONTRACT, prompt="reel coda")
    target = runlog.path(work)
    rows = target.read_bytes().split(b"\n")
    if rows and rows[-1] == b"":
        rows = rows[:-1]
    rows[-1] = rows[-1][: len(rows[-1]) // 2]
    target.write_bytes(b"\n".join(rows))

    data = state_of(root)
    run = data["run"]
    check("S3b il log senza riga centrale rotta si legge", run["corrupt"] is None)
    check("S3b la coda troncata si dichiara", run["tail_truncated"] is True)
    check(
        "S3b sette firme vere, l'ottava mancante",
        run["count"] == 7 and run["stages"]["deliver"]["state"] == "mancante",
        run["count"],
    )
    check("S3b `complete` non passa con l'ultimo evento perso", run["complete"] is False)


def check_no_log(raw: str) -> None:
    """S4: un lavoro senza log non riceve firme per gentilezza."""
    root = Path(raw) / "senza"
    make_job(root, CONTRACT, prompt="reel senza log", log=False)
    data = state_of(root)
    run = data["run"]
    check("S4 senza log run.present e' falso", run["present"] is False)
    check("S4 senza log: otto stadi mancanti", run["count"] == 0
          and run["missing"] == list(runlog.STAGES))
    check("S4 senza log `complete` e' falso", run["complete"] is False)
    check("S4 il blocco run ha comunque la forma promessa",
          set(run) >= {"stages", "complete", "failed", "log", "corrupt"})


def check_page_and_http(raw: str) -> None:
    """S5: la pagina porta la sezione, il timbro e le scelte; via HTTP uguale."""
    root = Path(raw) / "http"
    make_job(root, DELIVER_FAIL, prompt="reel http", stamp=True)
    server = serve.Server(root).start()
    try:
        with urllib.request.urlopen(server.url + "api/state", timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        run = data["run"]
        check("S5 /api/state porta il blocco run completo",
              run["failed"] == ["deliver"] and run["complete"] is False,
              run["failed"])
        check("S5 /api/state porta il nome vero del master timbrato",
              run["master"]["stamped"] is True and "NON-PUBBLICABILE-" in run["master"]["name"])

        def body(path: str) -> str:
            with urllib.request.urlopen(server.url + path, timeout=10) as response:
                return response.read().decode("utf-8")

        page = body("ui/index.html")
        script = body("ui/app.js")
        style = body("ui/app.css")
        check("S5 la pagina ha la sezione CONTRATTO", 'id="contract"' in page)
        check("S5 la testata ha il timbro", 'id="jobStamp"' in page)
        check("S5 il disegno degli otto stadi e' nel codice",
              "CONTRACT_STAGES" in script and "paintContract(" in script)
        check("S5 le due scelte del fallimento sono scritte",
              "contorno nero" in script.lower() and "safe zone" in script.lower())
        check("S5 il foglio di stile ha la riga fallita e la striscia",
              ".cstage.is-failed" in style and ".contract-strip" in style)
    finally:
        server.stop()


def check_warm(raw: str) -> None:
    """S6: a caldo il blocco `run` costa millisecondi (cache sull'mtime)."""
    root = Path(raw) / "caldo"
    work = make_job(root, CONTRACT, prompt="reel caldo")

    serve.run_state(work)  # scaldata
    first = serve.run_state(work)
    second = serve.run_state(work)
    check("S6 la cache restituisce lo stesso blocco senza rileggere",
          first["stages"] is second["stages"])
    key = str(runlog.path(work))
    check("S6 la voce di cache e' indicizzata dal log",
          key in serve._run_cache, key)

    values = []
    for _ in range(10):
        start = time.perf_counter()
        serve.run_state(work)
        values.append((time.perf_counter() - start) * 1000.0)
    warm = {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }
    print(f"    [INFO] run_state a caldo (10 giri) ms: {warm}")
    check("S6 la mediana del blocco run resta nei millisecondi",
          warm["median"] < 15.0, warm)
    check("S6 nessun giro supera i 100 ms", warm["max"] < 100.0, warm)

    # La pagina chiede `/api/state` ogni secondo: si misura anche quello, per
    # sapere quanto pesa il contratto sul resto (che non tocca questa prova).
    server = serve.Server(root).start()
    try:
        for _ in range(3):
            urllib.request.urlopen(server.url + "api/state", timeout=10).read()
        values = []
        for _ in range(10):
            start = time.perf_counter()
            urllib.request.urlopen(server.url + "api/state", timeout=10).read()
            values.append((time.perf_counter() - start) * 1000.0)
        api = {
            "min": round(min(values), 1),
            "median": round(statistics.median(values), 1),
            "max": round(max(values), 1),
        }
        print(f"    [INFO] /api/state a caldo (10 richieste) ms: {api}")
        # Qui si controlla solo che la risposta arrivi: il costo vero di
        # ``/api/state`` e' il battito della sessione, che non e' questo blocco.
        check("S6 /api/state risponde, con il contratto dentro", api["max"] < 5000.0, api)
    finally:
        server.stop()


def main() -> int:
    serve._cache_clear()
    serve._run_cache.clear()
    with tempfile.TemporaryDirectory(prefix="vx-runview-") as raw:
        check_complete(raw)
        check_failed_delivery(raw)
        check_corrupt(raw)
        check_tail(raw)
        check_no_log(raw)
        check_page_and_http(raw)
        check_warm(raw)
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
