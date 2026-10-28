"""F28-T04 gate -- any catalogue family is fetched on demand, licence first, bytes pinned.

Run offline (the default; no socket is opened)::

    python tests/test_fontfetch.py

Exercises, without the internet:
  a. catalogue integrity: 2,065 families, every row labelled, exactly the 15
     unlicenced ones refused with FONT_LICENCE_MISSING before any request;
  b. a family neither bundled nor cached is refused with FONT_OFFLINE, naming
     the family and the cache path, and the offline path makes zero requests;
  c. a bundled family returns its record with the network never consulted;
  d. a fake family injected into a temp copy of the catalogue goes the whole
     way against a local HTTP server: download, magic check, sha256, cache
     file, fetched.json row, record(), check();
  e. a payload with the wrong magic is refused and writes nothing;
  f. a cached file that no longer hashes to the recorded sha256 is refused.

One real network check, gated: ``VX_NET=1`` fetches ``abrilfatface`` into the
real ``assets/fonts`` cache and prints its fetched.json record.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import fontfetch  # noqa: E402
from vx.edl3 import EDLError  # noqa: E402

PASS = 0
FAIL = 0

FONTS = ROOT / "assets" / "fonts"
CATALOGUE = FONTS / "catalogue.json"
DEAD = "http://127.0.0.1:1"
TTF = b"\x00\x01\x00\x00" + b"\x00" * 4096 + b"vx fake font payload"
NOT_A_FONT = b"NOTAFONT" + b"\x00" * 128

FAKE = {
    "family": "vxfake",
    "directory": "ofl/vxfake",
    "licence": "OFL-1.1",
    "licenced": True,
    "files": ["VxFake-Regular.ttf"],
}
CORRUPT = {
    "family": "vxcorrupt",
    "directory": "ofl/vxcorrupt",
    "licence": "OFL-1.1",
    "licenced": True,
    "files": ["VxCorrupt-Regular.ttf"],
}


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL':4} {label:66} {detail}")


def code_of(call) -> str:
    try:
        call()
    except EDLError as exc:
        return str(exc.code)
    except Exception as exc:  # a non-refusal is a bug, and it must be visible
        return f"{type(exc).__name__}: {exc}"
    return ""


@contextlib.contextmanager
def serve(root: Path) -> Iterator[Tuple[str, Dict[str, int]]]:
    """A local HTTP server over ``root``, counting every GET it answers."""
    counter = {"hits": 0}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def do_GET(self) -> None:  # noqa: N802 -- stdlib name
            counter["hits"] += 1
            super().do_GET()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", counter
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fake_workspace(work: Path) -> Tuple[Path, Path]:
    """A temp catalogue (copy + two injected families) and the served repo."""
    repo = work / "repo"
    (repo / "ofl" / "vxfake").mkdir(parents=True)
    (repo / "ofl" / "vxcorrupt").mkdir(parents=True)
    (repo / "ofl" / "vxfake" / "VxFake-Regular.ttf").write_bytes(TTF)
    (repo / "ofl" / "vxcorrupt" / "VxCorrupt-Regular.ttf").write_bytes(NOT_A_FONT)
    document = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    document["families"].extend([dict(FAKE), dict(CORRUPT)])
    catalogue = work / "catalogue.json"
    catalogue.write_text(json.dumps(document), encoding="utf-8")
    return catalogue, repo


def run_catalogue(work: Path) -> None:
    document = fontfetch.catalogue()
    rows = document["families"]
    check("catalogue declares 2,065 families", len(rows) == 2065, len(rows))
    labels = {str(row.get("licence") or "").strip() for row in rows}
    check(
        "every row carries a licence label",
        all(str(row.get("licence") or "").strip() for row in rows),
        sorted(labels),
    )
    check(
        "the labels are the three the catalogue records",
        labels == {"OFL-1.1", "Apache-2.0", "Ubuntu-1.0"},
        sorted(labels),
    )
    check(
        "every row names a directory and at least one file",
        all(row.get("directory") and row.get("files") for row in rows),
    )
    unlicenced = [row["family"] for row in rows if row.get("licenced") is not True]
    check("exactly 15 families are marked unlicenced", len(unlicenced) == 15, unlicenced)

    dest = work / "unlicenced"
    with serve(work / "repo") as (base, counter):
        codes = [
            code_of(
                lambda family=family: fontfetch.fetch(
                    family,
                    dest=dest,
                    allow_network=True,
                    base_url=base,
                )
            )
            for family in unlicenced
        ]
        hits = counter["hits"]
    check(
        "all 15 unlicenced families refuse with FONT_LICENCE_MISSING",
        codes and set(codes) == {"FONT_LICENCE_MISSING"},
        sorted(set(codes)),
    )
    check("the licence refusal happens before any download", hits == 0, f"{hits} GET")
    check("no cache row was written for an unlicenced family", fontfetch.fetched_rows(dest) == [])


def run_lookup(work: Path) -> None:
    spaced = fontfetch.find("Abril Fatface")
    check("find() matches 'Abril Fatface'", spaced is not None and spaced["family"] == "abrilfatface")
    check("find() ignores case", fontfetch.find("ABRILFATFACE") == spaced)
    check("find() normalises underscores", fontfetch.find("abril_fatface") == spaced)
    check("find() returns None for an unknown family", fontfetch.find("no such family") is None)
    check("find() returns None for an empty query", fontfetch.find("   ") is None)


def run_offline_and_bundled(work: Path) -> None:
    dest = work / "offline"
    code = code_of(
        lambda: fontfetch.fetch("abrilfatface", dest=dest, allow_network=False, base_url=DEAD)
    )
    check("a family that is neither bundled nor cached refuses with FONT_OFFLINE", code == "FONT_OFFLINE", code)
    try:
        fontfetch.fetch("abrilfatface", dest=dest, allow_network=False, base_url=DEAD)
    except EDLError as exc:
        message = str(exc)
    else:
        message = ""
    check("the offline refusal names the family", "abrilfatface" in message, message[:80])
    check(
        "the offline refusal names the cache path",
        str(dest / "cache" / "abrilfatface") in message,
        message[-70:],
    )
    check("the offline path wrote no cache file", not (dest / "cache").exists())

    bundled = fontfetch.fetch("Inter", dest=FONTS, allow_network=False, base_url=DEAD)
    check("a bundled family returns its record", bundled["status"] == "bundled", bundled["status"])
    check("the bundled record carries a sha256 per file", all(len(f["sha256"]) == 64 for f in bundled["files"]), len(bundled["files"]))
    check("the bundled record carries the OFL licence", bundled["licence"] == "OFL-1.1", bundled["licence"])
    check(
        "the bundled lookup adds no fetched.json row",
        fontfetch.find("inter") is not None and all(
            row["family"] != "inter" for row in fontfetch.fetched_rows(FONTS)
        ),
    )

    unknown = code_of(
        lambda: fontfetch.fetch("definitely not a real family", dest=work / "x", allow_network=False)
    )
    check("an unknown family refuses with FONT_UNKNOWN_FAMILY", unknown == "FONT_UNKNOWN_FAMILY", unknown)


def run_fake_family(work: Path) -> Dict[str, Any]:
    catalogue, repo = fake_workspace(work)
    dest = work / "cache-root"
    with serve(repo) as (base, counter):
        record = fontfetch.fetch(
            "vx fake", dest=dest, allow_network=True, catalogue_path=catalogue, base_url=base
        )
        hits = counter["hits"]
    check("the fake family is fetched from the local server", record["status"] == "cached", record["status"])
    check("exactly one GET served the download", hits == 1, hits)

    cached = dest / "cache" / "vxfake" / "VxFake-Regular.ttf"
    check("the bytes are written under cache/<family>/", cached.is_file())
    digest = hashlib.sha256(TTF).hexdigest()
    check("the cached bytes are the served bytes", cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == digest, digest[:16])
    check("the cached record carries that sha256", record["files"][0]["sha256"] == digest, record["files"][0]["sha256"][:16])
    check("the cached record carries the byte count", record["files"][0]["bytes"] == len(TTF), record["files"][0]["bytes"])

    rows = fontfetch.fetched_rows(dest)
    row = next((item for item in rows if item["family"] == "vxfake"), {})
    check("fetched.json has exactly one row", len(rows) == 1, len(rows))
    check(
        "the row carries family/licence/directory/files/fetched_utc/source",
        set(row) == {"family", "licence", "directory", "files", "fetched_utc", "source"},
        sorted(row),
    )
    check("the row pins the sha256", row.get("files", [{}])[0].get("sha256") == digest)
    check("the row records the OFL licence", row.get("licence") == "OFL-1.1", row.get("licence"))
    check("the row records where the bytes came from", str(row.get("source", "")).startswith("http://127.0.0.1:"), row.get("source"))

    rec = fontfetch.record("vx_fake", dest=dest, catalogue_path=catalogue)
    check("record() merges the cached family", rec["status"] == "cached" and rec["family"] == "vxfake", rec["status"])
    check("record() exposes the licence trail", rec["licence"] == "OFL-1.1", rec["licence"])

    report = {"render": {"frames_written": 1}}
    fontfetch.annotate(report, [str(cached)], dest=dest, catalogue_path=catalogue)
    entry = (report.get("render", {}).get("fonts") or [{}])[0]
    check(
        "the render report carries {family, licence, source, sha256}",
        {"family", "licence", "source", "sha256"} <= set(entry),
        sorted(entry),
    )
    check("the report entry names the fetched family", entry.get("family") == "vxfake", entry.get("family"))
    check("the report entry pins the fetched bytes", entry.get("sha256") == digest)

    report_bundled = {"render": {}}
    fontfetch.annotate(report_bundled, [str(FONTS / "Antonio-Regular.ttf")], dest=FONTS)
    bundled_entry = (report_bundled["render"].get("fonts") or [{}])[0]
    check(
        "a bundled family annotates its own record",
        bundled_entry.get("family", "").lower() == "antonio"
        and bundled_entry.get("source") == "bundled"
        and len(str(bundled_entry.get("sha256") or "")) == 64,
        sorted(bundled_entry),
    )

    consistent = fontfetch.check(dest)
    check("check() accepts the intact cache", consistent.get("ok") is True and consistent.get("families") == 1, consistent)

    cached_record = record
    cached_path = cached
    return {"dest": dest, "base": base, "catalogue": catalogue, "record": cached_record, "path": cached_path}


def run_cached_path_offline(state: Dict[str, Any]) -> None:
    again = fontfetch.fetch(
        "vxfake",
        dest=state["dest"],
        allow_network=False,
        catalogue_path=state["catalogue"],
        base_url=state["base"],
    )
    check("a cached family is returned with the network off", again["status"] == "cached", again["status"])
    check(
        "the cached family keeps its pinned sha256",
        again["files"][0]["sha256"] == state["record"]["files"][0]["sha256"],
    )
    check(
        "nothing outside the temp dest was written",
        all(row["family"] != "vxfake" for row in fontfetch.fetched_rows(FONTS)),
    )


def run_corrupt_payload(work: Path) -> None:
    catalogue = work / "catalogue.json"
    dest = work / "corrupt-root"
    with serve(work / "repo") as (base, counter):
        code = code_of(
            lambda: fontfetch.fetch(
                "vxcorrupt", dest=dest, allow_network=True, catalogue_path=catalogue, base_url=base
            )
        )
        hits = counter["hits"]
    check("a payload with the wrong magic refuses with FONT_BAD_MAGIC", code == "FONT_BAD_MAGIC", code)
    check("the bad payload was downloaded once, then refused", hits == 1, hits)
    check("the refused payload left no cache file", not (dest / "cache" / "vxcorrupt").exists())
    check("the refused payload left no fetched.json row", fontfetch.fetched_rows(dest) == [])


def run_sha_drift(state: Dict[str, Any]) -> None:
    state["path"].write_bytes(TTF[:-1] + b"X")
    drifted = code_of(
        lambda: fontfetch.fetch(
            "vxfake",
            dest=state["dest"],
            allow_network=False,
            catalogue_path=state["catalogue"],
            base_url=DEAD,
        )
    )
    check("a cached file whose sha256 drifted is refused", drifted == "FONT_SHA_MISMATCH", drifted)
    checked = code_of(lambda: fontfetch.check(state["dest"]))
    check("check() refuses the drifted cache", checked == "FONT_SHA_MISMATCH", checked)


def run_import_silence(work: Path) -> None:
    script = (
        "import sys, vx.fontfetch; "
        "print(' '.join(str(int(name in sys.modules)) for name in "
        "('urllib.request', 'vx.fontapis', 'vx.edl3')))"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(ROOT),
    )
    flags = (proc.stdout or "").strip()
    check(
        "importing the module pulls no urllib/network/edl3",
        proc.returncode == 0 and flags == "0 0 0",
        flags or proc.stderr[-120:],
    )


def run_network_gate() -> None:
    if os.environ.get("VX_NET") != "1":
        print()
        print("NETWORK PATH WAS NOT EXERCISED: VX_NET IS NOT SET.")
        print("  run `VX_NET=1` (PowerShell: $env:VX_NET='1') to fetch abrilfatface.")
        return
    print()
    print("network gate: fetching abrilfatface from github.com/google/fonts ...")
    record = fontfetch.fetch("abrilfatface", allow_network=True)
    row = next(
        (item for item in fontfetch.fetched_rows(FONTS) if item["family"] == "abrilfatface"),
        {},
    )
    print("fetched.json record:")
    print(json.dumps(row, indent=2))
    check("the live fetch cached the family", record["status"] == "cached", record["status"])
    check("the live fetch has a fetched.json row", bool(row), sorted(row))
    check("the live row pins a 64-hex sha256", len(str((row.get("files") or [{}])[0].get("sha256"))) == 64)
    check("the live row is OFL", row.get("licence") == "OFL-1.1", row.get("licence"))
    check("check() accepts the live cache", fontfetch.check(FONTS).get("ok") is True)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vx_fontfetch_"))
    try:
        run_catalogue(work)
        run_lookup(work)
        run_offline_and_bundled(work)
        state = run_fake_family(work)
        run_cached_path_offline(state)
        run_corrupt_payload(work)
        run_sha_drift(state)
        run_import_silence(work)
        run_network_gate()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print()
    print(f"PASS {PASS}  FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
