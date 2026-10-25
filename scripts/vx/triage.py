"""vx.triage — the 3-question briefing when the brief is incomplete.

Exactly three questions, always the same ids, always in Italian. The briefing is
what the skill prints instead of guessing: format, hook, subtitles.

stdlib + ``vx.contract`` + ``vx.report`` only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import report as report_mod
from .contract import (
    ASPECTS,
    EXIT_NEEDS_INPUT,
    EXIT_OK,
    EXIT_USAGE,
    TRIAGE_SCHEMA,
)

__all__ = ["build_briefing", "to_markdown", "main"]

PYTHON = sys.executable
SCRIPTS = Path(__file__).resolve().parent.parent

_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}

_QUESTIONS: List[Dict[str, Any]] = [
    {
        "id": "target_format",
        "question": "In che formato esce il video?",
        "options": [
            "9:16 verticale",
            "16:9 orizzontale",
            "1:1 quadrato",
            "4:5 verticale stretto",
        ],
        "default": "9:16",
        "why": "decide il crop, la safe area e la larghezza dei sottotitoli",
    },
    {
        "id": "hook",
        "question": "Cosa metto nei primi 3 secondi?",
        "options": ["testo", "auto", "nessuno"],
        "default": "auto",
        "why": "senza aggancio il video perde spettatori subito",
    },
    {
        "id": "subtitles",
        "question": "Che sottotitoli vuoi?",
        "options": ["karaoke", "clean", "nessuno"],
        "default": "karaoke",
        "why": "cambia lo stile e la densità delle cue",
    },
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _answered(brief: dict, key: str) -> bool:
    value = brief.get(key)
    if not isinstance(value, str) or not value.strip():
        return False
    if key == "format":
        return value.strip() in ASPECTS
    return True


def _complete(brief: dict) -> bool:
    return all(_answered(brief, key) for key in ("format", "hook", "subtitles"))


def _collect_findings(report: dict) -> List[Dict[str, Any]]:
    """Existing findings refreshed by a fresh detection pass, deduped by id."""
    existing = [
        item
        for item in (report.get("findings") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    try:
        from .plan import detect_findings

        detected = detect_findings(report)
    except Exception:  # pragma: no cover - detection must never block triage
        detected = []

    by_id: Dict[str, Dict[str, Any]] = {item["id"]: item for item in existing}
    for item in detected:
        by_id[item["id"]] = item
    return list(by_id.values())


def _headline(ranked: List[Dict[str, Any]]) -> str:
    parts = [item.get("evidence") for item in ranked[:3] if item.get("evidence")]
    if not parts:
        return "Nessun problema rilevante: il materiale è pronto per il montaggio."
    return "Da mettere a posto prima del montaggio: " + "; ".join(parts) + "."


def _resume_command(report: dict, brief: dict) -> str:
    source = report.get("source") or {}
    report_path = str(report.get("_path") or source.get("path") or "report.json")
    brief_path = str(brief.get("_path") or "brief.json")
    answers_path = str(brief.get("_answers_path") or "answers.json")
    return (
        f'"{PYTHON}" "{SCRIPTS / "vx.py"}" plan '
        f'--report "{report_path}" --brief "{brief_path}" '
        f'--answers "{answers_path}" --out plan.json'
    )


# --------------------------------------------------------------------------- #
# briefing
# --------------------------------------------------------------------------- #


def build_briefing(report: dict, brief: dict) -> Dict[str, Any]:
    """The 3-question request the skill prints when the brief is incomplete."""
    report = report if isinstance(report, dict) else {}
    brief = brief if isinstance(brief, dict) else {}
    source = report.get("source") or {}

    findings = _collect_findings(report)
    ranked = sorted(
        findings,
        key=lambda item: (
            _SEVERITY_RANK.get(item.get("severity"), 3),
            str(item.get("id")),
        ),
    )
    detected = [
        {
            "severity": item.get("severity") or "info",
            "id": item.get("id"),
            "text": item.get("evidence") or "",
        }
        for item in ranked[:5]
    ]
    QUESTION_ID_FOR_BRIEF_KEY = {
        "format": "target_format",
        "hook": "hook",
        "subtitles": "subtitles",
    }
    missing = [
        QUESTION_ID_FOR_BRIEF_KEY[key]
        for key in QUESTION_ID_FOR_BRIEF_KEY
        if not _answered(brief, key)
    ]

    return {
        "schema": TRIAGE_SCHEMA,
        "status": "needs_user_input",
        "source": {
            key: source.get(key)
            for key in ("path", "duration", "fps", "width", "height", "has_audio")
        },
        "verdict_headline": _headline(ranked),
        "detected": detected,
        "missing": missing,
        "questions": [dict(question) for question in _QUESTIONS],
        "assumptions_if_no_answer": {
            "target_format": "9:16",
            "hook": "auto",
            "subtitles": "karaoke",
            "loudness": -14.0,
        },
        "resume_command": _resume_command(report, brief),
    }


def to_markdown(req: dict, *, max_lines: int = 25) -> str:
    """Senior-editor briefing, Italian, never longer than ``max_lines``."""
    req = req if isinstance(req, dict) else {}
    source = req.get("source") or {}
    name = Path(str(source.get("path") or "video")).name

    lines: List[str] = [f"# Briefing — {name}", ""]
    lines.append(str(req.get("verdict_headline") or ""))
    lines.append("")

    lines.append("## I problemi che contano")
    detected = req.get("detected") or []
    if detected:
        for index, item in enumerate(detected[:3], 1):
            lines.append(f"{index}. {item.get('text')}")
    else:
        lines.append("1. Niente di grave: il girato regge così com'è.")
    lines.append("")

    lines.append("## Mi servono 3 risposte")
    for index, question in enumerate((req.get("questions") or [])[:3], 1):
        options = " / ".join(question.get("options") or [])
        lines.append(
            f"{index}. {question.get('id')}: {question.get('question')} "
            f"({options}) — default {question.get('default')}"
        )
    lines.append("")

    lines.append("## Cosa non faccio senza conferma")
    lines.append(
        "Non taglio silenzi, non rifaccio il crop e non tocco il colore "
        "finché non rispondi."
    )

    return "\n".join(lines[:max_lines])


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def _load_json(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="vx.triage", description="Ask the 3 questions the brief is missing."
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--brief")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        rep = report_mod.read(args.report)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rep["_path"] = str(args.report)

    try:
        brief = _load_json(args.brief) if args.brief else {}
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if _complete(brief):
        print("Brief completo: nessuna domanda da fare.")
        return EXIT_OK

    request = build_briefing(rep, brief)
    if args.json and not args.markdown:
        print(json.dumps(request, indent=2, ensure_ascii=False))
    else:
        print(to_markdown(request))
    return EXIT_NEEDS_INPUT


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
