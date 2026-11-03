"""vx.guard — audit of a work folder: did the job really pass through vx?

This module exists because a job can be born with ``vx ingest`` and then drift
away: ffmpeg typed by hand, a bespoke pipeline script, a custom UI. The audit is
read-only and only looks at evidence on disk — it never runs anything it finds.

The six voices (each finding carries the file that justifies it):

    G1  firma vx              source/report/plan/progress.json, stage_*/, proxy/, out/
    G2  master fuori da vx    *.mp4 in root or out/ without report.json
    G3  UI artigianale        index.html + serve.py (or http.server) in the same folder
    G4  pipeline artigianale  *.py that mentions ffmpeg and subprocess/os.system
    G5  progresso pubblicato  progress.json missing on a vx job (warning, not a fault)
    G6  sorgente modificato   source.json sha256 no longer matches the file

Exit law: 0 = compliant, 2 = at least one violation. Warnings never change the
exit code — old jobs simply have no ``progress.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .contract import EXIT_OK, EXIT_USAGE, VxError

__all__ = ["audit", "to_text", "CODES"]

#: The four files that say "this folder was born inside vx".
SIGNATURE_FILES = ("source.json", "report.json", "plan.json", "progress.json")
#: The two folders that say the same, at the top of the job (as `vx serve` reads it).
SIGNATURE_DIRS = ("proxy", "out")
#: ``stage_*/`` (stage_00, stage_01 ...) is the staging convention of the skill.
SIGNATURE_GLOBS = ("stage_*",)

#: What makes an ``out/`` master legitimate: one of these exists at job root.
REPORT_MARKERS = ("report.json", "deliverables.json")

#: Evidence of a publishable master (only the formats the pipeline delivers).
MASTER_SUFFIXES = (".mp4",)

#: Directories walked by the evidence scans but never part of a job's own code.
SKIP_DIRS = {".git", "__pycache__", "node_modules"}

MAX_EVIDENCE = 6

CODES = {
    "G1": "firma vx",
    "G2": "master fuori da vx",
    "G3": "UI scritta a mano",
    "G4": "pipeline scritta a mano",
    "G5": "progresso non pubblicato",
    "G6": "sorgente modificato",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _walk(root: Path) -> Iterable[Path]:
    """Every file under ``root``, skipping the folders no job writes by hand."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _signature(root: Path) -> List[str]:
    """The vx marks at the top of the folder: what `vx serve` would call a job."""
    found = [name for name in SIGNATURE_FILES if (root / name).is_file()]
    found += [name for name in SIGNATURE_DIRS if (root / name).is_dir()]
    for pattern in SIGNATURE_GLOBS:
        found += sorted(p.name for p in root.glob(pattern) if p.is_dir())
    return found


def _masters(root: Path) -> List[Path]:
    """Candidate deliveries: ``out/*.mp4`` and anything loose at the root."""
    found: List[Path] = []
    for folder in (root, root / "out"):
        if not folder.is_dir():
            continue
        for suffix in MASTER_SUFFIXES:
            found += [p for p in sorted(folder.glob(f"*{suffix}")) if p.is_file()]
    return found


def _text(path: Path, limit: int = 256 * 1024) -> str:
    """Best-effort read of a script: only used to look for words, never to run it."""
    try:
        return path.read_bytes()[:limit].decode("utf-8", "ignore")
    except OSError:
        return ""


def _hash_file(path: Path) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _check(
    code: str,
    status: str,
    detail: str,
    files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "label": CODES[code],
        "status": status,  # ok | violation | warning | na
        "detail": detail,
        "files": list(files or []),
    }


# --------------------------------------------------------------------------- #
# the six voices
# --------------------------------------------------------------------------- #


def _check_signature(root: Path) -> Dict[str, Any]:
    marks = _signature(root)
    if marks:
        return _check("G1", "ok", "firma vx: " + ", ".join(marks), marks)
    loose = [p.name for p in _masters(root)]
    detail = "nessuna firma vx in questa cartella"
    if loose:
        detail += ": c'e' un video sciolto (" + ", ".join(loose) + ")"
    return _check("G1", "violation", "non e' un lavoro vx — " + detail, loose)


def _check_master(root: Path, job: bool) -> Dict[str, Any]:
    if not job:
        return _check("G2", "na", "non applicabile: la cartella non e' un lavoro vx")
    masters = _masters(root)
    if not masters:
        return _check("G2", "ok", "nessun master fuori dalla pipeline")
    markers = [name for name in REPORT_MARKERS if (root / name).is_file()]
    rel = [_rel(root, p) for p in masters]
    if markers:
        return _check(
            "G2", "ok", f"master tracciato da {', '.join(markers)}: {', '.join(rel)}", rel
        )
    return _check(
        "G2",
        "violation",
        "master prodotto fuori dalla pipeline — "
        f"{', '.join(rel)} senza report.json ne' deliverables.json",
        rel,
    )


