"""I quattro difetti verificati di vx.plan: loudness, gate, esposizione, consegna.

python tests/test_plan_fixes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import plan  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(f"    [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def _report(**analysis: dict) -> dict:
    report: dict = {"source": {"width": 1080, "height": 1920, "duration": 12.0}}
    if analysis:
        report["analysis"] = analysis
    return report


def _brief(**extra: object) -> dict:
    brief: dict = {"format": "9:16", "hook": "none", "subtitles": "auto"}
    brief.update(extra)
    return brief


def _treatment(built, name: str):
    return next((t for t in built.treatments if t.get("name") == name), None)


def _finding_ids(report: dict) -> list:
    return [f.get("id") for f in report.get("findings") or [] if isinstance(f, dict)]


def main() -> int:
    # -- 1. loudness writer: lowercase keys, real target -------------------- #
    apple = plan.build_plan(_report(), _brief(loudness="apple"))
    check(
        "apple: loudness in minuscolo i/tp/lra",
        apple.output.get("loudness") == {"i": -16.0, "tp": -1.0, "lra": 11.0},
        apple.output.get("loudness"),
    )
    check(
        "apple: loudness_target conservato",
        apple.output.get("loudness_target") == "apple",
        apple.output.get("loudness_target"),
    )
    broadcast = plan.build_plan(_report(), _brief(loudness="broadcast"))
    check(
        "broadcast: -23.0 / -2.0",
        broadcast.output.get("loudness") == {"i": -23.0, "tp": -2.0, "lra": 11.0},
        broadcast.output.get("loudness"),
    )

    # -- 2. loudness finding compares against the brief's target ----------- #
    off = _report(audio={"loudness": {"input_i": -20.0}})
    plan.build_plan(off, _brief(loudness="apple"))
    check(
        "apple -20 LUFS: finding loudness",
        "audio.loudness.off" in _finding_ids(off),
        _finding_ids(off),
    )
    finding = next(
        (f for f in off.get("findings") or [] if f.get("id") == "audio.loudness.off"),
        {},
    )
    check(
        "apple: soglia -16.0, non -14.0",
        finding.get("threshold") == -16.0,
        finding.get("threshold"),
    )
    check(
        "apple: messaggio col target reale",
        "-16.0" in str(finding.get("evidence") or ""),
        finding.get("evidence"),
    )

    on_target = _report(audio={"loudness": {"input_i": -16.0}})
    plan.build_plan(on_target, _brief(loudness="apple"))
    check(
        "apple -16 LUFS: nessun finding",
        "audio.loudness.off" not in _finding_ids(on_target),
        _finding_ids(on_target),
    )

    # -- 3. exposure veto: audio noise floor says nothing about the sensor -- #
    under = _report(
        video={"exposure": {"mean_luma": 20.0}},
        audio={"noise_floor_dbfs": 0.0},
    )
    built = plan.build_plan(under, _brief())
    exposure = _treatment(built, "exposure_fix")
    check("esposizione sotto: trattamento presente", exposure is not None)
    check(
        "rumore audio 0.0 non veta l'esposizione",
        bool(exposure and exposure.get("enabled")),
        (exposure or {}).get("enabled"),
    )
    explain = str(((exposure or {}).get("reason") or {}).get("explain") or "")
    check("explain porta [UNVER]", "[UNVER]" in explain, explain)

    # -- 4. deliverables: four intents, relative out/ paths ----------------- #
    speech = _report(speech={"word_count": 40, "wpm": 120})
    built_speech = plan.build_plan(speech, _brief())
    items = plan.deliverables(built_speech)
    names = [item.get("name") for item in items]
    check(
        "quattro consegne esatte",
        names == ["master.mp4", "subtitles.srt", "thumbnail.jpg", "contact_sheet.jpg"],
        names,
    )
    check(
        "ogni path sotto out/",
        all(str(item.get("path") or "").startswith("out/") for item in items),
        [item.get("path") for item in items],
    )
    check(
        "nessun export per piattaforma",
        not any(item.get("kind") == "platform" for item in items),
        [item.get("kind") for item in items],
    )
    check(
        "master tiene le misure del piano",
        items[0].get("width") == built_speech.output.get("width")
        and items[0].get("height") == built_speech.output.get("height"),
        (items[0].get("width"), items[0].get("height")),
    )
    subs = next(item for item in items if item.get("name") == "subtitles.srt")
    check("sottotitoli disponibili col parlato", subs.get("available") is True)

    silent = plan.build_plan(_report(), _brief())
    subs_silent = next(
        item
        for item in plan.deliverables(silent)
        if item.get("name") == "subtitles.srt"
    )
    check("senza parlato: available false", subs_silent.get("available") is False)
    check(
        "senza parlato: reason no speech",
        subs_silent.get("reason") == "no speech",
        subs_silent.get("reason"),
    )

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
