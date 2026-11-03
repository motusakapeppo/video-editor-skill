"""vx.learning: archivio leggero, eventi atomici, rotazione, potatura, policy.

Nessuna rete, nessun video: i fotogrammi dei test sono JPEG veri generati in
memoria, e l'archivio vive in una cartella temporanea che si cancella alla fine.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import learning  # noqa: E402

PASS = 0
FAIL = 0

MODULE = ROOT / "scripts" / "vx" / "learning.py"
NETWORK = ("socket", "urllib", "http.client", "requests", "httpx", "aiohttp")
MB = 1024 * 1024

try:  # i fotogrammi sono JPEG veri: il modulo li legge dai marcatori SOF
    from PIL import Image
    import random
except Exception:  # pragma: no cover - l'ambiente di test ha Pillow
    Image = None  # type: ignore
    random = None  # type: ignore


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:62} {detail}")


def make_thumb(path: Path, width: int, height: int, *, noise: bool = True) -> Path:
    """Un JPEG a dimensione dichiarata; ``noise`` lo rende pesante come una foto."""
    if noise:
        raw = bytes(random.getrandbits(8) for _ in range(width * height * 3))
        image = Image.frombytes("RGB", (width, height), raw)
    else:
        image = Image.new("RGB", (width, height), (40, 90, 140))
    image.save(path, format="JPEG", quality=85)
    return path


def all_files(base: Path) -> list:
    return [path for path in base.rglob("*") if path.is_file()]


def bytes_of(base: Path) -> int:
    total = 0
    for path in all_files(base):
        if path.name.endswith(".lock"):
            continue
        total += path.stat().st_size
    return total


# --------------------------------------------------------------------------- #
# eventi: una riga, rotazione, concorrenza
# --------------------------------------------------------------------------- #


def test_events(base: Path) -> None:
    root = base / "learning-events"
    stamp = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
    learning.append_event("failure", {"reason": "gamma alta"}, job_id="job-a", root=root, when=stamp)
    live = root / "events" / "2026-11.jsonl"
    check("evento scritto in events/2026-11.jsonl", live.is_file(), live.name)
    lines = live.read_text(encoding="utf-8").splitlines()
    check("un evento = una riga", len(lines) == 1, len(lines))
    record = json.loads(lines[0])
    check(
        "record event: schema, kind, job_id, payload",
        record["schema"] == learning.SCHEMA_EVENT
        and record["kind"] == "failure"
        and record["job_id"] == "job-a"
        and record["payload"] == {"reason": "gamma alta"},
        record,
    )

    learning.append_event("failure", {"reason": "vecchio"}, root=root, when=datetime(2026, 1, 9, tzinfo=timezone.utc))
    check(
        "rotazione mensile: due file per due mesi",
        sorted(path.name for path in (root / "events").glob("*.jsonl"))
        == ["2026-01.jsonl", "2026-11.jsonl"],
        sorted(path.name for path in (root / "events").glob("*.jsonl")),
    )
    check("read_events(month) filtra un mese", len(learning.read_events("2026-01", root=root)) == 1)
    check("read_events() legge tutti i mesi", len(learning.read_events(root=root)) == 2)

    try:
        learning.append_event("", {}, root=root)
        check("kind vuoto rifiutato", False)
    except ValueError:
        check("kind vuoto rifiutato", True)
    try:
        learning.append_event("failure", ["non", "e'", "un", "oggetto"], root=root)
        check("payload non-oggetto rifiutato", False)
    except TypeError:
        check("payload non-oggetto rifiutato", True)
    try:
        learning.read_events("novembre", root=root)
        check("mese malformato rifiutato", False)
    except ValueError:
        check("mese malformato rifiutato", True)


def test_concurrent_append(base: Path) -> None:
    root = base / "learning-concurrent"
    threads = 8
    per_thread = 5
    start = threading.Barrier(threads)
    errors: list = []

    def writer(index: int) -> None:
        try:
            start.wait(timeout=10)
            for step in range(per_thread):
                learning.append_event(
                    "failure",
                    {"worker": index, "step": step, "note": "x" * 200},
                    job_id=f"job-{index}",
                    root=root,
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=writer, args=(index,)) for index in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    events = learning.read_events(root=root)
    check("concorrenza: nessun errore nei thread", not errors, errors[:1])
    check(
        f"concorrenza: {threads * per_thread} eventi, nessuno perso",
        len(events) == threads * per_thread,
        len(events),
    )
    check(
        "concorrenza: ogni riga e' JSON integro",
        all(isinstance(item.get("payload"), dict) for item in events),
        len(events),
    )
    check(
        "concorrenza: nessuna riga fusa",
        all(len(item["payload"]["note"]) == 200 for item in events),
        "",
    )


# --------------------------------------------------------------------------- #
# corpus: tre fotogrammi a 320 px, mai un video
# --------------------------------------------------------------------------- #


def test_corpus(base: Path) -> None:
    root = base / "learning-corpus"
    thumbs = base / "thumbs"
    thumbs.mkdir()
    small = make_thumb(thumbs / "a.jpg", 320, 180)
    tall = make_thumb(thumbs / "b.jpg", 180, 320)
    square = make_thumb(thumbs / "c.jpg", 320, 320)
    plan = {"prompt": "montaggio lento", "shots": [{"in": 0, "out": 2.4}], "note": "x" * 800}
    metrics = {"outcome": "failed", "balance": "low", "rating": 2, "estimate_seconds": 240, "actual_seconds": 300}

    written = learning.store_failure("job-0001", plan, metrics, [small, tall, square], root=root)
    folder = root / "corpus" / "job-0001"
    check(
        "corpus: piano, misure e tre fotogrammi",
        sorted(path.name for path in folder.iterdir())
        == ["metrics.json", "plan.json", "thumb_0.jpg", "thumb_1.jpg", "thumb_2.jpg"],
        sorted(path.name for path in folder.iterdir()),
    )
    check("corpus: piano conservato come testo", "montaggio lento" in (folder / "plan.json").read_text(encoding="utf-8"))
    check("corpus: misure conservate", json.loads((folder / "metrics.json").read_text(encoding="utf-8"))["metrics"]["rating"] == 2)
    check(
        "corpus: dimensione dichiarata dal record",
        written["bytes"] == sum(path.stat().st_size for path in folder.iterdir()),
        written["bytes"],
    )
    check(
        "corpus: fotogrammi restano <= 320 px",
        all(max(learning.jpeg_size(path.read_bytes()) or (9999, 9999)) <= 320 for path in folder.glob("thumb_*.jpg")),
        [learning.jpeg_size(path.read_bytes()) for path in folder.glob("thumb_*.jpg")],
    )

    four = [make_thumb(thumbs / f"extra{i}.jpg", 320, 180) for i in range(4)]
    try:
        learning.store_failure("job-bad", {}, {}, four, root=root)
        check("rifiuta piu' di tre fotogrammi", False)
    except ValueError as exc:
        check("rifiuta piu' di tre fotogrammi", "troppi" in str(exc), str(exc)[:50])
    big = make_thumb(thumbs / "big.jpg", 640, 360)
    try:
        learning.store_failure("job-big", {}, {}, [big], root=root)
        check("rifiuta un fotogramma sopra 320 px", False)
    except ValueError as exc:
        check("rifiuta un fotogramma sopra 320 px", "troppo grande" in str(exc), str(exc)[:50])
    notjpg = thumbs / "frame.png"
    notjpg.write_bytes(b"\x89PNG\r\n\x1a\n")
    try:
        learning.store_failure("job-png", {}, {}, [notjpg], root=root)
        check("rifiuta un fotogramma non JPEG", False)
    except ValueError as exc:
        check("rifiuta un fotogramma non JPEG", "non JPEG" in str(exc), str(exc)[:50])
    fake_video = thumbs / "master.mp4"
    fake_video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    try:
        learning.store_failure("job-mp4", {}, {}, [fake_video], root=root)
        check("un video non entra nel corpus", False)
    except ValueError:
        check("un video non entra nel corpus", True)
    check(
        "nessun .mp4/.mov dentro learning/",
        not [path for path in all_files(root) if path.suffix.lower() in (".mp4", ".mov", ".mkv")],
        "",
    )
    check("job_id con separatore rifiutato", _rejects(lambda: learning.store_failure("../evil", {}, {}, root=root)))

    # cinque lavori finti: la misura che conta e' la media per corpus
    for index in range(1, 5):
        learning.store_failure(
            f"job-000{index + 1}",
            {"prompt": f"lavoro {index}", "pad": "x" * 600},
            {"outcome": "failed", "rating": 1 + index % 4},
            [make_thumb(base / "thumbs" / f"set{index}_{n}.jpg", 320, 180) for n in range(3)],
            root=root,
        )
    corpora = [path for path in (root / "corpus").iterdir() if path.is_dir()]
    total = sum(learning._dir_bytes(path) for path in corpora)
    average = total / len(corpora)
    check(
        "dimensione media corpus < 300 KB",
        average < 300 * 1024,
        f"{average / 1024:.1f} KB su {len(corpora)} corpus",
    )
    check(
        "nessun file video nell'archivio dopo cinque lavori",
        not [path for path in all_files(root) if path.suffix.lower() in (".mp4", ".mov")],
        "",
    )


def _rejects(call) -> bool:
    try:
        call()
    except (ValueError, TypeError):
        return True
    return False


# --------------------------------------------------------------------------- #
# stato, sfondamento, potatura
# --------------------------------------------------------------------------- #


def test_status_and_prune(base: Path) -> None:
    root = base / "learning-prune"
    thumbs = base / "prune-thumbs"
    thumbs.mkdir()
    for index in range(5):
        stamp = (datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=index)).isoformat()
        learning.store_failure(
            f"job-{index:04d}",
            {"prompt": f"lavoro {index}", "pad": "p" * 500},
            {"outcome": "failed", "rating": 2, "estimate_seconds": 100, "actual_seconds": 120},
            [
                make_thumb(thumbs / f"{index}_{n}.jpg", 320, 180)
                for n in range(3)
            ],
            root=root,
            captured_utc=stamp,
        )
    learning.append_event("failure", {"note": "x"}, job_id="job-0000", root=root)

    data = learning.status(root=root)
    check("status: bytes uguali alla somma dei file", data["bytes"] == bytes_of(root), (data["bytes"], bytes_of(root)))
    check("status: corpus_count = 5", data["corpus_count"] == 5, data["corpus_count"])
    check("status: eventi contati", data["events_count"] >= 5, data["events_count"])
    check("status: aree presenti", set(data["by_area"]) >= {"events", "archive", "corpus", "policy"}, list(data["by_area"]))
    check("status: oldest e' una data", bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:]+Z", data["oldest"] or "")), data["oldest"])
    check("status: sotto 20 MB non c'e' nulla da potare", data["will_prune_next"] == [], data["will_prune_next"])
    check("status: budget rispettato a 20 MB", not data["over_budget"])

    before = data["bytes"]
    tight = learning.status(root=root, budget_mb=0.1)
    check("status: a 0.1 MB dichiara sfondamento", tight["over_budget"] is True, f"{tight['bytes']} B")
    check("status: will_prune_next non e' vuoto", len(tight["will_prune_next"]) > 0, tight["will_prune_next"])

    learning.policy_save(
        {"thresholds": {"variety_min": 0.6}, "corpus_refs": ["job-0002"]},
        reason="il montaggio era troppo ripetitivo",
        feedback_ids=["fb-1", "fb-2"],
        root=root,
    )
    result = learning.prune(root=root, budget_mb=0.1)
    after = learning.status(root=root, budget_mb=0.1)
    check("prune: i byte scendono", after["bytes"] < before, f"{before} -> {after['bytes']}")
    check("prune: il corpus citato dalla policy resta", (root / "corpus" / "job-0002").is_dir())
    check("prune: il corpus piu' recente resta", (root / "corpus" / "job-0004").is_dir())
    check("prune: il corpus piu' vecchio non citato sparisce", not (root / "corpus" / "job-0000").is_dir())
    check("prune: conto coerente con lo stato", after["corpus_count"] == len([p for p in (root / "corpus").iterdir() if p.is_dir()]), after["corpus_count"])
    check("prune: policy non toccata", (root / "policy" / "policy.json").is_file())
    check("prune: report con bytes_after", result["bytes_after"] == after["bytes"], result["bytes_after"])
    check(
        "prune: con budget largo non rimuove niente",
        learning.prune(root=root, budget_mb=20)["removed"] == [],
    )


# --------------------------------------------------------------------------- #
# archivio compresso e policy versionata
# --------------------------------------------------------------------------- #


def test_archive(base: Path) -> None:
    root = base / "learning-archive"
    for index in range(12):
        learning.append_event("failure", {"n": index, "note": "y" * 120}, root=root, when=datetime(2026, 9, 10, tzinfo=timezone.utc))
    live = root / "events" / "2026-09.jsonl"
    original = live.stat().st_size
    done = learning.archive_events(root=root, keep_month="2026-11")
    archived = list((root / "archive").glob("2026-09.jsonl.*"))
    check("archive: un file compresso", len(archived) == 1 and archived[0].suffix in (".zst", ".gz"), [p.name for p in archived])
    check("archive: l'originale e' sparito", not live.exists())
    check("archive: comprime davvero", archived[0].stat().st_size < original, f"{original} -> {archived[0].stat().st_size}")
    check("archive: read_events legge il mese compresso", len(learning.read_events("2026-09", root=root)) == 12)
    check("archive: read_events(None) include l'archivio", len(learning.read_events(root=root)) == 12)
    check("archive: il mese corrente non si archivia", learning.archive_events(root=root) == [], "")
    check("archive: report per mese", len(done) == 1 and done[0]["month"] == "2026-09", done)


def test_policy(base: Path) -> None:
    root = base / "learning-policy"
    fresh = learning.policy_load(root=root)
    check("policy: default con version 0", fresh["version"] == 0 and "variety_min" in fresh["thresholds"], fresh["version"])
    first = learning.policy_save(
        {"thresholds": {"variety_min": 0.6, "cut_rate_max": 0.7}},
        reason="prima taratura",
        feedback_ids=["fb-1"],
        root=root,
    )
    check("policy: la prima scrittura e' v1", first["version"] == 1, first["version"])
    check(
        "policy: history v001 scritta",
        (root / "policy" / "history" / "v001.json").is_file(),
        "",
    )
    second = learning.policy_save(
        {"thresholds": {"variety_min": 0.65, "cut_rate_max": 0.7}},
        reason="seconda taratura",
        feedback_ids=["fb-1", "fb-2"],
        root=root,
    )
    history = json.loads((root / "policy" / "history" / "v002.json").read_text(encoding="utf-8"))
    check("policy: la seconda scrittura e' v2", second["version"] == 2, second["version"])
    check("policy: il diff nomina la soglia cambiata", "thresholds.variety_min" in history["diff"]["changed"], history["diff"])
    check("policy: il motivo resta nella storia", history["reason"] == "seconda taratura", history["reason"])
    check("policy: hash dei feedback citati", re.fullmatch(r"[0-9a-f]{64}", history["feedback_digest"] or "") is not None, history["feedback_digest"])
    check("policy: load rilegge la v2", learning.policy_load(root=root)["version"] == 2)
    check("policy: senza motivo non si salva", _rejects(lambda: learning.policy_save({}, reason="", root=root)))
    check(
        "policy: prune tiene i corpus citati dalla storia",
        _cited_kept_when_tight(base),
    )


def _cited_kept_when_tight(base: Path) -> bool:
    root = base / "learning-cited"
    thumbs = base / "cited-thumbs"
    thumbs.mkdir(exist_ok=True)
    for index in range(4):
        stamp = (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=index)).isoformat()
        learning.store_failure(
            f"cit-{index:04d}",
            {"prompt": "x", "pad": "q" * 400},
            {"outcome": "failed"},
            [make_thumb(thumbs / f"cit{index}_{n}.jpg", 320, 180) for n in range(3)],
            root=root,
            captured_utc=stamp,
        )
    learning.policy_save(
        {"thresholds": {"variety_min": 0.7}, "cited_jobs": ["cit-0001"]},
        reason="il piano di cit-0001 spiega il calo",
        feedback_ids=["fb-9"],
        root=root,
    )
    learning.prune(root=root, budget_mb=0.1)
    return (root / "corpus" / "cit-0001").is_dir() and (root / "corpus" / "cit-0003").is_dir()


# --------------------------------------------------------------------------- #
# niente rete, niente video nel modulo
# --------------------------------------------------------------------------- #


def test_module_hygiene() -> None:
    source = MODULE.read_text(encoding="utf-8")
    imported = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    network = sorted(
        name for name in imported if any(name == bad or name.startswith(bad + ".") for bad in NETWORK)
    )
    check("nessun import di rete (AST)", not network, network)
    check("nessun riferimento a socket/urllib/http", not re.search(r"\b(socket|urllib|http\.client)\b", source))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vx-learning-") as tmp:
        base = Path(tmp)
        test_events(base)
        test_concurrent_append(base)
        test_corpus(base)
        test_status_and_prune(base)
        test_archive(base)
        test_policy(base)
    test_module_hygiene()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
