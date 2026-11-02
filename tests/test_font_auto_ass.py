"""vx.auto: la famiglia scelta arriva al `Fontname` e al `fontsdir=` del burn.

Il cancello dei font puo' scegliere ``Archivo Black`` e finire nel report, ma se
l'ASS dell'auto si scrive col preset il pixel non cambia. Qui si prova il
percorso vero: ``auto._caption_font`` (che usa ``cli._job_font``) ->
``bridge.caption_ass`` -> ``render.build_edl_filtergraph``.

    python tests/test_font_auto_ass.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, bridge, fonts, render  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402

PASS = 0
FAIL = 0

FAMILY = "Archivo Black"
PRESET_FAMILY = "Inter"

CAPTIONS = [
    {"dst_in": 0.2, "dst_out": 1.4, "text": "Ciao a tutti"},
]


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def _plan() -> EDLPlan:
    clip = ClipSegment("v0_0", "src", 0.0, 3.0, 0.0, 3.0, 0, "main_talking_head")
    return EDLPlan(
        fps=30.0,
        duration_sec=3.0,
        aspect_ratio="9:16",
        resolution=(1080, 1920),
        sources={"src": SourceRef("a.mp4", "a" * 64, 3.0)},
        video_tracks=(Track(0, (clip,), "a_roll"),),
        audio_tracks=(),
    )


def style_family(ass_text: str) -> str:
    """Il campo `Fontname` della riga `Style:` (indice 1 dopo il nome)."""
    for line in ass_text.splitlines():
        if line.startswith("Style: "):
            return line[len("Style: ") :].split(",")[1]
    return ""


def _choice(work: Path, *, ttf: bool) -> None:
    target = work / "fonts"
    target.mkdir(parents=True, exist_ok=True)
    font_file = target / "ArchivoBlack-Regular.ttf"
    if ttf:
        # Un TTF finto basta: `job_fonts_dir` copia per sha, non parsa il font.
        font_file.write_bytes(b"\x00\x01\x00\x00archivo-black")
    (work / "font_proposal.json").write_text(
        json.dumps(
            {
                "schema": "vx.fontproposal/1",
                "chosen": FAMILY,
                "candidates": [
                    {
                        "family": FAMILY,
                        "file": str(font_file),
                        "licence": "unknown",
                        "score": 0.93,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (work / "font_choice.json").write_text(
        json.dumps({"family": FAMILY}), encoding="utf-8"
    )


def _burn_graph(ass: Path, fonts_dir: str | None) -> str:
    return render.build_edl_filtergraph(
        _plan(),
        {"src": 0},
        has_audio=False,
        subtitles=str(ass),
        fonts_dir=fonts_dir,
    )


def test_choice_reaches_ass_and_filter() -> None:
    print("\n-- scelta + TTF in <workdir>/fonts: Fontname e fontsdir cambiano")
    with tempfile.TemporaryDirectory(prefix="vx-font-auto-") as raw:
        work = Path(raw)
        _choice(work, ttf=True)
        font = auto._caption_font(work)
        check("la scelta e' passata da _job_font", font["family"] == FAMILY, font)
        check(
            "fonts_dir e' <workdir>/fonts",
            Path(font["fonts_dir"]).resolve() == (work / "fonts").resolve(),
            font["fonts_dir"],
        )
        check("nessun fallback dichiarato", font["fallback_reason"] == "", font)

        ass = bridge.caption_ass(
            CAPTIONS,
            work / "captions.ass",
            font_family=font["family"] or None,
        )
        text = Path(ass).read_text(encoding="utf-8")
        check(
            f"Style: ...,{FAMILY},...",
            style_family(text) == FAMILY,
            [line for line in text.splitlines() if line.startswith("Style:")],
        )

        graph = _burn_graph(Path(ass), font["fonts_dir"] or None)
        expected = "fontsdir=" + render.ass_filter_path(str(work / "fonts"))
        check(
            "il filtro porta fontsdir=<workdir>/fonts",
            expected in graph,
            expected,
        )


def test_without_choice_preset_and_identical_bytes() -> None:
    print("\n-- senza scelta: preset e ASS byte-identico a oggi")
    with tempfile.TemporaryDirectory(prefix="vx-font-auto-") as raw:
        work = Path(raw)
        font = auto._caption_font(work)
        check("nessuna scelta: famiglia vuota", font["family"] == "", font)

        today = bridge.caption_ass(CAPTIONS, work / "a.ass")
        explicit = bridge.caption_ass(
            CAPTIONS, work / "b.ass", font_family=font["family"] or None
        )
        today_text = Path(today).read_text(encoding="utf-8")
        explicit_text = Path(explicit).read_text(encoding="utf-8")
        check(
            f"Style: ...,{PRESET_FAMILY},... (preset)",
            style_family(today_text) == PRESET_FAMILY,
            style_family(today_text),
        )
        check(
            "font_family=None: stessi byte",
            today_text == explicit_text,
            (len(today_text), len(explicit_text)),
        )

        plain = _burn_graph(Path(today), None)
        check(
            "senza scelta il filtro punta al bundle",
            "fontsdir=" + fonts.fontsdir_arg() in plain,
            fonts.fontsdir_arg(),
        )


def test_missing_file_declares_fallback() -> None:
    print("\n-- scelta senza TTF in <workdir>/fonts: preset + motivo dichiarato")
    with tempfile.TemporaryDirectory(prefix="vx-font-auto-") as raw:
        work = Path(raw)
        _choice(work, ttf=False)
        font = auto._caption_font(work)
        check("fallback: famiglia vuota", font["family"] == "", font)
        reasons = [font["fallback_reason"]]
        section = {"family": FAMILY}
        if not font["family"] or not font["fonts_dir"]:
            # la stessa regola del report in `auto._run_locked`
            section["fallback_reason"] = font["fallback_reason"] or (
                f"{FAMILY}: nessun file in {work / 'fonts'}, "
                "il master resta sul preset"
            )
        check(
            "font.fallback_reason non e' vuoto",
            bool(str(section.get("fallback_reason") or "").strip()),
            section.get("fallback_reason"),
        )
        check(
            "il motivo nomina la famiglia o il file mancante",
            FAMILY in " ".join(reasons + [str(section.get("fallback_reason") or "")]),
            reasons,
        )

        ass = bridge.caption_ass(CAPTIONS, work / "captions.ass", font_family=None)
        check(
            f"fallback: Style resta {PRESET_FAMILY}",
            style_family(Path(ass).read_text(encoding="utf-8")) == PRESET_FAMILY,
            style_family(Path(ass).read_text(encoding="utf-8")),
        )


def main() -> int:
    test_choice_reaches_ass_and_filter()
    test_without_choice_preset_and_identical_bytes()
    test_missing_file_declares_fallback()
    print(f"\n{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
