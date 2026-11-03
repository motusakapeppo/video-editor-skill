"""Test di ``vx.verify``: le falsificazioni eseguite, non raccontate.

Due master sintetici costruiti con ffmpeg (nessuna rete, nessun asset esterno):

* ``good.mp4`` — fondo scuro, titolo bianco, audio normalizzato a -14 LUFS;
* ``bad.mp4``  — 0,5 s di nero in apertura e titolo grigio su fondo grigio.

Il primo deve passare ``verify_all``, il secondo deve essere bocciato con una
misura numerica (contrasto e luma minima), non con una frase. Ogni check ha il
suo caso pass e il suo caso fail; una voce senza check automatico resta
``verified: false``. Il test e' un programma autonomo come gli altri della
cartella.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vx import decisions, ffmpeg as ff, verify  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += 1 if ok else 0
    FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:52} {detail}")


def _filter_path(path: Path) -> str:
    """Percorso per un filtro ffmpeg: slash in avanti e due punti protetti."""
    return str(path).replace("\\", "/").replace(":", "\\:")


def _build_good(path: Path, font: Path) -> None:
    graph = (
        f"[0:v]drawtext=fontfile='{_filter_path(font)}':text='HOOK':"
        "fontcolor=white:fontsize=54:x=(w-tw)/2:y=24[v];"
        "[1:a]loudnorm=I=-14:TP=-1:LRA=11[a]"
    )
    subprocess.run(
        [
            ff.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x101010:s=320x180:d=2:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _build_bad(path: Path, font: Path) -> None:
    graph = (
        "[0:v][1:v]concat=n=2:v=1:a=0,"
        f"drawtext=fontfile='{_filter_path(font)}':text='HOOK':"
        "fontcolor=0x5a5a5a:fontsize=54:x=(w-tw)/2:y=24:"
        "enable='gte(t,0.5)'[v];"
        "[2:a]loudnorm=I=-14:TP=-1:LRA=11[a]"
    )
    subprocess.run(
        [
            ff.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.5:r=30",
            "-f", "lavfi", "-i", "color=c=0x4a4a4a:s=320x180:d=1.5:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _report(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "fps": 30.0,
        "timeline_sec": 2.0,
        "loudness_target": {"lufs": -14.0, "true_peak_db": -1.0},
        "contrast_at": 1.0,
        "coherence": {
            "metrics": {"variety": 0.75, "distinct_shots": 3, "shots": 4}
        },
        "text_required": True,
        "texts": ["HOOK"],
    }
    base.update(overrides)
    return base


def _decision_log() -> list[Dict[str, Any]]:
    registry = decisions.Registry()
    registry.add(
        decisions.record(
            id="font",
            question="quale famiglia per il titolo?",
            chosen="Antonio Bold",
            confidence="high",
            evidence=["score 4.2", "licenza OFL"],
            alternatives=[{"option": "Bebas Neue", "why_not": "score 3.1"}],
            falsified_if="il titolo risulta illeggibile nel master",
        )
    )
    registry.add(
        decisions.record(
            id="keyframes",
            question="quali fotogrammi mostrare?",
            chosen="3 fotogrammi approvati",
            confidence="high",
            evidence=["3 fotogrammi ai quartili", "digest abc123"],
            alternatives_note="nessuna alternativa reale",
            falsified_if="un fotogramma non corrisponde al clip dichiarato",
        )
    )
    return registry.as_list()


def _by_id(entries: list[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("id")): item for item in entries}


def main() -> int:
    font = ROOT / "assets" / "fonts" / "Antonio-Bold.ttf"
    if not font.exists():
        print(f"font di prova assente: {font}")
        return 2

    with tempfile.TemporaryDirectory(prefix="vx-verify-") as tmp:
        work = Path(tmp)
        good = work / "good.mp4"
        bad = work / "bad.mp4"
        print("costruzione dei due master sintetici")
        _build_good(good, font)
        _build_bad(bad, font)
        check("good.mp4 esiste", good.is_file() and good.stat().st_size > 0)
        check("bad.mp4 esiste", bad.is_file() and bad.stat().st_size > 0)

        report = _report()
        print("\ncontrast: WCAG del titolo sulla banda alta")
        p = verify.check_contrast(good, report)
        f = verify.check_contrast(bad, report)
        check(
            "contrast pass sul master leggibile",
            p["passed"] and p["measured"]["ratio"] >= verify.CONTRAST_MIN,
            str(p["measured"]),
        )
        check(
            "contrast fail sul titolo grigio su grigio",
            not f["passed"] and f["measured"]["ratio"] < verify.CONTRAST_MIN,
            str(f["measured"]),
        )
        check(
            "il rapporto WCAG bianco/nero e' 21:1",
            round(verify.wcag_ratio(255, 0), 1) == 21.0,
            str(round(verify.wcag_ratio(255, 0), 2)),
        )

        print("\nduration: entro un frame dal richiesto")
        p = verify.check_duration(good, _report(duration_requested_sec=2.0))
        f = verify.check_duration(good, _report(duration_requested_sec=5.0))
        check(
            "duration pass a 2.0 s su 30 fps",
            p["passed"] and p["measured"]["delta_sec"] <= p["measured"]["tolerance_sec"],
            str(p["measured"]),
        )
        check(
            "duration fail a 5.0 s richiesti",
            not f["passed"] and f["measured"]["delta_sec"] > 0.03,
            str(f["measured"]),
        )

        print("\nblack_frames: luma media, campione a 1 fps")
        p = verify.check_black_frames(good, report)
        f = verify.check_black_frames(bad, report)
        check(
            "black_frames pass sul master pieno",
            p["passed"] and p["measured"]["min_luma"] >= verify.BLACK_LUMA,
            str(p["measured"]),
        )
        check(
            "black_frames fail sui 0,5 s neri",
            not f["passed"] and f["measured"]["black_frames"] >= 1,
            str(f["measured"]),
        )

        print("\nloudness: LUFS e true peak contro il target")
        p = verify.check_loudness(good, _report())
        f = verify.check_loudness(good, _report(loudness_target={"lufs": -5.0, "true_peak_db": -1.0}))
        check(
            "loudness pass a -14 LUFS",
            p["passed"] and abs(p["measured"]["lufs"] + 14.0) <= 1.5,
            str(p["measured"]),
        )
        check(
            "loudness fail con target -5 LUFS",
            not f["passed"] and abs(f["measured"]["lufs"] + 5.0) > 1.5,
            str(f["measured"]),
        )

        print("\nvariety: shot distinti / totali dal referto del piano")
        p = verify.check_variety(good, _report())
        f = verify.check_variety(
            good,
            _report(coherence={"metrics": {"variety": 0.25, "distinct_shots": 1, "shots": 4}}),
        )
        check(
            "variety pass a 0.75",
            p["passed"] and p["measured"]["variety"] == 0.75,
            str(p["measured"]),
        )
        check(
            "variety fail a 0.25",
            not f["passed"] and f["measured"]["variety"] == 0.25,
            str(f["measured"]),
        )

        print("\ntext_present: titolo/hook richiesto")
        ass = work / "captions.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,HOOK\n",
            encoding="utf-8",
        )
        p = verify.check_text_present(
            good, _report(text_required=True, texts=[], bridge={"subtitles": str(ass)})
        )
        f = verify.check_text_present(
            good, _report(text_required=True, texts=[], bridge={})
        )
        n = verify.check_text_present(good, _report(text_required=False, texts=[]))
        check(
            "text_present pass leggendo le cue ASS",
            p["passed"] and p["measured"]["text_blocks"] >= 1,
            str(p["measured"]),
        )
        check(
            "text_present fail quando il testo e' richiesto e manca",
            not f["passed"] and f["measured"]["text_blocks"] == 0,
            str(f["measured"]),
        )
        check(
            "text_present pass quando il testo non e' richiesto",
            n["passed"] and n["measured"]["required"] is False,
            str(n["measured"]),
        )

        print("\nverify_all: il verdetto sul master e sul report")
        good_report = _report(decisions=_decision_log())
        good_verdict = verify.verify_all(good_report, good)
        check("verify_all passa il master conforme", good_verdict["ok"] is True)
        check(
            "il verdetto e' scritto in report['verification']",
            good_report.get("verification") is good_verdict
            and len(good_report["verification"]["checks"]) == 6,
            str(len(good_report["verification"]["checks"])),
        )
        bad_report = _report(decisions=_decision_log())
        bad_verdict = verify.verify_all(bad_report, bad)
        failed = [item["id"] for item in bad_verdict["checks"] if not item["passed"]]
        check("verify_all boccia il master non conforme", bad_verdict["ok"] is False)
        check(
            "bocciato da contrast e black_frames",
            set(failed) >= {"contrast", "black_frames"},
            str(failed),
        )
        contrast_measure = next(
            item["measured"] for item in bad_verdict["checks"] if item["id"] == "contrast"
        )
        check(
            "la bocciatura porta una misura numerica",
            isinstance(contrast_measure.get("ratio"), float)
            and contrast_measure["ratio"] < verify.CONTRAST_MIN,
            str(contrast_measure),
        )

        print("\ndecisioni: verified e verification, senza auto-certificazione")
        good_log = _by_id(good_report["decisions"])
        bad_log = _by_id(bad_report["decisions"])
        check(
            "font verificato dal contrasto sul master buono",
            good_log["font"]["verified"] is True
            and good_log["font"]["check"] == "contrast",
            str(good_log["font"]["verification"]),
        )
        check(
            "font non verificato quando il contrasto fallisce",
            bad_log["font"]["verified"] is False
            and bad_log["font"]["verification"]["measured"]["ratio"]
            < verify.CONTRAST_MIN,
            str(bad_log["font"]["verification"]["measured"]),
        )
        check(
            "keyframes senza check automatico resta non verificato",
            good_log["keyframes"]["verified"] is False
            and good_log["keyframes"]["verification"]["why"]
            == "falsifier non automatizzabile",
            str(good_log["keyframes"]["verification"]),
        )
        declared = decisions.record(
            id="prova-check",
            question="q",
            chosen="c",
            evidence=["contrasto misurato"],
            falsified_if="f",
            alternatives_note="solo una scelta",
            check="contrast",
        )
        check(
            "record conserva il check dichiarato",
            declared.get("check") == "contrast",
        )
        registry = decisions.Registry()
        registry.add(declared)
        check(
            "il registro dichiara 'check non eseguito'",
            registry.as_list()[0]["verified"] is False
            and registry.as_list()[0]["verification"]["why"] == "check non eseguito",
            str(registry.as_list()[0]["verification"]),
        )
        # La forma resta confrontabile: validate non perde i campi aggiunti.
        item = good_report["decisions"][0]
        check("validate conserva verified/verification", decisions.validate(item) == item)
        forged = decisions.record(
            id="forged",
            question="q",
            chosen="c",
            evidence=["contrasto misurato"],
            falsified_if="f",
            alternatives_note="solo una scelta",
        )
        forged["verified"] = True
        forged["verification"] = {"why": "fidati"}
        normalized = decisions.validate(forged)
        check(
            "un verified:true senza check viene riportato a false",
            normalized["verified"] is False
            and normalized["verification"]["why"] == "falsifier non automatizzabile",
            str(normalized["verification"]),
        )

        summary = {
            "good": [item["id"] for item in good_verdict["checks"] if item["passed"]],
            "bad": failed,
        }
        print("\nriepilogo: " + json.dumps(summary, ensure_ascii=False))

    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