def _check_ui(root: Path) -> Dict[str, Any]:
    html = [p for p in _walk(root) if p.suffix.lower() in (".html", ".htm")]
    if not html:
        return _check("G3", "ok", "nessuna pagina HTML scritta a mano")
    servers = {
        p.parent: p
        for p in _walk(root)
        if p.suffix.lower() == ".py"
        and (p.name == "serve.py" or "http.server" in _text(p))
    }
    paired = [(page, servers[page.parent]) for page in html if page.parent in servers]
    if not paired:
        return _check("G3", "ok", "nessuna UI artigianale (HTML senza server nella stessa cartella)")
    files: List[str] = []
    for page, server in paired:
        files += [_rel(root, page), _rel(root, server)]
    return _check(
        "G3",
        "violation",
        "UI scritta a mano: usa `vx serve` — " + ", ".join(files),
        files,
    )


def _check_pipeline(root: Path) -> Dict[str, Any]:
    handmade: List[str] = []
    for path in _walk(root):
        if path.suffix.lower() != ".py":
            continue
        text = _text(path).lower()
        if "ffmpeg" in text and ("subprocess" in text or "os.system" in text):
            handmade.append(_rel(root, path))
    if not handmade:
        return _check("G4", "ok", "nessuno script che chiama ffmpeg a mano")
    shown = handmade[:MAX_EVIDENCE]
    extra = len(handmade) - len(shown)
    detail = "pipeline scritta a mano — " + ", ".join(shown)
    if extra > 0:
        detail += f" (+{extra})"
    return _check("G4", "violation", detail, shown)


def _check_progress(root: Path, job: bool) -> Dict[str, Any]:
    if not job:
        return _check("G5", "na", "non applicabile: la cartella non e' un lavoro vx")
    if (root / "progress.json").is_file():
        return _check("G5", "ok", "progress.json pubblicato", ["progress.json"])
    return _check(
        "G5",
        "warning",
        "progress.json assente: la UI di `vx serve` non puo' raccontare il lavoro",
        [],
    )


def _check_source(root: Path) -> Dict[str, Any]:
    manifest = root / "source.json"
    if not manifest.is_file():
        return _check("G6", "na", "nessun source.json da verificare")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _check(
            "G6", "violation", f"source.json illeggibile ({exc}): sorgente non verificabile"
        )
    if not isinstance(data, dict):
        return _check(
            "G6", "violation", "source.json non e' un oggetto: sorgente non verificabile"
        )
    declared = str(data.get("sha256") or "").strip().lower()
    source = str(data.get("path") or "").strip()
    if not declared:
        return _check(
            "G6", "warning", "source.json senza sha256: l'immutabilita' non e' dichiarata"
        )
    if not source:
        return _check(
            "G6", "violation", "source.json senza path: il sorgente non e' verificabile"
        )
    path = Path(source)
    if not path.is_file():
        return _check(
            "G6", "violation", f"sorgente non trovato: {source}", ["source.json"]
        )
    actual = _hash_file(path)
    if actual is None:
        return _check(
            "G6", "violation", f"sorgente illeggibile: {source}", ["source.json"]
        )
    if actual != declared:
        return _check(
            "G6",
            "violation",
            f"sorgente modificato: sha256 atteso {declared[:12]}…, trovato {actual[:12]}… "
            f"({source})",
            ["source.json", _rel(root, path)],
        )
    return _check("G6", "ok", f"sorgente intatto (sha256 {actual[:12]}…)", ["source.json"])


# --------------------------------------------------------------------------- #
# audit + rendering
# --------------------------------------------------------------------------- #


def audit(directory: str | Path) -> Dict[str, Any]:
    """Read-only audit of one work folder. Returns the report, never prints."""
    root = Path(directory)
    if not root.is_dir():
        raise VxError(f"cartella inesistente: {root}")

    marks = _signature(root)
    job = bool(marks)
    checks = [
        _check_signature(root),
        _check_master(root, job),
        _check_ui(root),
        _check_pipeline(root),
        _check_progress(root, job),
        _check_source(root),
    ]
    violations = [c for c in checks if c["status"] == "violation"]
    warnings = [c for c in checks if c["status"] == "warning"]
    return {
        "dir": str(root.resolve()),
        "job": job,
        "signature": marks,
        "conforme": not violations,
        "ok": len(violations) == 0,
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
        "exit_code": EXIT_OK if not violations else EXIT_USAGE,
    }


def to_text(report: Dict[str, Any]) -> str:
    """Readable Italian audit: one ``[ok]``/``[!]`` line per voice, plus warnings."""
    lines: List[str] = [
        f"VX guard  {report.get('dir')}",
        "esito     "
        + ("CONFORME" if report.get("conforme") else "NON CONFORME")
        + ("   (lavoro vx)" if report.get("job") else "   (non e' un lavoro vx)"),
    ]
    marks = {"ok": "[ok]", "violation": "[!]", "warning": "[!]", "na": "[--]"}
    for check in report.get("checks") or []:
        mark = marks.get(str(check.get("status")), "[--]")
        lines.append(
            f"{mark} {check.get('code')} {str(check.get('label')):<26} {check.get('detail')}"
        )
    violations = len(report.get("violations") or [])
    warnings = len(report.get("warnings") or [])
    lines.append(
        f"violazioni {violations}   avvisi {warnings}   "
        f"exit {report.get('exit_code')}"
    )
    return "\n".join(lines)
