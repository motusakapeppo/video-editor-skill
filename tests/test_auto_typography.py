"""vx.auto: il brief con testo diventa ASS, e il render lo brucia.

Il percorso ``auto`` produceva ``hook: none`` e ``ass: null``: il piano sapeva
dove mettere il testo, ma nessuno scriveva il file. Qui si prova il ponte vero,
senza rete: brief -> ``textbrief`` -> ``subs.write_hook_ass``/``merge_ass`` ->
``render.render_edl``.

    python tests/test_auto_typography.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vx import auto, fontlib, fonts, render, subs, textbrief  # noqa: E402
from vx.edl_plan import ClipSegment, EDLPlan, SourceRef, Track  # noqa: E402
from vx.ffmpeg import ffmpeg  # noqa: E402

PASS = 0
FAIL = 0
FFMPEG = ffmpeg()
W, H = 540, 960
CLIP_SECONDS = 2.0


def check(label: str, ok: object, detail: object = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:58} {detail}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clip(path: Path) -> Path:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x303030:size={W}x{H}:rate=30:duration={CLIP_SECONDS:g}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def plan(path: Path, duration: float = CLIP_SECONDS) -> EDLPlan:
    body = ClipSegment("v0_0", "src", 0.0, duration, 0.0, duration, 0, "main_talking_head")
    return EDLPlan(
        fps=30.0,
        duration_sec=duration,
        aspect_ratio="9:16",
        resolution=(W, H),
        sources={"src": SourceRef(str(path), sha256(path), duration)},
        video_tracks=(Track(0, (body,), "a_roll"),),
        audio_tracks=(),
    )


def ass_lines(path: Path, prefix: str) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]


def style_font_size(path: Path) -> tuple[str, str]:
    line = ass_lines(path, "Style: ")[0]
    fields = line[len("Style: ") :].split(",")
    return fields[1], fields[2]


def test_parser() -> None:
    print("\n-- textbrief: solo cio' che il brief dichiara")
    brief = textbrief.parse_brief_text(
        "reel tatuaggio, hook: MI HA DETTO: FAI TU, cta: PRENOTA ORA, "
        "font Antonio, 106 px, hook at 0.10s, hook durata 2s, cta at 18.5s"
    )
    check("hook_text letto", brief.hook_text == "MI HA DETTO: FAI TU", brief.hook_text)
    check("hook_at letto", brief.hook_at == 0.10, brief.hook_at)
    check("hook_dur letto", brief.hook_dur == 2.0, brief.hook_dur)
    check("cta_text letto", brief.cta_text == "PRENOTA ORA", brief.cta_text)
    check("cta_at letto", brief.cta_at == 18.5, brief.cta_at)
    check("font letto", brief.font == "Antonio", brief.font)
    check("size letto", brief.size == 106, brief.size)
    check(
        "1080 px non e' un corpo",
        textbrief.parse_brief_text("hook: X, 1080 px").size is None,
        textbrief.parse_brief_text("hook: X, 1080 px").size,
    )
    empty = textbrief.parse_brief_text("montaggio pulito senza scritte")
    check("brief senza testo: asks_text False", empty.asks_text is False, empty.to_dict())


def test_stage(work: Path, source: Path) -> None:
    print("\n-- _run_typography: file onesti, secondi, font")
    p = plan(source)
    section, render_layer = auto._run_typography(
        work,
        p,
        "hook: CIAO A TUTTI, cta: SEGUIMI, font Antonio, 106 px, hook at 0.10s, "
        "hook durata 1.5s, cta at 1.6s",
        {"family": ""},
        None,
    )
    check("applied", section.get("applied") is True, section.get("reason"))
    hook = Path(section["files"]["hook"])
    cta = Path(section["files"]["subs"])
    check("hook.ass esiste col suo nome", hook.is_file() and hook.name == "hook.ass", hook)
    check("subs.ass esiste col suo nome", cta.is_file() and cta.name == "subs.ass", cta)
    hook_events = ass_lines(hook, "Dialogue:")
    check(
        "hook alla battuta giusta (0.10 s)",
        hook_events and hook_events[0].split(",")[1] == subs._ass_time(0.10),
        hook_events[0] if hook_events else "none",
    )
    check("testo hook in maiuscolo", "CIAO A TUTTI" in hook.read_text(encoding="utf-8"))
    family, size = style_font_size(hook)
    check("Fontname = Antonio", family == "Antonio", family)
    check("Fontsize = 106 (dal brief)", size == "106", size)
    cta_events = ass_lines(cta, "Dialogue:")
    check(
        "CTA alla battuta giusta (1.60 s)",
        cta_events and cta_events[0].split(",")[1] == subs._ass_time(1.60),
        cta_events[0] if cta_events else "none",
    )
    check(
        "overlays.ass porta due Dialogue",
        len(ass_lines(Path(render_layer), "Dialogue:")) == 2,
        render_layer,
    )
    check(
        "report dichiara i testi e i secondi",
        [(item["kind"], item["at"]) for item in section["texts"]]
        == [("hook", 0.10), ("cta", 1.60)],
        section["texts"],
    )
    check(
        "font da bundle, nessun ripiego",
        section["font"]["family"] == "Antonio"
        and section["font"]["source"] == "bundled"
        and section["font"]["fallback"] is False,
        section["font"],
    )

    fallback, _ = auto._run_typography(
        work / "fallback", p, "hook: X, font NonEsisteDavvero", {"family": ""}, None
    )
    check(
        "font assente: fallback dichiarato",
        fallback["font"]["fallback"] is True
        and fallback["font"]["family"] == "Antonio"
        and "NonEsisteDavvero" in fallback["font"]["note"],
        fallback["font"],
    )

    store_dir = work / "store"
    store = fontlib.Store(store_dir)
    source_font = fonts.font_file("Montserrat", "Regular")
    store.add_file(source_font, family="Montserrat", licence="commercial")
    previous = os.environ.get("VX_FONT_STORE")
    os.environ["VX_FONT_STORE"] = str(store_dir)
    try:
        from_store, _ = auto._run_typography(
            work / "store_job", p, "hook: X, font Montserrat", {"family": ""}, None
        )
    finally:
        if previous is None:
            os.environ.pop("VX_FONT_STORE", None)
        else:
            os.environ["VX_FONT_STORE"] = previous
    check(
        "font registrato nello store: source store",
        from_store["font"]["source"] == "store"
        and from_store["font"]["family"] == "Montserrat"
        and from_store["font"]["fallback"] is False,
        from_store["font"],
    )
    check(
        "font dello store copiato nella cartella del lavoro",
        Path(from_store["font"].get("fonts_dir") or "").is_dir()
        and any(Path(from_store["font"]["fonts_dir"]).glob("Montserrat*.ttf")),
        from_store["font"].get("fonts_dir"),
    )


def test_no_text(work: Path, source: Path) -> None:
    print("\n-- brief senza testo: nessun ASS, caption intatte")
    p = plan(source)
    captions = work / "captions.ass"
    subs.write_hook_ass(captions, "CAPTION ESISTENTE", out_w=W, out_h=H, start_s=0.2)
    section, render_layer = auto._run_typography(
        work, p, "montaggio pulito senza scritte", {"family": ""}, str(captions)
    )
    check("applied False", section.get("applied") is False, section)
    check("reason dichiarata", "non chiede" in section.get("reason", ""), section.get("reason"))
    check("nessun hook.ass scritto", not (work / "hook.ass").exists())
    check("nessun subs.ass scritto", not (work / "subs.ass").exists())
    check("caption esistenti passate intatte", render_layer == str(captions), render_layer)

    merged, layer = auto._run_typography(
        work, p, "hook: CIAO", {"family": ""}, str(captions)
    )
    text = Path(layer).read_text(encoding="utf-8")
    check("con hook le caption si uniscono, non spariscono", merged["captions"]["merged"] and "CAPTION ESISTENTE" in text and "CIAO" in text, layer)


def test_render(work: Path, source: Path) -> None:
    print("\n-- render: il testo c'e' a 0.15 s e non a 0.05 s")
    p = plan(source)
    hook = subs.write_hook_ass(
        work / "hook.ass", "CIAO", out_w=W, out_h=H, start_s=0.10, duration_s=1.5,
        font="Antonio", size=106,
    )
    plain = work / "plain.mp4"
    burnt = work / "burnt.mp4"
    render.render_edl(p, {"src": source}, plain, workdir=work / "w_plain", crf=28, preset="ultrafast")
    render.render_edl(
        p, {"src": source}, burnt, workdir=work / "w_burnt", crf=28, preset="ultrafast",
        subtitles=str(hook),
    )
    check("senza testo e con testo sono master diversi", sha256(plain) != sha256(burnt), sha256(burnt)[:12])

    frames = {}
    for at in (0.05, 0.15):
        png = work / f"f_{at:.2f}.png"
        subprocess.run(
            [FFMPEG, "-y", "-ss", f"{at:.3f}", "-i", str(burnt), "-frames:v", "1", str(png)],
            check=True,
            capture_output=True,
        )
        frames[at] = png
    try:
        import numpy as np
        from PIL import Image

        before = np.asarray(Image.open(frames[0.05]).convert("RGB"), dtype=np.int16)
        after = np.asarray(Image.open(frames[0.15]).convert("RGB"), dtype=np.int16)
        diff = np.abs(after - before).mean(axis=2)
        ys, xs = np.where(diff > 8)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None
        check("frame 0.15 diverso da 0.05", float(diff.mean()) > 0.1, round(float(diff.mean()), 3))
        check(
            "la differenza e' nel riquadro del testo (in basso)",
            bbox is not None and bbox[1] > H * 0.5,
            bbox,
        )
    except ImportError:  # pragma: no cover - Pillow/numpy are used by the render tests
        check("frame 0.15 diverso da 0.05", sha256(frames[0.05]) != sha256(frames[0.15]), "sha")


def main() -> int:
    print("vx.auto typography")
    with tempfile.TemporaryDirectory(prefix="vx-auto-typo-") as raw:
        work = Path(raw)
        source = clip(work / "clip.mp4")
        test_parser()
        test_stage(work / "stage", source)
        test_no_text(work / "no_text", source)
        test_render(work / "render", source)
    total = PASS + FAIL
    print(f"\n{PASS}/{total} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
